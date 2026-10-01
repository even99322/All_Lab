"""
操作設定 JSON 存取（原子寫入 + 備份，避免閃退時寫壞檔案）
"""
import os
import json
import numpy as np


def default_dir():
    """設定檔資料夾（<程式資料夾>/config）"""
    from .paths import config_dir
    return config_dir()


def _default(o):
    if isinstance(o, np.generic):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    return str(o)


class SessionStore:
    def __init__(self, path=None):
        self.path = path or os.path.join(default_dir(), "fit_gui_session.json")
        self._last_text = None

    @property
    def folder(self):
        return os.path.dirname(self.path)

    def load(self):
        for p in (self.path, self.path + ".bak"):
            try:
                with open(p, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                if isinstance(data, dict):
                    return data
            except (OSError, ValueError):
                continue
        return {}

    def save(self, state, force=False):
        """內容沒變就不寫；回傳是否有寫入"""
        text = json.dumps(state, ensure_ascii=False, indent=2, default=_default)
        if text == self._last_text and not force:
            return False
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        if os.path.exists(self.path):
            try:
                os.replace(self.path, self.path + ".bak")
            except OSError:
                pass
        os.replace(tmp, self.path)
        self._last_text = text
        return True
