"""Qt 拖放小工具（PyQt6 或 PySide6 都可以；量測模塊用 PyQt6，讀檔模塊用 PySide6）。"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from . import dnd


def _qt():
    try:
        from PyQt6 import QtCore  # type: ignore
        return QtCore
    except ImportError:
        from PySide6 import QtCore  # type: ignore
        return QtCore


def mime_for(items: Iterable[Dict[str, Any]]):
    """items：[{path?, catalog_id?, gateway?, name, …}] → QMimeData（本機檔案也放 urls，任何程式都能接）。"""
    QtCore = _qt()
    items = list(items)
    md = QtCore.QMimeData()
    urls = [QtCore.QUrl.fromLocalFile(str(Path(i["path"]).resolve())) for i in items if i.get("path")]
    if urls:
        md.setUrls(urls)
    md.setData(dnd.MIME, QtCore.QByteArray(dnd.payload(items)))
    md.setText("\n".join(str(i.get("path") or i.get("name") or "") for i in items))
    return md


def accepts(md) -> bool:
    return bool(md is not None and (md.hasUrls() or md.hasFormat(dnd.MIME) or md.hasFormat(dnd.MIME_WEB)
                                   or md.hasText()))


def files_from(md, session: Any = None, dest: Optional[Path] = None) -> List[Path]:
    data = None
    for f in (dnd.MIME, dnd.MIME_WEB):
        if md.hasFormat(f):
            data = bytes(md.data(f))
            break
    urls = [u.toString() for u in md.urls()] if md.hasUrls() else []
    if not urls and md.hasText():
        urls = [t for t in md.text().splitlines() if t.strip()]
    return dnd.resolve(urls, data, session, dest)
