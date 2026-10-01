"""QEL Lab 大程式整合（通信模塊 labcomm）。

沒有 labcomm（不是從 QEL Lab 大程式開啟）時全部不作用，LabLogViewer 照常使用。

* 本機傳遞 ``open_file``：量測模塊存好數據後交過來 → 開一個 Viewer（也可以是大程式上的數據編號，先下載）。
* 本機傳遞 ``show_papers``：列出這些標籤對應的論文（可點開到論文庫）。
* Interfaces → Measurement：把選取的數據交給量測模塊套用它的量測設置（量測模塊沒開會請大程式開啟）。
* Interfaces → Online Paper Library：依選取數據的標籤列出論文；沒有選數據時開啟論文庫。
* 共用標籤：啟動時把大程式的標籤加進 Tags（分類對應到這裡的分類）。
"""
from __future__ import annotations

import html
import logging
import threading
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QMessageBox, QTextBrowser, QVBoxLayout

logger = logging.getLogger(__name__)

try:
    import labcomm
    from labcomm import actions, handoff, local
    from labcomm import tags as shared_tags
    from labcomm.errors import CommError
    AVAILABLE = True
except ImportError:                                   # pragma: no cover - 沒有大程式時
    labcomm = None
    AVAILABLE = False

    class CommError(RuntimeError):                    # type: ignore[no-redef]
        pass

TAG_CATEGORY_MAP = {"Measurement": "Other", "Paper Topic": "Other"}
_bridge: "QelBridge | None" = None


def client():
    """登入大程式時的連線（大程式開啟模塊時會帶 QEL_TOKEN）；沒有登入回傳 None。"""
    if not AVAILABLE:
        return None
    cfg = labcomm.load_config()
    if not cfg.token:
        return None
    from app import __version__
    return labcomm.PortalClient(cfg.portal_url, cfg.token, timeout=15, client_name=f"lablogviewer/{__version__}")


def send_to_measurement(path: str) -> str:
    """交給量測模塊套用設置。回傳 "sent" 或 "launching"；失敗丟 CommError。"""
    if not AVAILABLE:
        raise CommError("沒有通信模塊（labcomm），請從 QEL Lab 大程式開啟 LabLogViewer")
    if not handoff.is_data_file(path):
        raise CommError("只能傳 HDF5 數據檔")
    return local.deliver("labcontrol", actions.APPLY_SCHEME, {"path": str(path)}, sender="lablogviewer")


def tags_of_file(path: str) -> list[str]:
    """數據檔自己的標籤（Labber Tags 與 Lab Control 寫入的 qel/meta）。"""
    if not AVAILABLE:
        return []
    try:
        return list(handoff.read_meta(path).get("tags") or [])
    except Exception:  # noqa: BLE001
        return []


def papers_html(groups: list[dict]) -> str:
    out = []
    for g in groups:
        tag = html.escape(g["tag"])
        link = f' <a href="{html.escape(g["tag_url"])}">（論文庫）</a>' if g.get("tag_url") and not g.get("no_access") else ""
        out.append(f"<h3>{tag}{link}</h3>")
        if g.get("no_access"):
            out.append("<p>站長還沒有開放論文模塊給你。</p>")
            continue
        if not g.get("papers"):
            out.append("<p style='color:gray'>還沒有論文（可以在大程式網頁的標籤頁連結）。</p>")
            continue
        out.append("<ul>")
        for p in g["papers"]:
            meta = " · ".join(str(x) for x in (p.get("first_author"), p.get("year"), p.get("venue")) if x)
            out.append(f'<li><a href="{html.escape(p["url"])}">{html.escape(p["title"])}</a>'
                       f'<br><span style="color:gray">{html.escape(meta)}</span></li>')
        out.append("</ul>")
    return "".join(out) or "<p>沒有標籤。</p>"


def show_papers_dialog(parent, tags: list[str], title: str = "相關論文") -> bool:
    """依標籤列出論文。沒有登入大程式時回傳 False（呼叫端改用原本的做法）。"""
    c = client()
    if c is None:
        return False
    tags = shared_tags.normalize_list(tags)
    try:
        groups = c.papers_for_tags(tags) if tags else []
    except CommError as e:
        QMessageBox.warning(parent, title, str(e))
        return True
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.resize(620, 520)
    v = QVBoxLayout(dlg)
    view = QTextBrowser()
    view.setOpenExternalLinks(True)
    view.setHtml(papers_html(groups) if tags else
                 "<p>這筆數據還沒有標籤。加上標籤（例如專案、板子設計）後，這裡會列出對應的論文。</p>")
    v.addWidget(view)
    bb = QDialogButtonBox(QDialogButtonBox.Close)
    lib = bb.addButton("開啟論文庫", QDialogButtonBox.ActionRole)
    lib.clicked.connect(lambda: open_paper_library())
    bb.rejected.connect(dlg.reject)
    v.addWidget(bb)
    dlg.setAttribute(Qt.WA_DeleteOnClose)
    dlg.show()
    parent._qel_papers_dialog = dlg
    return True


def open_paper_library() -> bool:
    """開論文庫網頁（經大程式一次性登入，瀏覽器直接是登入狀態）。"""
    c = client()
    if c is None:
        return False
    try:
        site = c.api("GET", "/site")
        t = c.api("POST", "/sso/ticket")
        from urllib.parse import quote
        return QDesktopServices.openUrl(QUrl(f"{c.base}{t['url']}&next={quote(site['paperlib_url'] + '/', safe='')}"))
    except CommError:
        return False


