"""QEL Lab 大程式整合（通信模塊 labcomm）。

沒有 labcomm（不是從大程式開啟、或舊的 Lab APP 環境）時這裡全部不作用，Lab Control 照常使用。

* 存檔（``data.exported`` 事件）→ 把量測方案與標籤寫進數據檔（qel/scheme、qel/meta）→ 登錄到大程式
  → 讀檔模塊開著就交給它開啟（settings.yaml ``qel.open_in_viewer``）。
* 量測節點上傳到 Hub（``data.uploaded`` 事件）→ 更新登錄資料，網頁與其他電腦可以下載。
* 讀檔模塊把數據檔拖過來 / 選單「用這個檔的設置量測」（本機傳遞 ``apply_scheme``）→ 讀回方案、載入流程圖。
* 檔案設置的 Tags 欄位提示全平台共用標籤。
"""
from __future__ import annotations

import logging
import queue
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..settings import setting

log = logging.getLogger("labcontrol.qel")

try:                                       # 通信模塊由大程式放進 PYTHONPATH
    import labcomm
    from labcomm import actions, handoff, local
    from labcomm import tags as shared_tags
    from labcomm.errors import CommError
    AVAILABLE = True
except ImportError:                        # pragma: no cover - 沒有大程式時
    labcomm = None
    AVAILABLE = False

    class CommError(RuntimeError):         # type: ignore[no-redef]
        pass

DATA_SUFFIXES = (".hdf5", ".h5", ".hdf")
ApplyFn = Callable[[Dict[str, Any], str], None]


def is_data_file(path: str) -> bool:
    return str(path).lower().endswith(DATA_SUFFIXES)


def scheme_from_file(path: str) -> Dict[str, Any]:
    """讀回數據檔裡的量測方案（Lab Control 存的 Labber 檔、原生 HDF5、或同一天 _raw/ 的原生檔）。"""
    if not AVAILABLE:
        raise CommError("沒有通信模塊（labcomm），請從 QEL Lab 大程式開啟 Lab Control")
    s = handoff.extract_scheme(path)
    if not s:
        raise CommError(f"「{Path(path).name}」裡沒有量測設置（不是 Lab Control 存的，或是舊版存的檔）")
    return s


