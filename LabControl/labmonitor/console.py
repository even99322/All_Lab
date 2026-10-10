"""Hub 控制台（Monitor 的分頁）：透過 NAS 上的控制代理（labcontrol-hub-agent，port 8766）更新 / 重建 / 重啟網站。

以後的更新流程：下載新版 LabControlHub zip → 打開這個分頁 → 選 zip → 按「更新網站」（不用解壓縮）。
每一步即時顯示：停止 → 備份 → 換程式 → 重建 → 啟動 → 確認版本；失敗會自動換回舊版。
"""
from __future__ import annotations

import io
import re
import time
import zipfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from PyQt6 import QtCore, QtWidgets
from PyQt6.QtCore import Qt

from . import style
from .bg import run_bg
from .client import AgentClient, HubError, agent_url_for, bundled_hub, package_version, zip_package

ICON = {"pending": "○", "running": "◐", "ok": "✔", "fail": "✖", "skip": "–"}
ACTIONS = {"update": "更新網站", "full_rebuild": "完全重建", "rebuild": "重建", "restart": "重新啟動網站",
           "stop": "停止", "start": "啟動", "rollback": "回到備份"}


def zip_version(data: bytes) -> Optional[str]:
    """zip 裡 labhub/__init__.py 的版本（找不到 → None）。"""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = [n for n in z.namelist() if n.replace("\\", "/").endswith("labhub/__init__.py")]
            if not names:
                return None
            m = re.search(r'__version__\s*=\s*["\']([^"\']+)', z.read(sorted(names, key=len)[0]).decode("utf-8", "replace"))
            return m.group(1) if m else None
    except (zipfile.BadZipFile, OSError, KeyError):
        return None


class StepPill(QtWidgets.QFrame):
    def __init__(self, title: str) -> None:
        super().__init__()
        h = QtWidgets.QHBoxLayout(self)
        h.setContentsMargins(10, 5, 10, 5)
        self.icon = QtWidgets.QLabel(ICON["pending"])
        self.text = QtWidgets.QLabel(title)
        h.addWidget(self.icon)
        h.addWidget(self.text)
        self.set("pending")

    def set(self, status: str, msg: str = "") -> None:
        t = style.tokens(_dark())
        color = {"ok": t["ok"], "fail": t["err"], "running": t["accent"], "skip": t["faint"]}.get(status, t["muted"])
        self.icon.setText(ICON.get(status, "•"))
        self.icon.setStyleSheet(f"color:{color}; font-weight:700;")
        self.text.setStyleSheet(f"color:{t['text'] if status in ('ok', 'running', 'fail') else t['muted']};"
                                f"{'font-weight:600;' if status == 'running' else ''}")
        self.setStyleSheet(f"StepPill {{ border:1px solid {color if status != 'pending' else t['border']};"
                           f" border-radius:14px; background:{t['accent_soft'] if status == 'running' else 'transparent'}; }}")
        self.setToolTip(msg)


def _dark() -> bool:
    app = QtWidgets.QApplication.instance()
    return bool(app and app.palette().window().color().lightness() < 128)


class ZipDrop(QtWidgets.QFrame):
    """選 zip：按鈕或把檔案拖進來。"""

    picked = QtCore.pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        self.setProperty("role", "card")
        h = QtWidgets.QHBoxLayout(self)
        h.setContentsMargins(12, 10, 12, 10)
        self.label = QtWidgets.QLabel("把新版 LabControlHub_v*.zip 拖到這裡，或按「選擇 zip…」（不用解壓縮）")
        self.label.setProperty("role", "muted")
        self.label.setWordWrap(True)
        h.addWidget(self.label, 1)
        b = QtWidgets.QPushButton("選擇 zip…")
        b.clicked.connect(self._browse)
        h.addWidget(b)

    def _browse(self) -> None:
        p, _ = QtWidgets.QFileDialog.getOpenFileName(self, "選擇 Hub 新版 zip", str(Path.home() / "Downloads"),
                                                     "Zip (*.zip)")
        if p:
            self.picked.emit(p)

    def dragEnterEvent(self, ev) -> None:
        if ev.mimeData().hasUrls() and ev.mimeData().urls()[0].toLocalFile().lower().endswith(".zip"):
            ev.acceptProposedAction()

    def dropEvent(self, ev) -> None:
        self.picked.emit(ev.mimeData().urls()[0].toLocalFile())


