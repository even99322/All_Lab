"""即時監控：電源群組卡片（與實驗室 Yokogawa 網頁面板相同的操作方式）。

  * 拖曳成員到別的群組卡片 = 合併（跟隨第一台 master）；拖到兩張卡片之間的空隙 = 拆出來自己一組。
  * 合併時目前值不同 → 詢問「覆寫（以 master 值）」或「平均」，然後整組斜坡過去。
  * 左側 M1 / M2 / M3 記憶：點一下 = 以 0.05 mA/s 斜坡到記憶值（沒存過則記住目前值）；右鍵 = 記住 / 清除。
  * 右側 ＋ / −（只有剛好兩台時）：一次只動一台一個最小步進（+ 動較低的、− 動較高的）。
  * 跟隨者的數值一直顯示。群組存在 LAB/live_groups.json（每台電腦 / 節點分開）。
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from PyQt6 import QtCore, QtGui, QtWidgets
from PyQt6.QtCore import Qt, pyqtSignal

from ....core import live_groups as lg
from ....paths import lab_path
from ....settings import setting
from .. import theme
from ..worker import run_bg

MIME = "application/x-labcontrol-source"


def fmt_level(v: Any, unit: str) -> str:
    if v is None:
        return "—"
    try:
        v = float(v)
    except (TypeError, ValueError):
        return str(v)
    if unit == "A":
        return f"{v * 1e3:.4f} mA"
    return f"{v:.5g} {unit}"


def disp_scale(unit: str) -> float:
    """顯示單位換算（電流用 mA）。"""
    return 1e-3 if unit == "A" else 1.0


def disp_unit(unit: str) -> str:
    return "mA" if unit == "A" else unit


class MemberChip(QtWidgets.QFrame):
    """群組裡的一台電源：可拖曳。"""

    def __init__(self, ref: str, info: Dict[str, Any], master: bool, parent=None) -> None:
        super().__init__(parent)
        self.ref = ref
        self.setObjectName("chip")
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        h = QtWidgets.QHBoxLayout(self)
        h.setContentsMargins(8, 3, 8, 3)
        h.setSpacing(8)
        grip = QtWidgets.QLabel("⠿")
        grip.setProperty("role", "muted")
        h.addWidget(grip)
        self.name = QtWidgets.QLabel(f"<b>{info.get('label') or ref.split('.')[0]}</b>")
        self.name.setToolTip(ref + ("\n master：群組跟隨這台" if master else "\n 跟隨 master"))
        h.addWidget(self.name)
        tag = QtWidgets.QLabel("master" if master else "跟隨")
        tag.setStyleSheet(f"color:{theme.c('accent' if master else 'muted')}; font-size:11px;")
        h.addWidget(tag)
        h.addStretch(1)
        self.value = QtWidgets.QLabel("—")
        f = self.value.font()
        f.setFamily("Consolas")
        f.setPointSizeF(f.pointSizeF() + 1.5)
        f.setBold(master)
        self.value.setFont(f)
        h.addWidget(self.value)
        self.dot = QtWidgets.QLabel("●")
        self.dot.setToolTip("輸出")
        h.addWidget(self.dot)
        self.setStyleSheet(f"#chip {{ background:transparent; border:1px solid {theme.c('border')}; border-radius:6px; }}")
        self._press: Optional[QtCore.QPoint] = None

    def show_value(self, v: Any, unit: str, output: Any, err: str = "") -> None:
        self.value.setText("錯誤" if err else fmt_level(v, unit))
        self.value.setToolTip(err)
        self.dot.setStyleSheet(f"color:{theme.c('ok') if output else theme.c('disabled')};")
        self.dot.setToolTip("輸出 ON" if output else ("輸出 OFF" if output is not None else "輸出狀態未知"))

    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.MouseButton.LeftButton:
            self._press = ev.position().toPoint()
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev) -> None:
        if self._press is None or (ev.position().toPoint() - self._press).manhattanLength() < 8:
            return
        self._press = None
        md = QtCore.QMimeData()
        md.setData(MIME, self.ref.encode("utf-8"))
        drag = QtGui.QDrag(self)
        drag.setMimeData(md)
        drag.setPixmap(self.grab())
        drag.exec(Qt.DropAction.MoveAction)


class DropGap(QtWidgets.QFrame):
    """卡片之間的空隙：拖成員放在這裡 = 拆出來自己一組。"""

    dropped = pyqtSignal(str, int)

    def __init__(self, index: int, parent=None) -> None:
        super().__init__(parent)
        self.index = index
        self.setAcceptDrops(True)
        self.setFixedHeight(10)
        self._hl(False)

    def _hl(self, on: bool) -> None:
        self.setFixedHeight(28 if on else 10)
        self.setStyleSheet(f"border:1px dashed {theme.c('accent')}; border-radius:6px;" if on else "border:none;")
        self.setToolTip("放開：拆成新群組")

    def dragEnterEvent(self, ev) -> None:
        if ev.mimeData().hasFormat(MIME):
            self._hl(True)
            ev.acceptProposedAction()

    def dragLeaveEvent(self, ev) -> None:
        self._hl(False)

    def dropEvent(self, ev) -> None:
        self._hl(False)
        self.dropped.emit(bytes(ev.mimeData().data(MIME)).decode("utf-8"), self.index)
        ev.acceptProposedAction()


class GroupCard(QtWidgets.QFrame):
    """一個群組：記憶（左）、成員與控制（中）、微調（右，兩台時）。"""

    dropped = pyqtSignal(str, int)              # ref, 群組 index（合併）

    def __init__(self, panel: "DCGroupsPanel", g: Dict[str, Any]) -> None:
        super().__init__()
        self.panel, self.g = panel, g
        self.index = g["index"]
        self.members: List[str] = list(g["members"])
        self.unit = panel.sources.get(self.members[0], {}).get("unit", "A")
        self.job: Optional[str] = None
        self.setProperty("role", "card")
        self.setAcceptDrops(True)
        root = QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(10)

        mem = QtWidgets.QVBoxLayout()
        mem.setSpacing(4)
        self.mem_btns: List[QtWidgets.QPushButton] = []
        for i in range(lg.N_MEMORY):
            b = QtWidgets.QPushButton(f"M{i + 1}")
            b.setFixedWidth(86)
            b.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            b.clicked.connect(lambda _c=False, i=i: self.recall(i))
            b.customContextMenuRequested.connect(lambda _p, i=i, b=b: self._mem_menu(i, b))
            mem.addWidget(b)
            self.mem_btns.append(b)
        mem.addStretch(1)
        root.addLayout(mem)

        mid = QtWidgets.QVBoxLayout()
        mid.setSpacing(5)
        head = QtWidgets.QHBoxLayout()
        self.title = QtWidgets.QLabel(g["name"] or "群組")
        self.title.setProperty("role", "h2")
        self.title.setToolTip("雙擊改名")
        self.title.mouseDoubleClickEvent = lambda _e: self.rename()
        head.addWidget(self.title)
        n = QtWidgets.QLabel(f"{len(self.members)} 台" + ("（拖曳成員可合併 / 拆開）" if len(self.members) == 1 else ""))
        n.setProperty("role", "muted")
        head.addWidget(n)
        head.addStretch(1)
        self.state = QtWidgets.QLabel("")
        self.state.setProperty("role", "muted")
        head.addWidget(self.state)
        self.b_out = QtWidgets.QPushButton("輸出 OFF")
        self.b_out.setCheckable(True)
        self.b_out.setToolTip("整組輸出開 / 關")
        self.b_out.clicked.connect(self.toggle_output)
        head.addWidget(self.b_out)
        mid.addLayout(head)
        self.chips: Dict[str, MemberChip] = {}
        for i, ref in enumerate(self.members):
            c = MemberChip(ref, panel.sources.get(ref, {}), i == 0)
            self.chips[ref] = c
            mid.addWidget(c)
        ctl = QtWidgets.QHBoxLayout()
        ctl.setSpacing(6)
        du = disp_unit(self.unit)
        self.target = QtWidgets.QLineEdit()
        self.target.setPlaceholderText(f"目標（{du}）")
        self.target.setMaximumWidth(130)
        self.target.returnPressed.connect(self.go)
        self.rate = QtWidgets.QLineEdit()
        self.rate.setPlaceholderText(f"速率 {du}/s")
        self.rate.setToolTip("斜坡速率；空白 = instruments.yaml 的 ramp_rate")
        self.rate.setMaximumWidth(110)
        self.b_go = QtWidgets.QPushButton("▶ 前往")
        self.b_go.setProperty("primary", True)
        self.b_go.clicked.connect(self.go)
        self.b_stop = QtWidgets.QPushButton("■ 停止")
        self.b_stop.setProperty("danger", True)
        self.b_stop.clicked.connect(self.stop)
        for w in (self.target, self.rate, self.b_go, self.b_stop):
            ctl.addWidget(w)
        ctl.addStretch(1)
        mid.addLayout(ctl)
        root.addLayout(mid, 1)

        self.fine: List[QtWidgets.QPushButton] = []
        if len(self.members) == 2:
            fine = QtWidgets.QVBoxLayout()
            fine.setSpacing(4)
            for sign, text in ((+1, "＋"), (-1, "－")):
                b = QtWidgets.QPushButton(text)
                b.setFixedSize(44, 40)
                b.setToolTip("微調：一次只動一台一個最小步進（" + ("較低的那台升一步" if sign > 0 else "較高的那台降一步") + "）")
                b.setAutoRepeat(True)
                b.setAutoRepeatDelay(500)
                b.setAutoRepeatInterval(250)
                b.clicked.connect(lambda _c=False, s=sign: self.panel.fine_step(self, s))
                fine.addWidget(b)
                self.fine.append(b)
            fine.addStretch(1)
            root.addLayout(fine)
        self._update_memory()

    # ---- 顯示 ---------------------------------------------------------------------
    def _update_memory(self) -> None:
        for i, b in enumerate(self.mem_btns):
            v = self.g["memory"][i]
            b.setText(f"M{i + 1}" if v is None else f"M{i + 1}\n{float(v) / disp_scale(self.unit):.4f}")
            b.setToolTip("點一下：斜坡到記憶值（0.05 mA/s）；右鍵：記住目前值 / 清除" if v is not None else
                         "點一下：記住 master 目前值；右鍵：記住 / 清除")
            b.setMinimumHeight(40 if v is not None else 28)

    def update_values(self, levels: Dict[str, Any], outputs: Dict[str, Any], errors: Dict[str, str]) -> None:
        for ref, c in self.chips.items():
            c.show_value(levels.get(ref), self.unit, outputs.get(ref), errors.get(ref, ""))
        outs = [outputs.get(r) for r in self.members]
        on = bool(outs) and all(bool(o) for o in outs)
        self.b_out.blockSignals(True)
        self.b_out.setChecked(on)
        self.b_out.setText("輸出 ON" if on else ("輸出 部分" if any(outs) else "輸出 OFF"))
        self.b_out.blockSignals(False)

    def set_job(self, job: Optional[Dict[str, Any]]) -> None:
        if job is None or job.get("done"):
            if self.job and job is not None and job.get("error"):
                self.panel.message.emit(f"❌ {self.g['name']} 斜坡失敗：{job['error']}")
            self.job = None
            self.state.setText("")
        else:
            self.state.setText(f"斜坡中 → {fmt_level(job.get('target'), self.unit)}")

    def set_enabled(self, on: bool) -> None:
        for w in [*self.mem_btns, self.b_out, self.target, self.rate, self.b_go, *self.fine]:
            w.setEnabled(on)
        self.b_stop.setEnabled(True)
        self.setAcceptDrops(on)

    # ---- 動作 ---------------------------------------------------------------------
    def master_level(self) -> Optional[float]:
        v = self.panel.levels.get(self.members[0])
        return None if v is None else float(v)

    def _mem_menu(self, i: int, b: QtWidgets.QPushButton) -> None:
        m = QtWidgets.QMenu(self)
        m.addAction(f"記住目前值到 M{i + 1}", lambda: self.store(i))
        a = m.addAction(f"清除 M{i + 1}", lambda: self.panel.set_memory(self.index, i, None))
        a.setEnabled(self.g["memory"][i] is not None)
        m.exec(QtGui.QCursor.pos())

    def store(self, i: int) -> None:
        v = self.master_level()
        if v is None:
            self.panel.message.emit("讀不到目前值，稍後再試")
            return
        self.panel.set_memory(self.index, i, v)

    def recall(self, i: int) -> None:
        v = self.g["memory"][i]
        if v is None:
            self.store(i)
            return
        rate = float(setting("live.memory_ramp_mA_s", 0.05)) * 1e-3 if self.unit == "A" else None
        self.panel.ramp(self, float(v), rate)

    def go(self) -> None:
        from ....core.units import parse_quantity

        du = disp_unit(self.unit)
        try:
            t = parse_quantity(self.target.text(), du) if self.target.text().strip() else None
            if t is None:
                return
            r = parse_quantity(self.rate.text(), f"{du}/s") if self.rate.text().strip() else None
        except Exception as e:  # noqa: BLE001
            self.panel.message.emit(f"無法解析：{e}")
            return
        self.panel.ramp(self, t, r)

    def stop(self) -> None:
        self.panel.stop(self)

    def toggle_output(self, on: bool) -> None:
        self.panel.output(self, bool(on))

    def rename(self) -> None:
        name, ok = QtWidgets.QInputDialog.getText(self, "群組名稱", "名稱：", text=self.g["name"])
        if ok and name.strip():
            self.panel.rename(self.index, name.strip())

    # ---- 拖放（合併）------------------------------------------------------------------
    def dragEnterEvent(self, ev) -> None:
        if ev.mimeData().hasFormat(MIME) and bytes(ev.mimeData().data(MIME)).decode() not in self.members:
            self.setStyleSheet(f"QFrame[role=\"card\"] {{ border:1px solid {theme.c('accent')}; }}")
            ev.acceptProposedAction()

    def dragLeaveEvent(self, ev) -> None:
        self.setStyleSheet("")

    def dropEvent(self, ev) -> None:
        self.setStyleSheet("")
        self.dropped.emit(bytes(ev.mimeData().data(MIME)).decode("utf-8"), self.index)
        ev.acceptProposedAction()


class DCGroupsPanel(QtWidgets.QWidget):
    """所有電源群組。backend：LocalBackend / RemoteBackend（本機或量測節點）。"""

    message = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.backend = None
        self.key = ""
        self.store = lg.GroupStore(lab_path("live_groups.json"))
        self.sources: Dict[str, Dict[str, Any]] = {}
        self.groups: List[Dict[str, Any]] = []
        self.levels: Dict[str, Any] = {}
        self.outputs: Dict[str, Any] = {}
        self.errors: Dict[str, str] = {}
        self.jobs: Dict[int, str] = {}          # 群組 index → 斜坡工作 id
        self.cards: List[GroupCard] = []
        self.busy = False
        self._polling = False
        self._gen = 0
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        self.scroll = QtWidgets.QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        v.addWidget(self.scroll, 1)
        self.body = QtWidgets.QWidget()
        self.lay = QtWidgets.QVBoxLayout(self.body)
        self.lay.setContentsMargins(2, 2, 6, 2)
        self.lay.setSpacing(0)
        self.scroll.setWidget(self.body)
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(int(float(setting("live.poll_s", 1.0)) * 1000))

    # ---- 資料 ---------------------------------------------------------------------
    def set_backend(self, backend, key: str) -> None:
        self.backend, self.key = backend, key
        self.sources, self.levels, self.outputs, self.jobs = {}, {}, {}, {}
        self.groups = []
        self._gen += 1
        self.render()
        self.reload()

    def reload(self) -> None:
        be, gen = self.backend, self._gen
        if be is None:
            return

        def ok(info):
            if gen != self._gen:
                return
            src = info.get("sources") or []
            self.sources = {s["ref"]: s for s in src}
            for s in src:
                self.levels.setdefault(s["ref"], s.get("level"))
                self.outputs.setdefault(s["ref"], s.get("output"))
            self.groups = lg.reconcile(self.store.load(self.key), src, info.get("groups") or [])
            for j in info.get("ramps") or []:
                i = self._group_of(j["refs"][0]) if j.get("refs") else None
                if i is not None:
                    self.jobs[i] = j["id"]
            self.render()
            self.poll()

        run_bg(be.sources, ok, lambda m: self.message.emit(f"讀取電源失敗：{m}"))

    def _group_of(self, ref: str) -> Optional[int]:
        return lg.find(self.groups, ref)

    def save(self) -> None:
        if self.key and self.groups:
            try:
                self.store.save(self.key, self.groups)
            except OSError as e:
                self.message.emit(f"無法儲存群組：{e}")

    # ---- 畫面 ---------------------------------------------------------------------
    def render(self) -> None:
        while self.lay.count():
            w = self.lay.takeAt(0).widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self.cards = []
        vis = lg.visible(self.groups, list(self.sources))
        if not vis:
            lab = QtWidgets.QLabel("沒有已連線的電源。\n按上方「全部連線」，或到儀器伺服器連線。"
                                   if self.backend is not None else "")
            lab.setProperty("role", "muted")
            lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lab.setMinimumHeight(120)
            self.lay.addWidget(lab)
            self.lay.addStretch(1)
            return
        for k, g in enumerate(vis):
            gap = DropGap(g["index"])
            gap.dropped.connect(self.on_split)
            self.lay.addWidget(gap)
            card = GroupCard(self, g)
            card.dropped.connect(self.on_merge)
            card.set_enabled(not self.busy)
            card.update_values(self.levels, self.outputs, self.errors)
            self.lay.addWidget(card)
            self.cards.append(card)
        gap = DropGap(len(self.groups))
        gap.dropped.connect(self.on_split)
        self.lay.addWidget(gap)
        hint = QtWidgets.QLabel("拖曳成員（⠿）到別張卡片 = 合併；拖到卡片之間的空隙 = 拆開")
        hint.setProperty("role", "muted")
        hint.setStyleSheet("font-size:11px;")
        self.lay.addWidget(hint)
        self.lay.addStretch(1)

    def set_busy(self, busy: bool) -> None:
        self.busy = busy
        for c in self.cards:
            c.set_enabled(not busy)

    # ---- 輪詢 ---------------------------------------------------------------------
    def poll(self) -> None:
        be, gen = self.backend, self._gen
        if be is None or self._polling or not self.sources or not self.isVisible():
            return
        refs = list(self.sources)
        outs = [r[: -len(".level")] + ".output" for r in refs if r.endswith(".level")
                and self.sources[r].get("output") is not None]
        jobs = dict(self.jobs)
        self._polling = True

        def work():
            vals = be.levels(refs + outs)
            st = {i: be.ramp_status(j) for i, j in jobs.items()}
            return vals, st

        def ok(res):
            self._polling = False
            if gen != self._gen:
                return
            vals, st = res
            for r in refs:
                v, e = vals.get(r, (None, ""))
                self.errors[r] = e or ""
                if not e:
                    self.levels[r] = v
                o = r[: -len(".level")] + ".output"
                if o in vals and not vals[o][1]:
                    self.outputs[r] = vals[o][0]
            for i, s in st.items():
                if s.get("done"):
                    self.jobs.pop(i, None)
                    if s.get("error"):
                        self.message.emit(f"❌ 斜坡失敗：{s['error']}")
            for c in self.cards:
                c.update_values(self.levels, self.outputs, self.errors)
                c.set_job(st.get(c.index) if c.index in self.jobs else None)

        def fail(m):
            self._polling = False
        run_bg(work, ok, fail)

    # ---- 操作 ---------------------------------------------------------------------
    def _run(self, fn: Callable[[], Any], ok: Optional[Callable[[Any], None]] = None, what: str = "") -> None:
        def done(r):
            if ok:
                ok(r)
            self.poll()
        run_bg(fn, done, lambda m: self.message.emit(f"❌ {what}失敗：{m}"))

    def ramp(self, card: GroupCard, target: float, rate: Optional[float]) -> None:
        if self.busy:
            return
        be, refs, idx = self.backend, list(card.members), card.index

        def ok(jid):
            self.jobs[idx] = jid
            card.job = jid
            card.set_job({"target": target, "done": False})
            self.message.emit(f"{card.g['name']} → {fmt_level(target, card.unit)}")
        self._run(lambda: be.group_ramp(refs, target, rate), ok, "斜坡")

    def stop(self, card: GroupCard) -> None:
        jid = self.jobs.get(card.index)
        be, refs = self.backend, set(card.members)

        def work():
            if jid:
                be.group_stop(jid)
                return
            for j in be.sources().get("ramps") or []:        # 別台電腦開始的斜坡也停得掉
                if refs & set(j.get("refs") or []):
                    be.group_stop(j["id"])
        self._run(work, lambda _r: self.message.emit(f"■ {card.g['name']} 已停止"), "停止")

    def fine_step(self, card: GroupCard, direction: int) -> None:
        if self.busy or card.index in self.jobs:
            return
        be, refs = self.backend, list(card.members)

        def ok(lv):
            for r, v in (lv or {}).items():
                self.levels[r] = v
            card.update_values(self.levels, self.outputs, self.errors)
        self._run(lambda: be.fine_step(refs, direction), ok, "微調")

    def output(self, card: GroupCard, on: bool) -> None:
        be, refs = self.backend, list(card.members)

        def ok(res):
            for r, v in (res or {}).items():
                self.outputs[r] = v
            card.update_values(self.levels, self.outputs, self.errors)
        self._run(lambda: be.output(refs, on), ok, "輸出切換")

    def set_memory(self, index: int, slot: int, value: Optional[float]) -> None:
        self.groups[index]["memory"][slot] = value
        self.save()
        for c in self.cards:
            if c.index == index:
                c.g["memory"] = list(self.groups[index]["memory"])
                c._update_memory()

    def rename(self, index: int, name: str) -> None:
        self.groups[index]["name"] = name
        self.save()
        self.render()

    def ask_merge(self, names: List[str], vals: List[str]) -> Optional[str]:
        """成員目前值不同：回傳 'override' / 'average' / None（取消）。"""
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle("合併群組")
        box.setText("成員目前的值不同：\n" + "\n".join(f"  {n}：{v}" for n, v in zip(names, vals)) +
                    "\n\n要怎麼同步？（會以斜坡慢慢移動）")
        b1 = box.addButton("覆寫（以 master 值）", QtWidgets.QMessageBox.ButtonRole.AcceptRole)
        b2 = box.addButton("平均", QtWidgets.QMessageBox.ButtonRole.AcceptRole)
        box.addButton("取消", QtWidgets.QMessageBox.ButtonRole.RejectRole)
        box.exec()
        return {b1: "override", b2: "average"}.get(box.clickedButton())

    def on_merge(self, ref: str, index: int) -> None:
        if self.busy or index >= len(self.groups):
            return
        new = lg.merge(self.groups, ref, index)
        tgt = next(g for g in new if ref in g["members"])
        members = [m for m in tgt["members"] if m in self.sources]
        res = [float(self.sources[m].get("resolution") or 1e-6) for m in members]
        target = None
        if lg.differs(self.levels, members, max(res) / 2):
            unit = self.sources[members[0]].get("unit", "A")
            mode = self.ask_merge([self.sources[m].get("label") or m for m in members],
                                  [fmt_level(self.levels.get(m), unit) for m in members])
            if mode is None:
                return
            target = lg.merge_target(self.levels, members, mode)
        self.groups = new
        self.jobs = {}
        self.save()
        self.render()
        if target is not None:
            card = next(c for c in self.cards if ref in c.members)
            self.ramp(card, target, None)

    def on_split(self, ref: str, at: int) -> None:
        if self.busy:
            return
        label = self.sources.get(ref, {}).get("label") or ref.split(".")[0]
        src = lg.find(self.groups, ref)
        if src is not None and len(self.groups[src]["members"]) == 1:
            # 只有自己：換位置
            g = self.groups.pop(src)
            self.groups.insert(at - (1 if at > src else 0), g)
        else:
            self.groups = lg.split(self.groups, ref, label, at)
        self.jobs = {}
        self.save()
        self.render()
