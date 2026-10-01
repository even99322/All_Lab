"""通信錯誤。訊息一律是可以直接顯示給使用者看的中文。"""
from __future__ import annotations


class CommError(RuntimeError):
    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


class Unreachable(CommError):
    """連不到大程式伺服器（網路、NAS、容器沒開）。"""


class NotLoggedIn(CommError):
    """沒有登入或登入已過期（401）。"""


class PermissionDenied(CommError):
    """已登入，但站長沒有開放這個模塊或動作（403）。"""