class HubConsole(QtWidgets.QWidget):
    """Hub 控制台分頁。agent_url()：目前的控制代理網址；token()：Hub token。"""

    def __init__(self, agent_url: Callable[[], str], token: Callable[[], str], parent=None) -> None:
        super().__init__(parent)
        self.agent_url, self.token = agent_url, token
        self.zip_data: Optional[bytes] = None
        self.zip_name = ""
        self.job_id: Optional[str] = None
        self.status: Dict[str, Any] = {}
        self._after = 0
        self._polling = False
        self.pills: List[StepPill] = []

        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(16, 14, 16, 12)
        v.setSpacing(10)
        head = QtWidgets.QHBoxLayout()
        t = QtWidgets.QLabel("Hub 控制台")
        t.setProperty("role", "h1")
        head.addWidget(t)
        self.sub = QtWidgets.QLabel("—")
        self.sub.setProperty("role", "muted")
        head.addWidget(self.sub, 1)
        v.addLayout(head)

        card = QtWidgets.QFrame()
        card.setProperty("role", "card")
        g = QtWidgets.QGridLayout(card)
        g.setContentsMargins(14, 12, 14, 12)
        g.setHorizontalSpacing(24)
        self.info: Dict[str, QtWidgets.QLabel] = {}
        for i, (k, lab) in enumerate((("state", "網站狀態"), ("hub", "執行中的版本"), ("code", "NAS 上的程式"),
                                      ("agent", "控制代理"))):
            a = QtWidgets.QLabel(lab)
            a.setProperty("role", "muted")
            b = QtWidgets.QLabel("—")
            b.setProperty("role", "big")
            g.addWidget(a, 0, i)
            g.addWidget(b, 1, i)
            self.info[k] = b
        v.addWidget(card)

        self.drop = ZipDrop()
        self.drop.picked.connect(self.load_zip)
        v.addWidget(self.drop)
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(8)
        self.b_update = QtWidgets.QPushButton("⬆ 更新網站")
        style.mark(self.b_update, primary=True)
        self.b_update.setEnabled(False)
        self.b_update.clicked.connect(self.update_site)
        self.b_bundled = QtWidgets.QPushButton("用這個程式內附的 Hub")
        self.b_bundled.setToolTip("不用另外下載：把 Monitor 內附的 labhub 打包送去更新")
        self.b_bundled.clicked.connect(self.use_bundled)
        self.b_bundled.setVisible(bundled_hub() is not None)
        row.addWidget(self.b_update)
        row.addWidget(self.b_bundled)
        row.addSpacing(16)
        self.b_full = QtWidgets.QPushButton("完全重建")
        self.b_full.setToolTip("重新下載 python 基礎映像並重建容器（很久沒更新或映像壞掉時）")
        self.b_restart = QtWidgets.QPushButton("重新啟動網站")
        self.b_start = QtWidgets.QPushButton("啟動")
        self.b_stop = QtWidgets.QPushButton("停止")
        style.mark(self.b_stop, danger=True)
        for b, act in ((self.b_full, "full_rebuild"), (self.b_restart, "restart"), (self.b_start, "start"),
                       (self.b_stop, "stop")):
            b.clicked.connect(lambda _c=False, a=act: self.run_action(a))
            row.addWidget(b)
        row.addStretch(1)
        v.addLayout(row)

        self.steps_box = QtWidgets.QHBoxLayout()
        self.steps_box.setSpacing(6)
        self.job_title = QtWidgets.QLabel("")
        self.job_title.setProperty("role", "h2")
        v.addWidget(self.job_title)
        v.addLayout(self.steps_box)
        split = QtWidgets.QSplitter(Qt.Orientation.Horizontal)
        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(3000)
        self.log.setPlaceholderText("執行紀錄")
        split.addWidget(self.log)
        bk = QtWidgets.QWidget()
        bl = QtWidgets.QVBoxLayout(bk)
        bl.setContentsMargins(0, 0, 0, 0)
        lab = QtWidgets.QLabel("備份（每次更新前自動備份）")
        lab.setProperty("role", "muted")
        bl.addWidget(lab)
        self.backups = QtWidgets.QTreeWidget()
        self.backups.setHeaderLabels(["版本", "時間", "大小"])
        self.backups.setRootIsDecorated(False)
        self.backups.setColumnWidth(0, 80)
        self.backups.setColumnWidth(1, 150)
        bl.addWidget(self.backups, 1)
        self.b_back = QtWidgets.QPushButton("回到這版")
        self.b_back.clicked.connect(self.rollback)
        bl.addWidget(self.b_back)
        split.addWidget(bk)
        split.setSizes([640, 360])
        v.addWidget(split, 1)
        self.msg = QtWidgets.QLabel("")
        self.msg.setWordWrap(True)
        v.addWidget(self.msg)
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(4000)
        self._buttons()

    # ---- 連線 ---------------------------------------------------------------------
    def client(self) -> Optional[AgentClient]:
        u = self.agent_url()
        return AgentClient(u, self.token(), timeout=10) if u else None

    def refresh(self) -> None:
        c = self.client()
        if c is None or self._polling or not self.isVisible() and self.status:
            return
        self._polling = True

        def ok(st):
            self._polling = False
            self.status = st
            self._show_status()
            j = st.get("job")
            if j and not j.get("done") and self.job_id is None:
                self.follow(j["id"])          # 別台電腦開始的工作也看得到

        def err(m):
            self._polling = False
            self.status = {}
            self.sub.setText(f"連不到控制代理 {c.url}")
            self.info["agent"].setText("未連線")
            self.msg.setText(f"⚠ {m}\n第一次使用：請依 deploy/hub/README.md 在 Container Manager 用新的 docker-compose.yml "
                             "重新建立專案（多一個 labcontrol-hub-agent 容器），之後就都在這裡更新。")
            self._buttons()
        run_bg(c.status, ok, err)

    def _show_status(self) -> None:
        st = self.status
        c = st.get("container") or {}
        state = {"running": "執行中", "exited": "已停止", "created": "已建立（未啟動）", "missing": "找不到容器",
                 "restarting": "重新啟動中"}.get(c.get("status"), c.get("status") or "—")
        self.info["state"].setText(state + ("" if st.get("hub_online") or c.get("status") != "running" else "（無回應）"))
        self.info["hub"].setText(f"v{st['hub_version']}" if st.get("hub_version") else "—")
        self.info["code"].setText(f"v{st.get('code_version') or '—'}")
        self.info["agent"].setText(f"v{st.get('agent_version')}")
        self.sub.setText(f"{self.agent_url()}　·　{st.get('hub_dir', '')}")
        cur = self.backups.currentItem()
        cur_name = cur.data(0, Qt.ItemDataRole.UserRole) if cur else None
        self.backups.clear()
        for b in st.get("backups") or []:
            it = QtWidgets.QTreeWidgetItem([f"v{b['version']}", time.strftime("%Y-%m-%d %H:%M", time.localtime(b["time"])),
                                            f"{b['size'] / 1024:.0f} KB"])
            it.setData(0, Qt.ItemDataRole.UserRole, b["name"])
            self.backups.addTopLevelItem(it)
            if b["name"] == cur_name:
                self.backups.setCurrentItem(it)
        if self.msg.text().startswith("⚠"):
            self.msg.setText("")                       # 連線恢復：清掉連不到的訊息
        self._buttons()

    def _buttons(self) -> None:
        running = self.job_id is not None
        ok = bool(self.status) and not running
        self.b_update.setEnabled(ok and self.zip_data is not None)
        self.b_bundled.setEnabled(ok)
        for b in (self.b_full, self.b_restart, self.b_start, self.b_stop, self.b_back):
            b.setEnabled(ok)

    # ---- zip ------------------------------------------------------------------------
    def load_zip(self, path: str) -> None:
        try:
            data = Path(path).read_bytes()
        except OSError as e:
            self.msg.setText(f"❌ 讀不到 {path}：{e}")
            return
        ver = zip_version(data)
        if ver is None:
            self.msg.setText(f"❌ {Path(path).name} 不是 Hub 的 zip（裡面找不到 labhub/__init__.py）")
            return
        self.zip_data, self.zip_name = data, Path(path).name
        self.drop.label.setText(f"📦 {self.zip_name}　·　Hub v{ver}　·　{len(data) / 1024:.0f} KB")
        self.drop.label.setProperty("role", "")
        style.mark(self.drop.label)
        self._buttons()

    def use_bundled(self) -> None:
        pkg = bundled_hub()
        if pkg is None:
            return
        self.zip_data, self.zip_name = zip_package(pkg), f"內附 labhub v{package_version(pkg)}"
        self.drop.label.setText(f"📦 {self.zip_name}")
        self._buttons()

    # ---- 動作 -----------------------------------------------------------------------
    def confirm(self, text: str) -> bool:
        return QtWidgets.QMessageBox.question(self, "Hub 控制台", text) == QtWidgets.QMessageBox.StandardButton.Yes

    def update_site(self) -> None:
        if self.zip_data is None:
            return
        ver = zip_version(self.zip_data) or "?"
        cur = self.status.get("hub_version") or self.status.get("code_version") or "?"
        if not self.confirm(f"用 {self.zip_name} 更新網站？\n目前 v{cur} → v{ver}\n"
                            "（停止 → 備份 → 換程式 → 重建 → 啟動 → 確認版本；失敗會自動換回）"):
            return
        self._submit("update", data=self.zip_data)

    def run_action(self, action: str) -> None:
        if action in ("stop", "full_rebuild") and not self.confirm(
                f"確定要{ACTIONS[action]}？" + ("\n網站停止後，所有電腦都連不到 Hub，直到按「啟動」。" if action == "stop" else
                                            "\n會重新下載基礎映像，需要幾分鐘。")):
            return
        self._submit(action)

    def rollback(self) -> None:
        it = self.backups.currentItem()
        if it is None:
            self.msg.setText("請先在右邊選一個備份")
            return
        if not self.confirm(f"回到備份 {it.text(0)}（{it.text(1)}）？目前版本會先備份。"):
            return
        self._submit("rollback", backup=it.data(0, Qt.ItemDataRole.UserRole))

    def _submit(self, action: str, data: Optional[bytes] = None, backup: str = "") -> None:
        c = self.client()
        if c is None:
            return
        self.job_id = "…"
        self._buttons()
        self.log.clear()
        run_bg(lambda: c.submit(action, data=data, backup=backup),
               lambda j: self.follow(j["id"], j), lambda m: self._fail(m))

    def _fail(self, m: str) -> None:
        self.job_id = None
        self.msg.setText(f"❌ {m}")
        self._buttons()

    def follow(self, jid: str, first: Optional[Dict[str, Any]] = None) -> None:
        self.job_id = jid
        self._after = 0
        self.log.clear()
        self._buttons()
        if first:
            self._show_job(first)
        self._poll_job()

    def _poll_job(self) -> None:
        c, jid = self.client(), self.job_id
        if c is None or not jid:
            return

        def ok(v):
            self._show_job(v)
            if v.get("done"):
                self.job_id = None
                self.refresh()
                self._buttons()
            else:
                QtCore.QTimer.singleShot(50, self._poll_job)

        def err(m):
            # 代理本身在更新後重新啟動（agent.py 有變）→ 稍後再問
            self.msg.setText(f"⚠ 暫時讀不到進度（{m}），重試中…")
            QtCore.QTimer.singleShot(2000, self._poll_job)
        run_bg(lambda: c.job(jid, self._after, wait=15), ok, err)

    def _show_job(self, v: Dict[str, Any]) -> None:
        self.job_title.setText(f"{v.get('title') or ACTIONS.get(v.get('action'), '')}　"
                               f"<span style='font-weight:400'>by {v.get('by', '')}</span>")
        steps = v.get("steps") or []
        if [p.text.text() for p in self.pills] != [s["title"] for s in steps]:
            while self.steps_box.count():
                w = self.steps_box.takeAt(0).widget()
                if w is not None:
                    w.setParent(None)
            self.pills = []
            for k, s in enumerate(steps):
                if k:
                    arrow = QtWidgets.QLabel("→")
                    arrow.setProperty("role", "muted")
                    self.steps_box.addWidget(arrow)
                p = StepPill(s["title"])
                self.pills.append(p)
                self.steps_box.addWidget(p)
            self.steps_box.addStretch(1)
        for p, s in zip(self.pills, steps):
            p.set(s.get("status", "pending"), s.get("msg", ""))
        for ln in v.get("lines") or []:
            self.log.appendPlainText(time.strftime("%H:%M:%S ", time.localtime(ln["time"])) + ln["msg"])
        self._after = v.get("n_lines", self._after)
        if v.get("done"):
            t = style.tokens(_dark())
            self.msg.setText(f"<span style='color:{t['ok'] if v.get('ok') else t['err']}'>"
                             f"{'✅' if v.get('ok') else '❌'} {v.get('result', '')}</span>")


def default_agent_url(cfg: Dict[str, Any], hub_url: Optional[str]) -> str:
    if cfg.get("agent_url"):
        return str(cfg["agent_url"])
    if hub_url:
        return agent_url_for(hub_url)
    urls = cfg.get("hub_urls") or []
    return agent_url_for(urls[0]) if urls else ""


__all__ = ["HubConsole", "default_agent_url", "zip_version", "HubError"]
