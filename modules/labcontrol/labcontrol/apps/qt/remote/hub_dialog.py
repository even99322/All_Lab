"""「設定 ▾ → Hub 連線設定…」：Hub 網址與 token，測試後寫回 LAB/settings.yaml。"""
from __future__ import annotations

from PyQt6 import QtWidgets

from ....remote.hub import Hub, HubError, hub_token, hub_urls
from ....settings import settings, setting
from .. import theme
from ..worker import run_bg


class HubSettingsDialog(QtWidgets.QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Lab Control Hub 連線設定")
        self.resize(520, 260)
        lay = QtWidgets.QVBoxLayout(self)
        note = QtWidgets.QLabel(
            "Hub 是 NAS 上的中繼網站（見 docs/REMOTE.md）。網址可以寫多個，依序嘗試（例如內網、VPN）；"
            "沒寫埠號時用 8765。token 是 Hub 第一次啟動時產生的實驗室共用碼，不是 NAS 帳號密碼。")
        note.setWordWrap(True)
        note.setStyleSheet("color:palette(placeholder-text);")
        lay.addWidget(note)
        form = QtWidgets.QFormLayout()
        self.urls = QtWidgets.QLineEdit(", ".join(str(u) for u in (setting("remote.hub_urls", []) or [])))
        self.urls.setPlaceholderText("192.168.50.2, 100.114.33.20")
        self.token = QtWidgets.QLineEdit(hub_token())
        self.token.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        show = QtWidgets.QCheckBox("顯示")
        show.toggled.connect(lambda on: self.token.setEchoMode(
            QtWidgets.QLineEdit.EchoMode.Normal if on else QtWidgets.QLineEdit.EchoMode.Password))
        tr = QtWidgets.QHBoxLayout()
        tr.addWidget(self.token, 1)
        tr.addWidget(show)
        form.addRow("Hub 網址", self.urls)
        form.addRow("token", tr)
        lay.addLayout(form)
        self.msg = QtWidgets.QLabel()
        self.msg.setWordWrap(True)
        lay.addWidget(self.msg)
        lay.addStretch()
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Save |
                                        QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        self.b_test = bb.addButton("測試連線", QtWidgets.QDialogButtonBox.ButtonRole.ActionRole)
        self.b_test.clicked.connect(self.test)
        bb.accepted.connect(self.save)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _urls(self):
        return [u.strip() for u in self.urls.text().replace("；", ",").replace(";", ",").split(",") if u.strip()]

    def _apply(self) -> None:
        s = settings()
        s.set("remote.hub_urls", self._urls())
        s.set("remote.token", self.token.text().strip())

    def test(self) -> None:
        self._apply()
        urls, token = hub_urls(), self.token.text().strip()
        self.msg.setText("測試中…")

        def work():
            out = []
            for u in urls:
                try:
                    p = Hub(u, token, timeout=4).ping()
                    out.append(f"✔ {u}：Hub v{p.get('version')}" + ("" if p.get("auth") else "，但 token 不正確"))
                except HubError as e:
                    out.append(f"✖ {e}")
            return out
        run_bg(work, lambda out: self.msg.setText("<br>".join(
            theme.span(x, "ok" if x.startswith("✔") and "不正確" not in x else "err") for x in out)),
            lambda m: self.msg.setText(theme.span(m, "err")))

    def save(self) -> None:
        from ....core import labfile

        self._apply()
        s = settings()
        try:
            if s.path is not None:
                labfile.set_value(s.path, "remote.hub_urls", self._urls())
                labfile.set_value(s.path, "remote.token", self.token.text().strip())
        except Exception as e:  # noqa: BLE001
            QtWidgets.QMessageBox.warning(self, "無法儲存", f"寫入 settings.yaml 失敗：{e}")
            return
        self.accept()