def selected_tags(browser) -> list[str]:
    """Browser 選取的數據：這裡加的 Tags ＋ 檔案裡的 Labber Tags。"""
    try:
        _item, entry = browser._selected_data()
    except Exception:  # noqa: BLE001
        entry = None
    if entry is None:
        return []
    tags: list[str] = []
    try:
        tags += sorted(browser.tag_store.tags_for(browser._entry_database(entry), entry.relative_path))
    except Exception:  # noqa: BLE001
        pass
    tags += tags_of_file(entry.absolute_path)
    return list(dict.fromkeys(tags))


def show_related_papers(browser) -> bool:
    """Interfaces → Online Paper Library：有選數據就列出它的論文，否則開論文庫。沒有大程式時回傳 False。"""
    if client() is None:
        return False
    try:
        _item, entry = browser._selected_data()
    except Exception:  # noqa: BLE001
        entry = None
    if entry is None:
        return open_paper_library()
    return show_papers_dialog(browser, selected_tags(browser), f"相關論文：{entry.log_name}")


def merge_shared_tags(tag_store, taxonomy: dict) -> int:
    """把大程式的共用標籤加進 Tags（已經有的不動）。回傳新增的數量。"""
    have = {t.casefold() for t in tag_store.list_tags()}
    cats = set(tag_store.categories())
    added = 0
    for t in taxonomy.get("tags", []):
        name = t.get("name") or ""
        if not name or name.casefold() in have:
            continue
        cat = TAG_CATEGORY_MAP.get(t.get("category"), t.get("category"))
        try:
            if tag_store.create_tag(name, cat if cat in cats else "Other"):
                added += 1
                have.add(name.casefold())
        except Exception:  # noqa: BLE001
            logger.debug("無法加入標籤 %s", name, exc_info=True)
    return added


class QelBridge(QObject):
    """本機傳遞（背景執行緒）→ 主執行緒。"""
    action = Signal(str, dict)
    tags_loaded = Signal(dict)

    def __init__(self, browser, open_path: Callable[[str], Any]) -> None:
        super().__init__(browser)
        self.browser = browser
        self.open_path = open_path
        self.viewers: list = []
        self.endpoint = None
        self.action.connect(self._on_action)
        self.tags_loaded.connect(lambda tax: merge_shared_tags(browser.tag_store, tax)
                                 if hasattr(browser, "tag_store") else None)

    def start(self) -> "QelBridge":
        from app import __version__
        try:
            self.endpoint = local.LocalEndpoint("lablogviewer", __version__, self._handle,
                                                [actions.OPEN_FILE, actions.SHOW_PAPERS]).start()
        except OSError as e:
            logger.warning("本機傳遞埠開不起來：%s", e)
        threading.Thread(target=self._load_tags, daemon=True, name="qel-tags").start()
        return self

    def stop(self) -> None:
        if self.endpoint is not None:
            self.endpoint.stop()
            self.endpoint = None

    def _load_tags(self) -> None:
        try:
            self.tags_loaded.emit(shared_tags.shared_taxonomy(client()))
        except Exception:  # noqa: BLE001
            logger.debug("讀不到共用標籤", exc_info=True)

    def _handle(self, action: str, payload: dict) -> dict:
        if action == actions.OPEN_FILE:
            if payload.get("path"):
                p = Path(str(payload["path"]))
                if not p.is_file():
                    raise CommError(f"這台電腦找不到 {p}")
                self.action.emit(action, {"path": str(p)})
                return {"accepted": True}
            if payload.get("dataset_id") is not None:
                p = self._download(int(payload["dataset_id"]))
                self.action.emit(action, {"path": str(p)})
                return {"accepted": True, "path": str(p)}
            raise CommError("需要 path 或 dataset_id")
        self.action.emit(action, dict(payload))
        return {"accepted": True}

    def _download(self, dsid: int) -> Path:
        c = client()
        if c is None:
            raise CommError("沒有登入 QEL Lab 大程式")
        ds = c.dataset(dsid)
        local_path = Path(ds.get("path") or "")
        if ds.get("path") and local_path.is_file():
            return local_path                                  # 本機就有（同一台電腦或掛載的 NAS）
        dest = labcomm.qel_home() / "cache" / "datasets" / f"{dsid}_{Path(ds['name']).name}"
        if not dest.exists():
            c.download_dataset(dsid, dest)
        return dest

    def _on_action(self, action: str, payload: dict) -> None:
        if action == actions.OPEN_FILE:
            w = self.open_path(payload["path"])
            if w is not None:
                self.viewers = [v for v in self.viewers if _alive(v)] + [w]
                w.raise_()
                w.activateWindow()
        elif action == actions.SHOW_PAPERS:
            show_papers_dialog(self.browser, list(payload.get("tags") or []))


def _alive(w) -> bool:
    try:
        from shiboken6 import isValid
        return isValid(w)
    except ImportError:                                        # pragma: no cover
        return True


def attach_browser(browser) -> "QelBridge | None":
    """main.py 建好 BrowserWindow 後呼叫。"""
    global _bridge
    if not AVAILABLE:
        return None

    def open_path(path: str):
        from app import __version__
        viewer = browser._take_viewer(None)
        viewer.setWindowTitle(f"LabLogViewer v{__version__} — {Path(path).name}")
        viewer.operation_journal = browser._record_operation
        viewer.show()
        viewer.open_file(path)
        return viewer

    _bridge = QelBridge(browser, open_path).start()
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is not None:
        app.aboutToQuit.connect(_bridge.stop)
    return _bridge
