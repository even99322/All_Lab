"""NAS 登入視窗：用 Windows 認證管理員保存帳密（Lab Control 不存密碼）。"""
from __future__ import annotations

from PyQt6 import QtWidgets

from ....remote.nas_login import login
from ....settings import setting
from .. import theme
from ..worker import run_bg


class NasLoginDialog(QtWidgets.QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("NAS 登入")
        f = QtWidgets.QFormLayout(self)
        self.host = QtWidgets.QComboBox()
        self.host.setEditable(True)
        self.host.addItems([str(h) for h in (setting("remote.hosts", []) or [])])
        self.share = QtWidgets.QLineEdit(str(setting("remote.share", "ccuqel")))
        self.user = QtWidgets.QLineEdit("Lab_user")
        self.pw = QtWidgets.QLineEdit()
        self.pw.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        self.remember = QtWidgets.QCheckBox("記住（存在 Windows 認證管理員）")
        self.remember.setChecked(True)
        f.addRow("NAS", self.host)
        f.addRow("共用資料夾", self.share)
        f.addRow("帳號", self.user)
        f.addRow("密碼", self.pw)
        f.addRow("", self.remember)
        note = QtWidgets.QLabel("建議使用 Lab_user（需有 LabControl 資料夾的讀寫權限），不要用 admin。")
        note.setWordWrap(True)
        note.setStyleSheet("color:palette(placeholder-text);")
        f.addRow(note)
        self.msg = QtWidgets.QLabel()
        self.msg.setWordWrap(True)
        f.addRow(self.msg)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok
                                        | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        bb.button(QtWidgets.QDialogButtonBox.StandardButton.Ok).setText("登入")
        bb.accepted.connect(self._go)
        bb.rejected.connect(self.reject)
        f.addRow(bb)

    def _go(self) -> None:
        host, share, user, pw = self.host.currentText().strip(), self.share.text().strip(), \
            self.user.text().strip(), self.pw.text()
        self.msg.setText("連線中…")
        run_bg(lambda: login(host, share, user, pw, self.remember.isChecked()),
               lambda _r: self.accept(), lambda m: self.msg.setText(f"<span style='color:{theme.c('err')}'>{m}</span>"))
