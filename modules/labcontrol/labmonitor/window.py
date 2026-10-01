"""Lab Control Monitor 主視窗：節點、儀器（掛載位置 / 歸屬 / 連線）、電腦、Hub 控制台（更新 / 重建 / 重啟網站）。"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from PyQt6 import QtCore, QtGui, QtWidgets
from PyQt6.QtCore import Qt

from . import APP_NAME, __version__, config
from .bg import run_bg
from .client import HubClient, HubError, find
from .instrument_dialog import RemoteInstrumentDialog

STATE = {"idle": "閒置", "preparing": "準備中", "running": "量測中", "paused": "已暫停", "finished": "完成",
         "aborted": "已中斷", "failed": "失敗", "updating": "更新中", "offline": "離線"}
COLOR = {"running": "#1c7ed6", "preparing": "#1c7ed6", "paused": "#f08c00", "failed": "#e03131",
         "aborted": "#e8590c", "updating": "#e67700", "offline": "#868e96", "idle": "#2b8a3e", "finished": "#2b8a3e"}
ROLE = Qt.ItemDataRole.UserRole


def ago(t: Optional[float]) -> str:
    if t is None:
        return "—"
    t = float(t)
    if t < 60:
        return f"{t:.0f} 秒前"
    if t < 3600:
        return f"{t / 60:.0f} 分前"
    if t < 86400:
        return f"{t / 3600:.0f} 小時前"
    return f"{t / 86400:.0f} 天前"


def fmt_level(d: Dict[str, Any]) -> str:
    v, u = d.get("level"), d.get("unit") or ""
    if v is None:
        return ""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return ""
    if u in ("A", "V") and abs(v) < 1:
        return f"{v * 1e3:.6g} m{u}"
    return f"{v:.6g} {u}"


class SettingsDialog(QtWidgets.QDialog):
    def __init__(self, cfg: Dict[str, Any], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Hub 連線設定")
        self.resize(520, 220)
        self.cfg = dict(cfg)
        form = QtWidgets.QFormLayout(self)
        self.urls = QtWidgets.QLineEdit(", ".join(cfg.get("hub_urls") or []))
        self.token = QtWidgets.QLineEdit(cfg.get("token", ""))
        self.token.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        self.agent = QtWidgets.QLineEdit(cfg.get("agent_url", ""))
        self.agent.setPlaceholderText("空白 = Hub 同一台主機的 8766（例如 http://192.168.50.2:8766）")
        form.addRow("Hub 網址（依序嘗試）", self.urls)
        form.addRow("token", self.token)
        form.addRow("控制代理網址", self.agent)
        note = QtWidgets.QLabel("token 是 Hub 的存取碼（docker-compose.yml 的 LABHUB_TOKEN），不是 NAS 帳號密碼。")
        note.setWordWrap(True)
        note.setStyleSheet("color:gray;")
        form.addRow(note)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Save |
                                        QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def result_cfg(self) -> Dict[str, Any]:
        c = dict(self.cfg)
        c["hub_urls"] = [u.strip() for u in self.urls.text().replace(";", ",").split(",") if u.strip()]
        c["token"] = self.token.text().strip()
        c["agent_url"] = self.agent.text().strip()
        return c


class MonitorWindow(QtWidgets.QMainWindow):
    data_ready = QtCore.pyqtSignal(dict)

    def __init__(self, cfg: Optional[Dict[str, Any]] = None, persist: bool = True) -> None:
        super().__init__()
        self.cfg = cfg if cfg is not None else config.load()
        self.persist = persist
        self.client: Optional[HubClient] = None
        self.state: Dict[str, Any] = {}
        self.registry: List[Dict[str, Any]] = []
        self._polling = False
        self._fails = 0
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.resize(1280, 820)
        self._build()
        self.data_ready.connect(self._apply)
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(int(float(self.cfg.get("refresh_s", 2.0)) * 1000))
        self.connect_hub()

    # ---- 版面 ------------------------------------------------------------
    def _build(self) -> None:
        tb = self.addToolBar("主要")
        tb.setMovable(False)
        self.hub_label = QtWidgets.QLabel("  Hub：連線中…  ")
        tb.addWidget(self.hub_label)
        tb.addSeparator()
        tb.addAction("⟳ 重新整理", self.poll)
        tb.addAction("⚙ 連線設定…", self.open_settings)
        tb.addAction("🌐 開啟 Hub 網頁", self.open_web)
        self.tabs = QtWidgets.QTabWidget()
        self.setCentralWidget(self.tabs)
        self.tabs.addTab(self._nodes_tab(), "量測節點")
        self.tabs.addTab(self._inst_tab(), "儀器")
        self.tabs.addTab(self._devices_tab(), "所有電腦")
        from .console import HubConsole, default_agent_url

        self.console = HubConsole(lambda: default_agent_url(self.cfg, self.client.url if self.client else None),
                                  lambda: self.cfg.get("token", ""))
        self.tabs.addTab(self.console, "Hub 控制台")
        self.status = QtWidgets.QLabel()
        self.statusBar().addPermanentWidget(self.status, 1)

    def _tree(self, headers: List[str], widths: List[int]) -> QtWidgets.QTreeWidget:
        t = QtWidgets.QTreeWidget()
        t.setHeaderLabels(headers)
        t.setAlternatingRowColors(True)
        t.setUniformRowHeights(True)
        for i, w in enumerate(widths):
            t.setColumnWidth(i, w)
        return t

    def _nodes_tab(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        self.nodes = self._tree(["節點", "狀態", "電腦", "版本", "量測", "進度", "儀器", "最後回報"],
                                [150, 90, 180, 80, 260, 150, 90, 90])
        self.nodes.setRootIsDecorated(True)
        self.nodes.currentItemChanged.connect(lambda *_: self._node_selected())
        self.nodes.itemDoubleClicked.connect(self._node_double)
        v.addWidget(self.nodes, 3)
        bar = QtWidgets.QHBoxLayout()
        self.nb = {}
        for key, text in (("connect_all", "🔌 全部連線"), ("disconnect_all", "⏏ 全部斷線"), ("scan", "🔍 掃描儀器"),
                          ("pause", "⏸ 暫停"), ("stop", "⏹ 停止量測"), ("control", "🎛 控制儀器…")):
            b = QtWidgets.QPushButton(text)
            b.clicked.connect(lambda _c=False, k=key: self.node_action(k))
            bar.addWidget(b)
            self.nb[key] = b
        bar.addStretch()
        v.addLayout(bar)
        self.node_log = QtWidgets.QPlainTextEdit()
        self.node_log.setReadOnly(True)
        self.node_log.setMaximumHeight(160)
        v.addWidget(self.node_log, 1)
        return w

    def _inst_tab(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        top = QtWidgets.QLabel("每台實體儀器一列（依位址 / 序號辨識），展開可看掛在哪些電腦。網路儀器若有兩台以上電腦都設定了，"
                               "需要「拉取」到一台電腦（群組），其他電腦就不會連它。")
        top.setWordWrap(True)
        top.setStyleSheet("color:gray;")
        v.addWidget(top)
        self.insts = self._tree(["儀器 / 電腦", "型號", "序號", "識別", "狀態", "值", "歸屬"],
                                [260, 150, 120, 230, 150, 110, 150])
        self.insts.itemDoubleClicked.connect(lambda it, _c: self.inst_action("control"))
        v.addWidget(self.insts, 1)
        bar = QtWidgets.QHBoxLayout()
        for key, text in (("connect", "▶ 連線"), ("disconnect", "■ 中斷"), ("control", "🎛 控制…"),
                          ("claim", "⇩ 拉取到…"), ("release", "取消歸屬")):
            b = QtWidgets.QPushButton(text)
            b.clicked.connect(lambda _c=False, k=key: self.inst_action(k))
            bar.addWidget(b)
        bar.addStretch()
        v.addLayout(bar)
        return w

    def _devices_tab(self) -> QtWidgets.QWidget:
        self.devs = self._tree(["狀態", "電腦", "使用者", "角色", "版本", "目前", "儀器數", "IP", "最後回報"],
                               [70, 160, 110, 170, 80, 260, 70, 120, 90])
        self.devs.setRootIsDecorated(False)
        return self.devs

    # ---- 連線 / 輪詢 ----------------------------------------------------------
    def connect_hub(self) -> None:
        urls, token = list(self.cfg.get("hub_urls") or []), self.cfg.get("token", "")
        self.hub_label.setText("  Hub：連線中…  ")

        def ok(c):
            self.client = c
            self._fails = 0
            self.hub_label.setText(f"  Hub：✔ {c.url.split('://')[-1]}  ")
            self.poll()
            self.console.refresh()

        def err(m):
            self.client = None
            self.hub_label.setText("  Hub：✖ 未連線（⚙ 連線設定）  ")
            self.hub_label.setToolTip(m)
            self.status.setText(f"❌ {m}")
        run_bg(lambda: find(urls, token), ok, err)

    def poll(self) -> None:
        if self.client is None:
            if not self._polling:
                self.connect_hub()
            return
        if self._polling:
            return
        self._polling = True
        c = self.client

        def work():
            s = c.state()
            try:
                reg = c.instruments()
            except HubError:
                reg = None           # 舊版 Hub 沒有儀器登錄
            return {"state": s, "registry": reg}

        def ok(d):
            self._polling = False
            self._fails = 0
            self.data_ready.emit(d)

        def err(m):
            self._polling = False
            self._fails += 1
            self.status.setText(f"⚠ 讀取 Hub 失敗（{self._fails}）：{m}")
            if self._fails >= 3:
                self.client = None
        run_bg(work, ok, err)

    # ---- 顯示 ------------------------------------------------------------
    def _apply(self, d: Dict[str, Any]) -> None:
        self.state = d["state"]
        self.registry = d["registry"] or []
        self._fill_nodes()
        self._fill_insts(d["registry"])
        self._fill_devices()
        s = self.state
        on = sum(1 for n in s.get("nodes") or [] if n.get("online"))
        self.status.setText(f"節點 {on}/{len(s.get('nodes') or [])} 上線 · 電腦 "
                            f"{sum(1 for x in s.get('devices') or [] if x.get('online'))} 台 · "
                            f"更新於 {time.strftime('%H:%M:%S')}")

    def _keep(self, tree: QtWidgets.QTreeWidget):
        it = tree.currentItem()
        cur = it.data(0, ROLE) if it else None
        expanded = set()
        for i in range(tree.topLevelItemCount()):
            t = tree.topLevelItem(i)
            if t.isExpanded():
                expanded.add(str(t.data(0, ROLE)))
        return cur, expanded, tree.verticalScrollBar().value()

    def _restore(self, tree, keep) -> None:
        cur, expanded, scroll = keep
        for i in range(tree.topLevelItemCount()):
            t = tree.topLevelItem(i)
            if str(t.data(0, ROLE)) in expanded:
                t.setExpanded(True)
            for it in [t] + [t.child(j) for j in range(t.childCount())]:
                if it.data(0, ROLE) == cur:
                    tree.setCurrentItem(it)
        tree.verticalScrollBar().setValue(scroll)

    def _fill_nodes(self) -> None:
        keep = self._keep(self.nodes)
        self.nodes.clear()
        for n in self.state.get("nodes") or []:
            st = n.get("state") if n.get("online") else "offline"
            run = n.get("run") or {}
            insts = [i for i in n.get("instruments") or [] if not i.get("virtual")]
            conn = sum(1 for i in insts if i.get("connected"))
            prog = ""
            if run:
                tot, idx = run.get("total") or 0, run.get("index") or 0
                prog = f"{idx}/{tot}（{100 * idx / tot:.0f}%）" if tot else f"{idx}"
            it = QtWidgets.QTreeWidgetItem([n.get("name", ""), STATE.get(st, st), f"{n.get('user')}@{n.get('host')}",
                                            str(n.get("version", "")),
                                            (run.get("name", "") + (f"（{STATE.get(run.get('status'), '')}）"
                                                                    if run.get("status") not in (None, "running") else ""))
                                            if run else "", prog, f"{conn}/{len(insts)} 連線", ago(n.get("seen_ago"))])
            it.setData(0, ROLE, ("node", n.get("name")))
            it.setForeground(1, QtGui.QBrush(QtGui.QColor(COLOR.get(st, "#868e96"))))
            f = it.font(0)
            f.setBold(True)
            it.setFont(0, f)
            for i in n.get("instruments") or []:
                ch = QtWidgets.QTreeWidgetItem([f"  {i.get('name')}", "量測中" if i.get("lease") else
                                                ("已連線" if i.get("connected") else "未連線"),
                                                i.get("address") or ("虛擬" if i.get("virtual") else ""),
                                                i.get("driver", ""), i.get("label", ""), fmt_level(i), "", ""])
                ch.setData(0, ROLE, ("inst", n.get("name"), i.get("name")))
                ch.setForeground(1, QtGui.QBrush(QtGui.QColor("#1c7ed6" if i.get("lease") else
                                                              "#2b8a3e" if i.get("connected") else "#868e96")))
                it.addChild(ch)
            self.nodes.addTopLevelItem(it)
        self._restore(self.nodes, keep)
        if self.nodes.currentItem() is None and self.nodes.topLevelItemCount():
            self.nodes.setCurrentItem(self.nodes.topLevelItem(0))      # 預設選第一個節點
        self._node_selected()

    def _fill_insts(self, reg: Optional[List[Dict[str, Any]]]) -> None:
        keep = self._keep(self.insts)
        self.insts.clear()
        if reg is None:
            it = QtWidgets.QTreeWidgetItem(["Hub 版本較舊，沒有儀器登錄：到「Hub」分頁更新 Hub"])
            self.insts.addTopLevelItem(it)
            return
        for r in reg:
            names = [e.get("name") for e in r["entries"] if e.get("name")]
            conn = [e for e in r["entries"] if e.get("connected")]
            state = ("量測中" if any(e.get("lease") for e in r["entries"]) else
                     f"連線於 {', '.join(e.get('node') or e['host'] for e in conn)}" if conn else "未連線")
            owner = r.get("owner") or ("⚠ 共用，未拉取" if r.get("shared") else "—")
            it = QtWidgets.QTreeWidgetItem([names[0] if names else r["key"], r.get("model", ""), r.get("serial", ""),
                                            r["key"], state, "", owner])
            it.setData(0, ROLE, ("reg", r["key"]))
            f = it.font(0)
            f.setBold(True)
            it.setFont(0, f)
            if r.get("shared") and not r.get("owner"):
                it.setForeground(6, QtGui.QBrush(QtGui.QColor("#e67700")))
            for e in r["entries"]:
                where = f"🛰 {e['node']}" if e.get("node") else f"💻 {e['host']}"
                st = ("離線" if not e.get("online") else "量測中" if e.get("lease") else
                      "已連線" if e.get("connected") else ("未連線" if e.get("configured") else "偵測到（未設定）"))
                ch = QtWidgets.QTreeWidgetItem([f"  {where} · {e.get('name') or '—'}", "", "", e.get("address") or "",
                                                st, fmt_level(e), "✔ 歸屬" if r.get("owner") == e["host"] else ""])
                ch.setData(0, ROLE, ("entry", r["key"], e["host"], e.get("node"), e.get("name")))
                ch.setForeground(4, QtGui.QBrush(QtGui.QColor("#1c7ed6" if e.get("lease") else
                                                              "#2b8a3e" if e.get("connected") else "#868e96")))
                it.addChild(ch)
            self.insts.addTopLevelItem(it)
            if r.get("shared"):
                it.setExpanded(True)
        self._restore(self.insts, keep)

    def _fill_devices(self) -> None:
        keep = self._keep(self.devs)
        self.devs.clear()
        for d in self.state.get("devices") or []:
            run = d.get("run") or {}
            st = d.get("state") if d.get("online") else "offline"
            now = STATE.get(st, st or "") + (f" · {run.get('name')} {run.get('index')}/{run.get('total')}" if run else "")
            it = QtWidgets.QTreeWidgetItem(["● 線上" if d.get("online") else "○ 離線", str(d.get("host", "")),
                                            str(d.get("user", "")),
                                            f"🛰 量測節點（{d.get('node')}）" if d.get("role") == "node" else "💻 控制端",
                                            str(d.get("version", "")), now, str(d.get("n_instruments", "")),
                                            str(d.get("ip", "")), ago(d.get("seen_ago"))])
            it.setData(0, ROLE, d.get("id"))
            it.setForeground(0, QtGui.QBrush(QtGui.QColor("#2b8a3e" if d.get("online") else "#868e96")))
            self.devs.addTopLevelItem(it)
        self._restore(self.devs, keep)

    # ---- 節點動作 ----------------------------------------------------------
    def _sel_node(self) -> Optional[str]:
        it = self.nodes.currentItem()
        d = it.data(0, ROLE) if it else None
        return d[1] if d else None

    def _node_selected(self) -> None:
        name = self._sel_node()
        n = next((x for x in self.state.get("nodes") or [] if x.get("name") == name), None)
        online = bool(n and n.get("online"))
        for k, b in self.nb.items():
            b.setEnabled(online)
        if n:
            st = n.get("state")
            self.nb["pause"].setText("▶ 繼續" if st == "paused" else "⏸ 暫停")
            self.nb["pause"].setEnabled(online and st in ("running", "paused"))
            self.nb["stop"].setEnabled(online and st in ("running", "paused", "preparing"))
            it = self.nodes.currentItem()
            d = it.data(0, ROLE) if it else None
            self.nb["control"].setEnabled(online and bool(d) and d[0] == "inst")
            logs = n.get("log") or []
            text = "\n".join(f"{time.strftime('%H:%M:%S', time.localtime(l['time']))} {l['message']}" for l in logs)
            if self.node_log.toPlainText() != text:
                self.node_log.setPlainText(text)
                self.node_log.verticalScrollBar().setValue(self.node_log.verticalScrollBar().maximum())

    def _node_double(self, it, _col) -> None:
        d = it.data(0, ROLE)
        if d and d[0] == "inst":
            RemoteInstrumentDialog(self.client, d[1], d[2], self).show()

    def node_action(self, key: str) -> None:
        node = self._sel_node()
        if not node or self.client is None:
            return
        if key == "control":
            it = self.nodes.currentItem()
            d = it.data(0, ROLE) if it else None
            if d and d[0] == "inst":
                RemoteInstrumentDialog(self.client, d[1], d[2], self).show()
            return
        n = next((x for x in self.state.get("nodes") or [] if x.get("name") == node), {})
        cmd = {"pause": "resume" if n.get("state") == "paused" else "pause"}.get(key, key)
        if cmd == "stop" and QtWidgets.QMessageBox.question(self, "停止量測", f"確定要停止 {node} 的量測？") != \
                QtWidgets.QMessageBox.StandardButton.Yes:
            return
        label = self.nb[key].text()
        self.status.setText(f"{label}（{node}）…")
        c = self.client

        def ok(res):
            if isinstance(res, dict) and cmd in ("connect_all", "disconnect_all"):
                bad = {k: v for k, v in res.items() if v}
                good = len(res) - len(bad)
                msg = f"{label}：{good}/{len(res)} 台成功"
                if bad:
                    self._notice(label, msg + "\n\n" + "\n".join(f"{k}：{v}" for k, v in bad.items()))
                self.status.setText(msg)
            elif cmd == "scan":
                self.status.setText(f"掃描完成：{node} 有 {len(res or [])} 個 VISA 資源（見「儀器」分頁）")
            else:
                self.status.setText(f"✔ {label}（{node}）")
            self.poll()
        run_bg(lambda: c.command(node, cmd, {}, wait=90 if cmd == "scan" else 45), ok,
               lambda m: QtWidgets.QMessageBox.warning(self, label, m))

    # ---- 儀器動作 ----------------------------------------------------------
    def inst_action(self, key: str) -> None:
        it = self.insts.currentItem()
        d = it.data(0, ROLE) if it else None
        if not d or self.client is None:
            return
        rkey = d[1]
        reg = next((r for r in self.registry if r["key"] == rkey), None)
        if reg is None:
            return
        c = self.client
        entry = None
        if d[0] == "entry":
            entry = next((e for e in reg["entries"] if e["host"] == d[2]), None)
        elif key in ("connect", "disconnect", "control"):
            cands = [e for e in reg["entries"] if e.get("node") and e.get("online") and e.get("name")]
            own = [e for e in cands if e["host"] == reg.get("owner")]
            entry = (own or [e for e in cands if e.get("connected")] or cands or [None])[0]
        if key in ("connect", "disconnect", "control"):
            if entry is None or not entry.get("node") or not entry.get("name"):
                QtWidgets.QMessageBox.information(self, "儀器", "這台儀器目前沒有在任何線上的量測節點設定"
                                                              "（控制端電腦上的儀器要在那台電腦開「🛰 量測節點」才能遠端控制）")
                return
            if key == "control":
                RemoteInstrumentDialog(c, entry["node"], entry["name"], self).show()
                return
            args = {"names": [entry["name"]]}

            def ok(res):
                bad = {k: v for k, v in (res or {}).items() if v}
                if bad:
                    QtWidgets.QMessageBox.warning(self, "儀器", "\n".join(f"{k}：{v}" for k, v in bad.items()))
                self.poll()
            run_bg(lambda: c.command(entry["node"], key, args), ok, lambda m: QtWidgets.QMessageBox.warning(self, "儀器", m))
            return
        if key == "claim":
            hosts = [e for e in reg["entries"] if e.get("online")]
            if not hosts:
                QtWidgets.QMessageBox.information(self, "拉取", "沒有線上的電腦有這台儀器")
                return
            labels = [f"{e.get('node') or e['host']}（{e['host']}）" for e in hosts]
            pre = labels.index(next(l for l, e in zip(labels, hosts) if e["host"] == d[2])) if d[0] == "entry" else 0
            choice, ok_ = QtWidgets.QInputDialog.getItem(self, "拉取到哪台電腦", f"{rkey}\n拉取後，其他電腦上連著的會被中斷：",
                                                         labels, pre, False)
            if not ok_:
                return
            host = hosts[labels.index(choice)]["host"]
            run_bg(lambda: c.claim(rkey, host),
                   lambda r: (self.status.setText(f"✔ {rkey} 已拉到 {host}" +
                                                  (f"（{', '.join(r.get('released') or [])} 已中斷）" if r.get("released") else "")),
                              self.poll()),
                   lambda m: QtWidgets.QMessageBox.warning(self, "拉取", m))
        elif key == "release":
            run_bg(lambda: c.release(rkey), lambda r: self.poll(), lambda m: QtWidgets.QMessageBox.warning(self, "取消歸屬", m))

    def _notice(self, title: str, text: str) -> None:
        """不擋住畫面的提示（部分儀器失敗時列出原因）。"""
        box = QtWidgets.QMessageBox(QtWidgets.QMessageBox.Icon.Information, title, text,
                                    QtWidgets.QMessageBox.StandardButton.Ok, self)
        box.setModal(False)
        box.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        box.show()
        self.last_notice = box

    # ---- 其他 ------------------------------------------------------------
    def open_settings(self) -> None:
        dlg = SettingsDialog(self.cfg, self)
        if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            self.cfg = dlg.result_cfg()
            if self.persist:
                config.save(self.cfg)
            self.client = None
            self.connect_hub()

    def open_web(self) -> None:
        import urllib.parse

        if self.client is None:
            return
        tok = self.cfg.get("token", "")
        QtGui.QDesktopServices.openUrl(QtCore.QUrl(self.client.url + "/" + (f"?token={urllib.parse.quote(tok)}"
                                                                             if tok else "")))