class QelBridge:
    def __init__(self, station, version: str, client=None) -> None:
        self.station = station
        self.version = version
        self.enabled = AVAILABLE
        self.client = client
        self.on_apply: Optional[ApplyFn] = None
        self.datasets: Dict[str, int] = {}          # 檔案路徑 → 大程式的數據編號
        self._taxonomy: Optional[Dict[str, Any]] = None
        self._tokens: List[int] = []
        self._q: "queue.Queue[Optional[Tuple[str, Dict[str, Any]]]]" = queue.Queue()
        self._endpoint = None
        self._worker: Optional[threading.Thread] = None
        if not self.enabled:
            return
        if self.client is None:
            cfg = labcomm.load_config()
            self.client = labcomm.PortalClient(cfg.portal_url, cfg.token, timeout=10,
                                               client_name=f"labcontrol/{version}") if cfg.token else None

    # ---- 啟動 / 關閉 ---------------------------------------------------------------
    def start(self, endpoint: bool = True) -> "QelBridge":
        if not self.enabled:
            return self
        bus = self.station.bus
        self._tokens = [bus.subscribe("data.exported", self._on_exported),
                        bus.subscribe("data.uploaded", self._on_uploaded)]
        self._worker = threading.Thread(target=self._work, daemon=True, name="qel-bridge")
        self._worker.start()
        if endpoint:
            try:
                self._endpoint = local.LocalEndpoint("labcontrol", self.version, self._handle,
                                                     [actions.APPLY_SCHEME]).start()
            except OSError as e:
                log.warning("本機傳遞埠開不起來：%s", e)
        return self

    def close(self) -> None:
        for t in self._tokens:
            self.station.bus.unsubscribe(t)
        self._tokens.clear()
        if self._worker is not None:
            self._q.put(None)
            self._worker.join(timeout=10)
            self._worker = None
        if self._endpoint is not None:
            self._endpoint.stop()
            self._endpoint = None

    # ---- 讀檔模塊 → 量測模塊：套用設置 --------------------------------------------------
    def resolve(self, payload: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
        if isinstance(payload.get("scheme"), dict):
            return payload["scheme"], str(payload.get("label") or "傳來的方案")
        if payload.get("path"):
            p = str(payload["path"])
            return scheme_from_file(p), Path(p).name
        if payload.get("dataset_id") is not None:
            if self.client is None:
                raise CommError("沒有登入 QEL Lab 大程式")
            s = self.client.dataset_scheme(int(payload["dataset_id"]))
            if not s:
                raise CommError("這筆數據沒有量測設置")
            return s, f"數據 #{payload['dataset_id']}"
        raise CommError("需要 path、dataset_id 或 scheme")

    def _handle(self, action: str, payload: Dict[str, Any]) -> Any:
        scheme, label = self.resolve(payload)
        if self.on_apply is None:
            raise CommError("Lab Control 還沒準備好")
        self.on_apply(scheme, label)
        return {"accepted": True, "name": scheme.get("name")}

    # ---- 共用標籤 ----------------------------------------------------------------
    def tag_names(self, refresh: bool = False) -> List[str]:
        if not self.enabled:
            return []
        if self._taxonomy is None or refresh:
            self._taxonomy = shared_tags.shared_taxonomy(self.client)
        return shared_tags.tag_names(self._taxonomy)

    # ---- 存檔後 ------------------------------------------------------------------
    def _on_exported(self, topic: str, p: Dict[str, Any]) -> None:
        self._q.put(("exported", dict(p)))

    def _on_uploaded(self, topic: str, p: Dict[str, Any]) -> None:
        self._q.put(("uploaded", dict(p)))

    def _work(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                return
            kind, p = item
            if kind == "noop":
                p["_done"].set()
                continue
            try:
                if kind == "exported":
                    self._exported(p)
                else:
                    self._uploaded(p)
            except Exception as e:  # noqa: BLE001 - 大程式的事不能影響量測
                log.warning("QEL 整合（%s）失敗：%s", kind, e)

    def flush(self, timeout: float = 30) -> None:
        """等背景工作做完（測試與關閉程式時用）。"""
        done = threading.Event()
        self._q.put(("noop", {"_done": done}))
        done.wait(timeout)

    def _source(self) -> Dict[str, Any]:
        from ..remote.hub import hostname, user_name
        return {"module": "labcontrol", "version": self.version, "host": hostname(), "user": user_name()}

    def _clean_tags(self, p: Dict[str, Any]) -> List[str]:
        tags = [t for t in (p.get("tags") or []) if t and t != p.get("name")]   # 方案名稱只是預設，不當共用標籤
        try:
            tax = self._taxonomy or shared_tags.load_cache()
        except Exception:  # noqa: BLE001
            tax = None
        return shared_tags.normalize_list(tags, tax)

    def _exported(self, p: Dict[str, Any]) -> None:
        path = str(p.get("path") or "")
        if not path or not is_data_file(path):
            return
        tags = self._clean_tags(p)
        scheme = p.get("scheme") if isinstance(p.get("scheme"), dict) else None
        source = dict(self._source(), writer=p.get("writer"))
        if setting("qel.embed_scheme", True):
            handoff.embed(path, scheme, {"tags": tags, "source": source})
        if self.client is not None and setting("qel.register_datasets", True):
            try:
                fp = handoff.fingerprint(path)
            except OSError:
                fp = ""
            ds = self.client.register_dataset(Path(path).name, path=str(Path(path).resolve()), tags=tags,
                                              scheme=scheme, source=source, fingerprint=fp,
                                              meta={"segment": p.get("segment"), "experiment": p.get("name")})
            self.datasets[str(Path(path).resolve())] = ds["id"]
            handoff.embed(path, None, {"dataset_id": ds["id"]})
            self.station.bus.log(f"🗂 已登錄到 QEL Lab（數據 #{ds['id']}）")
        mode = setting("qel.open_in_viewer", "running")
        if mode != "never":
            try:
                if mode == "always":
                    local.deliver("lablogviewer", actions.OPEN_FILE, {"path": path}, sender="labcontrol")
                elif local.is_running("lablogviewer"):
                    local.send("lablogviewer", actions.OPEN_FILE, {"path": path}, sender="labcontrol")
            except CommError as e:
                log.info("交給讀檔模塊失敗：%s", e)

    def _uploaded(self, p: Dict[str, Any]) -> None:
        if self.client is None:
            return
        path = str(Path(str(p.get("path") or "")).resolve())
        if not is_data_file(path):
            return
        try:
            fp = handoff.fingerprint(path)
        except OSError:
            fp = ""
        self.client.register_dataset(Path(path).name, path=path, fingerprint=fp,
                                     hub_file={"node": p.get("node"), "rel": p.get("rel")})


# ---- 視窗整合（PyQt6） ----------------------------------------------------------------
def attach_window(window, station, version: str, client=None) -> Optional[QelBridge]:
    """接到 Lab Control 主視窗：拖數據檔套用設置、本機傳遞、Tags 提示。不改主視窗的程式碼。"""
    from PyQt6 import QtCore, QtWidgets

    bridge = QelBridge(station, version, client=client)
    if not bridge.enabled:
        return None

    class _Relay(QtCore.QObject):
        apply = QtCore.pyqtSignal(dict, str)

        def eventFilter(self, obj, ev) -> bool:  # noqa: N802 — 拖數據檔到主視窗任何地方
            t = ev.type()
            if t in (QtCore.QEvent.Type.DragEnter, QtCore.QEvent.Type.DragMove):
                md = ev.mimeData()
                if md.hasUrls() and any(is_data_file(u.toLocalFile()) for u in md.urls()):
                    ev.acceptProposedAction()
                    return True
            elif t == QtCore.QEvent.Type.Drop:
                md = ev.mimeData()
                files = [u.toLocalFile() for u in md.urls() if is_data_file(u.toLocalFile())] if md.hasUrls() else []
                if files:
                    ev.acceptProposedAction()
                    QtCore.QTimer.singleShot(0, lambda f=files[0]: apply_file(f))
                    return True
            return False

    relay = _Relay(window)

    def apply_scheme(scheme: Dict[str, Any], label: str) -> None:
        from ..scheme import Scheme
        try:
            sch = Scheme.from_dict(scheme)
        except Exception as e:  # noqa: BLE001
            QtWidgets.QMessageBox.warning(window, "套用設置", f"方案格式不對：{e}")
            return
        window.raise_()
        window.activateWindow()
        window._load_scheme(sch, None)
        window.statusBar().showMessage(f"已套用「{label}」的量測設置（方案：{sch.name}）", 10000)

    def apply_file(path: str) -> None:
        try:
            scheme, label = bridge.resolve({"path": path})
        except CommError as e:
            QtWidgets.QMessageBox.information(window, "套用設置", str(e))
            return
        apply_scheme(scheme, label)

    relay.apply.connect(apply_scheme)
    bridge.on_apply = lambda s, label: relay.apply.emit(s, label)     # 背景執行緒 → 主執行緒
    window.setAcceptDrops(True)
    window.installEventFilter(relay)
    for w in window.findChildren(QtWidgets.QWidget):
        if w.acceptDrops():
            w.installEventFilter(relay)
    window._qel_relay = relay
    window._qel_bridge = bridge
    window.qel_apply_file = apply_file
    _tag_completer(window, bridge)
    bridge.start()
    old_close = window.closeEvent

    def close_event(ev) -> None:
        old_close(ev)
        if ev.isAccepted():
            bridge.close()
    window.closeEvent = close_event
    return bridge


def _tag_completer(window, bridge: QelBridge) -> None:
    """檔案設置的 Tags 欄位（逗號分隔）提示共用標籤。"""
    from PyQt6 import QtCore, QtWidgets

    edit = getattr(getattr(window, "files", None), "tags", None)
    if edit is None:
        return

    class TagCompleter(QtWidgets.QCompleter):
        def splitPath(self, path: str) -> List[str]:  # noqa: N802
            return [path.split(",")[-1].strip()]

        def pathFromIndex(self, index) -> str:  # noqa: N802
            head = edit.text().rsplit(",", 1)
            prefix = (head[0] + ", ") if len(head) > 1 else ""
            return prefix + super().pathFromIndex(index)

    model = QtCore.QStringListModel([], edit)
    comp = TagCompleter(model, edit)
    comp.setCaseSensitivity(QtCore.Qt.CaseSensitivity.CaseInsensitive)
    comp.setFilterMode(QtCore.Qt.MatchFlag.MatchContains)
    edit.setCompleter(comp)
    edit.setToolTip("逗號分隔；會提示 QEL Lab 的共用標籤")

    def load() -> None:
        from ..apps.qt.worker import run_bg
        run_bg(bridge.tag_names, lambda names: model.setStringList(names or []), lambda m: None)
    load()
