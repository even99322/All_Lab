"""Network Workspace (Qt side): Host publishing and Client mirroring.

Host   HostPublisher follows the Viewer the Host is working in (and its YIG
       window): it shares that measurement, and publishes the Viewer's view
       state (plus the raw reads it needed), YIG figures as data, and
       annotation strokes. Viewer state is polled every 300 ms; only changes
       are sent.
Client ClientMirror shows a read-only mirror Viewer (the existing Viewer on a
       remote Experiment), a YIG mirror window and the annotations. While
       connected, the whole Client application is view-only: input goes only
       to the Network panel and the mirror's Leave button.

Network I/O runs in the background threads of host.py / client.py; this
module only receives their events through a queued Qt signal.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import threading
import time
from pathlib import Path

from PySide6.QtCore import QEvent, QEventLoop, QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication, QToolBar, QWidget

from app.network import protocol as P
from app.network.quality import Quality
from app.network.remote_data import RecordingReader, RemoteReader, RemoteReaderError, build_remote_experiment

_workspace = None


def workspace() -> "NetworkWorkspace":
    global _workspace
    if _workspace is None:
        _workspace = NetworkWorkspace(QApplication.instance())
    return _workspace


# -- helpers ------------------------------------------------------------------------
def _viewers():
    from app.gui.main_window import MainWindow

    return [w for w in QApplication.topLevelWidgets() if isinstance(w, MainWindow)
            and not getattr(w, "_network_mirror", False) and w.isVisible()]


def _browser_root(path: str) -> str:
    """Path relative to an open Browser database (never the Host's absolute path)."""
    from app.gui.browser_window import BrowserWindow

    target = Path(path).resolve()
    for widget in QApplication.topLevelWidgets():
        if isinstance(widget, BrowserWindow) and getattr(widget, "scanner_root", None):
            root = Path(widget.scanner_root).resolve()
            try:
                return f"{root.name}/{target.relative_to(root).as_posix()}"
            except ValueError:
                continue
    return target.name


def _view_state(viewer) -> dict | None:
    state = viewer.session_state()
    if not isinstance(state, dict):
        return None
    state = {key: value for key, value in state.items() if key in {"display", "trace_selection", "pane"}}
    display = state.get("display")
    three_d = isinstance(display, dict) and display.get("mode") == "3d"
    if three_d:
        state["display"] = None                    # 3D is not shared over the network
    state["three_d"] = three_d
    # Marks and annotations live in their own store, not in the saved state: send them too,
    # with the Data key made portable (the Client knows the measurement by another key).
    try:
        state["marks"] = viewer.marks_payload(portable=True)
        state["show_mark_values"] = viewer.show_mark_values_checkbox.isChecked()
    except Exception:
        pass
    # The 2D heatmap's zoom is not part of the Viewer's saved state; mirror it too.
    try:
        if viewer.mode_combo.currentIndex() == 1:
            (x0, x1), (y0, y1) = viewer.plot_2d_widget.view_box.viewRange()
            state["range_2d"] = [[float(x0), float(x1)], [float(y0), float(y1)]]
    except Exception:
        pass
    return state


def _stable(value) -> str:
    return json.dumps(value, sort_keys=True, default=str)


# -- Host ---------------------------------------------------------------------------
class HostPublisher(QObject):
    def __init__(self, space: "NetworkWorkspace"):
        super().__init__(space)
        self.space = space
        self.viewer = None
        self.measurement_id: str | None = None
        self._experiment_key = None
        self._reads: dict[str, tuple] = {}
        self._last_view = None
        self._seq = 0
        self._yig_dirty: set[int] = set()
        self._yig_hooked: dict[int, object] = {}
        self._yig_window = None
        self._yig_full = True
        self._yig_sent: dict[str, str] = {}
        self._yig_layout_digest = None
        self._annotation_sig = None
        self._view_timer = QTimer(self, interval=300, timeout=self._poll_view)
        self._yig_timer = QTimer(self, interval=400, timeout=self._poll_yig)
        self._ink_timer = QTimer(self, interval=100, timeout=self._poll_annotation)
        QApplication.instance().focusChanged.connect(self._focus_changed)

    def start(self) -> None:
        self._pick_viewer(None)
        for timer in (self._view_timer, self._yig_timer, self._ink_timer):
            timer.start()

    def stop(self) -> None:
        for timer in (self._view_timer, self._yig_timer, self._ink_timer):
            timer.stop()
        self._unwrap()
        self.viewer = None

    def client_joined(self) -> None:
        self._last_view = None                     # resend the full view and figures
        self._yig_full = True
        self._annotation_sig = None
        if self.measurement_id is not None and self.viewer is not None:
            self._announce_share()

    # which Viewer ---------------------------------------------------------------
    def _focus_changed(self, _old, new) -> None:
        if new is None:
            return
        window = new.window()
        from app.gui.main_window import MainWindow

        if isinstance(window, MainWindow) and not getattr(window, "_network_mirror", False):
            self._pick_viewer(window)
        else:
            source = window.parent() if window.parent() is not None else getattr(window, "viewer", None)
            if isinstance(source, MainWindow) and not getattr(source, "_network_mirror", False):
                self._pick_viewer(source)

    def _pick_viewer(self, viewer) -> None:
        if viewer is None:
            viewers = _viewers()
            viewer = viewers[-1] if viewers else None
        if viewer is self.viewer:
            return
        self._unwrap()
        self.viewer = viewer
        self._experiment_key = None
        self._last_view = None
        self._yig_full = True
        self.space.changed.emit()

    def _unwrap(self) -> None:
        experiment = getattr(self.viewer, "experiment", None)
        reader = getattr(experiment, "_reader", None)
        if isinstance(reader, RecordingReader):
            experiment._reader = reader._inner

    # share the measurement ------------------------------------------------------------
    def _ensure_shared(self) -> bool:
        viewer = self.viewer
        experiment = getattr(viewer, "experiment", None)
        if viewer is None or experiment is None:
            return False
        key = (id(viewer), experiment.data_identity)
        if key == self._experiment_key:
            return self.measurement_id is not None
        self._experiment_key = key
        self.measurement_id = None
        self._reads.clear()
        # Record the raw reads the Viewer makes from now on (Clients prefetch them);
        # a fresh cache makes the current view read everything it shows once.
        if not isinstance(experiment._reader, RecordingReader):
            experiment._reader = RecordingReader(experiment._reader)
        from app.core.cache import CachedExperiment, LRUDataCache

        viewer.cached = CachedExperiment(experiment, LRUDataCache())
        path = str(experiment.source_path)
        hint = _browser_root(path)

        def work():
            try:
                measurement_id = self.space.host.share(path, hint)
            except Exception as error:
                self.space.raw_event.emit({"event": "share_failed", "reason": str(error)})
                return
            self.space.raw_event.emit({"event": "_shared", "key": key, "mid": measurement_id})

        threading.Thread(target=work, name="llvn-share", daemon=True).start()
        return False

    def shared_ready(self, key, measurement_id: str) -> None:
        if key != self._experiment_key:
            return
        self.measurement_id = measurement_id
        self._last_view = None
        self._announce_share()
        try:
            self.viewer.update_plot(preserve_view=True)      # re-read through the recorder
        except Exception:
            pass

    def _announce_share(self) -> None:
        experiment = self.viewer.experiment
        self.space.host.publish({"type": "share", "mid": self.measurement_id},
                                {"log_name": experiment.log_name, "title": self.viewer.windowTitle()})

    # publish view ------------------------------------------------------------------------
    def _poll_view(self) -> None:
        if self.space.host is None or not self._ensure_shared():
            return
        try:
            state = _view_state(self.viewer)
        except Exception:
            return
        reader = self.viewer.experiment._reader
        new_reads = reader.take_reads() if isinstance(reader, RecordingReader) else []
        for path, slice_ in new_reads:
            self._reads[P.slice_key(path, slice_)] = (path, slice_)
        signature = _stable(state)
        if signature == self._last_view and not new_reads:
            return
        self._last_view = signature
        self._seq += 1
        full_paths = {path for path, slice_ in self._reads.values() if slice_ is None}
        reads = [{"path": path, "slice": P.encode_slice(slice_)} for path, slice_ in self._reads.values()
                 if slice_ is None or path not in full_paths]
        self.space.host.publish({"type": "view", "mid": self.measurement_id, "seq": self._seq},
                                {"state": state, "reads": reads})

    # publish YIG ---------------------------------------------------------------------------
    def _yig_for_viewer(self):
        windows = [w for w in getattr(self.viewer, "_yig_fitting_windows", {}).values() if w.isVisible()]
        return windows[-1] if windows else None

    def _poll_yig(self) -> None:
        if self.space.host is None or self.measurement_id is None:
            return
        window = self._yig_for_viewer()
        if window is not self._yig_window:
            self._yig_window = window
            self._yig_full = True
            self._yig_hooked.clear()
            if window is None:
                self.space.host.publish({"type": "yig", "closed": True})
                return
        if window is None:
            return
        from app.network.yig_snapshot import hook_dirty, signature, snapshot

        hook_dirty(window, self._yig_hooked, self._yig_dirty)
        full = self._yig_full
        try:
            layout, content = snapshot(window, None if full else set(self._yig_dirty), set(self._yig_sent))
        except Exception:
            return
        self._yig_dirty.clear()
        sent = {} if full else self._yig_sent
        changed = {}
        for key, value in content.items():
            digest = signature(value)
            if sent.get(key) != digest:
                changed[key] = value
                sent[key] = digest
        layout_digest = signature(layout)
        if not changed and layout_digest == self._yig_layout_digest and not full:
            return
        self._yig_sent, self._yig_layout_digest, self._yig_full = sent, layout_digest, False
        self.space.host.publish({"type": "yig", "full": full}, {"layout": layout, "content": changed})

    # publish annotation --------------------------------------------------------------------
    def _poll_annotation(self) -> None:
        if self.space.host is None or self.viewer is None:
            return
        from app.network.ink import snapshot_session

        data = {"viewer": snapshot_session(getattr(self.viewer, "annotation", None))}
        window = self._yig_window
        if window is not None:
            data["yig"] = snapshot_session(getattr(window, "annotation", None), root=window.centralWidget())
        signature = _stable(data)
        if signature == self._annotation_sig:
            return
        self._annotation_sig = signature
        self.space.host.publish({"type": "annotation"}, data)


# -- Client -------------------------------------------------------------------------
class ClientMirror(QObject):
    applied = Signal()

    def __init__(self, space: "NetworkWorkspace"):
        super().__init__(space)
        self.space = space
        self.viewer = None
        self.yig_window = None
        self.reader: RemoteReader | None = None
        self.measurement_id: str | None = None
        self._pending_view = None
        self._busy = False
        self._tempdir: str | None = None
        self._ink = None

    # lifecycle ------------------------------------------------------------------------
    def close(self) -> None:
        from app.network.ink import InkMirror

        if isinstance(self._ink, InkMirror):
            self._ink.close()
        self._ink = None
        for window in (self.yig_window, self.viewer):
            if window is not None:
                window._network_closing = True
                window.close()
                window.deleteLater()
        self.yig_window = self.viewer = None
        if self.reader is not None:
            self.reader.close()                    # remote data only ever lived in memory
        self.reader = None
        if self._tempdir:
            shutil.rmtree(self._tempdir, ignore_errors=True)
            self._tempdir = None
        self.measurement_id = None

    # messages -------------------------------------------------------------------------
    def handle(self, header: dict, value) -> None:
        kind = header.get("type")
        if kind == "share":
            self._on_share(str(header.get("mid")), value or {})
        elif kind == "view":
            if header.get("mid") == self.measurement_id:
                self._pending_view = (header.get("seq", 0), value or {})
                self._next_view()
            else:
                self._pending_view = (header.get("seq", 0), value or {}, header.get("mid"))
        elif kind == "yig":
            if self.yig_window is None and not header.get("full") and not header.get("closed"):
                self.space.request_full_yig()           # reopened locally: ask for everything again
            self._on_yig(header, value or {})
        elif kind == "annotation":
            if self._ink is not None:
                self._ink.apply(value or {})

    def _on_share(self, measurement_id: str, info: dict) -> None:
        if measurement_id == self.measurement_id:
            return
        self.space.notice.emit(self.space.text("net.receiving_structure"))
        client = self.space.client

        def work():
            try:
                answer = client.wait(client.request_structure(measurement_id), 30)
                local = find_local_copy(answer.get("fingerprint") or {}, self.space.shared_folders())
                self.space.raw_event.emit({"event": "_structure", "mid": measurement_id,
                                           "answer": answer, "local": local})
            except Exception as error:
                self.space.raw_event.emit({"event": "notice", "text": f"{error}"})

        threading.Thread(target=work, name="llvn-structure", daemon=True).start()

    def structure_ready(self, measurement_id: str, answer: dict, local: str | None) -> None:
        from app.core.labber_parser import load_experiment

        previous_reader = self.reader
        if local:
            experiment = load_experiment(local)            # same file on this computer: no transfer
            self.reader = None
            self.space.data_source = "local"
        else:
            self.reader = RemoteReader(fetch=self._blocking_fetch)
            experiment = build_remote_experiment(answer["description"], self.reader,
                                                 f"{self.space.client.host_info.get('session_id')}/{measurement_id}")
            self.space.data_source = "host"
        self.measurement_id = measurement_id
        viewer = self._ensure_viewer()
        viewer.show_experiment(experiment)
        if previous_reader is not None:
            previous_reader.close()
        viewer.setWindowTitle(self.space.text("net.mirror_title").format(
            host=self.space.client.host_info.get("host_name", ""), name=experiment.log_name))
        pending = self._pending_view
        if pending is not None and len(pending) == 3 and pending[2] == measurement_id:
            self._pending_view = pending[:2]
        self._next_view()
        self.space.changed.emit()

    def _ensure_viewer(self):
        if self.viewer is not None:
            return self.viewer
        from app.core.axis_preset_store import AxisPresetStore
        from app.core.comment_store import CommentStore
        from app.core.mark_store import MarkStore
        from app.core.named_view_store import NamedViewStore
        from app.core.overlay_store import OverlayStore
        from app.core.transform_store import TransformStore
        from app.core.viewer_display_state_store import ViewerDisplayStateStore
        from app.gui.main_window import MainWindow

        # A throwaway folder: the mirror never writes the Client's own records.
        self._tempdir = tempfile.mkdtemp(prefix="lablogviewer-mirror-")
        folder = Path(self._tempdir)
        viewer = MainWindow(
            transform_store=TransformStore(folder / "t.json"), axis_preset_store=AxisPresetStore(folder / "a.json"),
            overlay_store=OverlayStore(folder / "o.json"), mark_store=MarkStore(folder / "m.json"),
            viewer_display_state_store=ViewerDisplayStateStore(folder / "v.json"),
            comment_store=CommentStore(folder / "c.json"), named_view_store=NamedViewStore(folder / "n.json"),
        )
        viewer._network_mirror = True
        viewer.surface_3d_action.setVisible(False)
        for bar in viewer.findChildren(QToolBar):
            bar.hide()                                   # Open / Export / tools do not apply to a mirror
        viewer.menuBar().hide()
        from app.network.mirror_chrome import install_mirror_banner

        install_mirror_banner(viewer, self.space)
        viewer.resize(1280, 820)
        viewer.show()
        from app.network.ink import InkMirror

        self._ink = InkMirror(self)
        self.viewer = viewer
        return viewer

    # view state -------------------------------------------------------------------------
    def _next_view(self) -> None:
        if self._busy or self._pending_view is None or len(self._pending_view) != 2 or self.viewer is None:
            return
        seq, value = self._pending_view
        self._pending_view = None
        reads = [(item["path"], P.decode_slice(item["slice"])) for item in value.get("reads", [])]
        missing = [(path, slice_) for path, slice_ in reads if self.reader is not None and not self.reader.has(path, slice_)]
        if not missing:
            self._apply(value.get("state"))
            return
        self._busy = True
        client, reader, measurement_id = self.space.client, self.reader, self.measurement_id

        def work():
            error = None
            for index, (path, slice_) in enumerate(missing, 1):
                def progress(done, total, index=index):
                    self.space.raw_event.emit({"event": "progress", "done": done, "total": total,
                                               "part": index, "parts": len(missing)})
                try:
                    array = client.wait(client.read(measurement_id, path, slice_, progress))
                    reader.put(path, slice_, array)
                except Exception as exc:
                    error = str(exc)
                    break
            self.space.raw_event.emit({"event": "_prefetched", "state": value.get("state"), "error": error})

        threading.Thread(target=work, name="llvn-prefetch", daemon=True).start()

    def prefetched(self, state, error: str | None) -> None:
        self._busy = False
        self.space.progress.emit("", 0, 0)
        if error:
            self.space.notice.emit(self.space.text("net.transfer_failed").format(reason=error))
        elif self.viewer is not None:
            self._apply(state)
        self._next_view()

    def _apply(self, state) -> None:
        if not isinstance(state, dict) or self.viewer is None:
            return
        try:
            self.viewer.restore_session_state(state)
            marks = state.get("marks")
            if isinstance(marks, dict):
                values = state.get("show_mark_values")
                if isinstance(values, bool):
                    self.viewer.show_mark_values_checkbox.setChecked(values)
                self.viewer.apply_marks_payload(marks, portable=True)
            range_2d = state.get("range_2d")
            if isinstance(range_2d, list) and len(range_2d) == 2 and self.viewer.mode_combo.currentIndex() == 1:
                self.viewer.plot_2d_widget.view_box.setRange(xRange=range_2d[0], yRange=range_2d[1], padding=0)
            if state.get("three_d"):
                self.space.notice.emit(self.space.text("net.host_in_3d"))
        except Exception as error:                    # never let a sync message crash the Client
            self.space.notice.emit(str(error))
        self.applied.emit()

    def _blocking_fetch(self, path: str, slice_):
        """A read that was not prefetched: wait without freezing the window."""
        client = self.space.client
        if client is None or self.measurement_id is None:
            raise RemoteReaderError("Not connected.")
        request = client.read(self.measurement_id, path, slice_)
        loop = QEventLoop()
        timer = QTimer(interval=50, timeout=lambda: loop.quit() if request.done.is_set() else None)
        timer.start()
        deadline = time.monotonic() + 120
        while not request.done.is_set() and time.monotonic() < deadline and client.state != "disconnected":
            loop.exec()
        timer.stop()
        if not request.done.is_set() or request.error:
            raise RemoteReaderError(request.error or "timeout")
        return request.result

    # YIG ---------------------------------------------------------------------------------
    def _on_yig(self, header: dict, value: dict) -> None:
        if header.get("closed"):
            if self.yig_window is not None:
                self.yig_window._network_closing = True
                self.yig_window.close()
                self.yig_window = None
            return
        if self.yig_window is None:
            from app.network.yig_mirror import YigMirrorWindow

            self.yig_window = YigMirrorWindow(self.space)
            self.yig_window.show()
        self.yig_window.apply(value, full=bool(header.get("full")))


def find_local_copy(fingerprint: dict, folders: list[str]) -> str | None:
    """A file under the Client's shared folders with the Host file's size and SHA-256."""
    size, digest, relative = fingerprint.get("size"), fingerprint.get("sha256"), fingerprint.get("relative") or ""
    if not isinstance(size, int) or not isinstance(digest, str):
        return None
    name = Path(relative).name
    candidates: list[Path] = []
    for folder in folders:
        root = Path(folder)
        if not root.is_dir():
            continue
        parts = Path(relative).parts
        for start in range(len(parts)):
            candidate = root.joinpath(*parts[start:])
            if candidate.is_file():
                candidates.append(candidate)
        if name:
            for index, candidate in enumerate(root.rglob(name)):
                candidates.append(candidate)
                if index > 50:
                    break
    for candidate in dict.fromkeys(candidates):
        try:
            if candidate.stat().st_size != size:
                continue
            hasher = hashlib.sha256()
            with candidate.open("rb") as stream:
                for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
                    hasher.update(block)
            if hasher.hexdigest() == digest:
                return str(candidate)
        except OSError:
            continue
    return None


# -- manager -----------------------------------------------------------------------------
class NetworkWorkspace(QObject):
    changed = Signal()
    notice = Signal(str)
    progress = Signal(str, int, int)
    copy_received = Signal(str)
    raw_event = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.role = "offline"
        self.host = None
        self.client = None
        self.publisher: HostPublisher | None = None
        self.mirror: ClientMirror | None = None
        self.client_quality = Quality(None, None, "unknown")
        self.client_state = "disconnected"
        self.host_clients: dict[str, dict] = {}
        self.data_source = ""
        self.last_reason = ""
        self._lock_filter = None
        self.raw_event.connect(self._on_event)

    # settings / text ----------------------------------------------------------------------
    @staticmethod
    def store():
        from app.localization import get_localization_manager

        return get_localization_manager().store

    @staticmethod
    def text(key: str) -> str:
        from app.localization import get_localization_manager

        return get_localization_manager().text(key)

    def user_name(self) -> str:
        return self.store().network_user_name()

    def shared_folders(self) -> list[str]:
        return self.store().network_shared_folders()

    # Host -------------------------------------------------------------------------------------
    def start_hosting(self, session_name: str) -> None:
        from app.core.warmup import start_background_warmup

        start_background_warmup()   # no font-cache stall during the session
        from app.network.host import HostServer

        if self.role != "offline":
            raise RuntimeError("Already in a Network Workspace session.")
        host = HostServer(self.user_name(), session_name, self.raw_event.emit,
                          port=int(os.environ.get("LABLOGVIEWER_NET_PORT", "47811")),
                          discovery_port=int(os.environ.get("LABLOGVIEWER_NET_DISCOVERY", "47810")))
        host.start()                                     # raises OSError without a LAN
        self.host = host
        self.role = "host"
        self.publisher = HostPublisher(self)
        self.publisher.start()
        self.changed.emit()

    def stop_hosting(self) -> None:
        if self.host is not None:
            self.publisher.stop()
            self.host.stop()
        self.host = None
        self.publisher = None
        self.host_clients.clear()
        self.role = "offline"
        self.changed.emit()

    # Client ----------------------------------------------------------------------------------------
    def connect_to(self, address: str, port: int, join_code: str) -> None:
        from app.core.warmup import start_background_warmup

        start_background_warmup()   # no font-cache stall during the session
        from app.core.external_state import default_state_path
        from app.network.client import ClientConnection

        if self.role != "offline":
            raise RuntimeError("Already in a Network Workspace session.")
        self.role = "client"
        self.client_state = "connecting"
        self.mirror = ClientMirror(self)
        self.client = ClientConnection(address, port, self.user_name(), join_code, self.raw_event.emit,
                                       copy_folder=default_state_path("received"))
        self.client.start()
        self.changed.emit()

    def disconnect(self) -> None:
        if self.client is not None:
            self.client.disconnect()
        self._client_ended("left")

    def _client_ended(self, reason: str) -> None:
        self.set_locked(False)
        if self.mirror is not None:
            self.mirror.close()
        self.mirror = None
        self.client = None
        self.role = "offline"
        self.client_state = "disconnected"
        self.last_reason = reason
        self.data_source = ""
        self.changed.emit()

    def request_full_yig(self) -> None:
        if self.client is not None:
            self.client.request_resync()

    # view-only lock -------------------------------------------------------------------------------
    def set_locked(self, locked: bool) -> None:
        app = QApplication.instance()
        if locked and self._lock_filter is None:
            self._lock_filter = _ViewOnlyFilter(self)
            app.installEventFilter(self._lock_filter)
        elif not locked and self._lock_filter is not None:
            app.removeEventFilter(self._lock_filter)
            self._lock_filter = None

    # events from network threads (queued to the GUI thread) ------------------------------------------
    def _on_event(self, event: dict) -> None:
        kind = event.get("event")
        if kind == "state" and self.role == "client":
            self.client_state = event.get("state", "")
            if self.client_state == "connected":
                self.set_locked(True)
            elif self.client_state == "disconnected":
                self._client_ended(str(event.get("reason") or ""))
                return
            self.changed.emit()
        elif kind == "quality":
            self.client_quality = Quality.from_dict(event.get("quality"))
            self.changed.emit()
        elif kind == "sync" and self.mirror is not None:
            self.mirror.handle(event.get("message") or {}, event.get("value"))
        elif kind == "_structure" and self.mirror is not None:
            self.mirror.structure_ready(event["mid"], event["answer"], event.get("local"))
        elif kind == "_prefetched" and self.mirror is not None:
            self.mirror.prefetched(event.get("state"), event.get("error"))
        elif kind == "progress":
            self.progress.emit(self.text("net.receiving_data"), int(event.get("done", 0)), int(event.get("total", 0)))
        elif kind == "_shared" and self.publisher is not None:
            self.publisher.shared_ready(event.get("key"), event["mid"])
        elif kind == "client_resync" and self.publisher is not None:
            self.publisher.client_joined()
        elif kind in {"client_joined", "client_left", "client_quality"}:
            client_id = event.get("client_id")
            if kind == "client_left":
                self.host_clients.pop(client_id, None)
            else:
                entry = self.host_clients.setdefault(client_id, {"name": event.get("name", ""),
                                                                 "address": event.get("address", "")})
                if kind == "client_quality":
                    entry["quality"] = event.get("quality")
                elif self.publisher is not None:
                    self.publisher.client_joined()
            self.changed.emit()
        elif kind == "copy_received":
            self.copy_received.emit(str(event.get("path")))
        elif kind == "copy_failed":
            self.notice.emit(self.text("net.copy_failed"))
        elif kind in {"notice", "share_failed"}:
            self.notice.emit(str(event.get("text") or event.get("reason") or ""))


class _ViewOnlyFilter(QObject):
    """While connected as a Client, only the Network panel and Leave button take input."""

    _INPUT = {
        QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease, QEvent.Type.MouseButtonDblClick,
        QEvent.Type.MouseMove, QEvent.Type.Wheel, QEvent.Type.KeyPress, QEvent.Type.KeyRelease,
        QEvent.Type.ShortcutOverride, QEvent.Type.ContextMenu, QEvent.Type.DragEnter, QEvent.Type.Drop,
        QEvent.Type.TabletPress, QEvent.Type.TouchBegin,
    }

    def eventFilter(self, watched, event):  # noqa: N802 - Qt API spelling
        if event.type() not in self._INPUT or not isinstance(watched, QWidget):
            return False
        if watched.property("networkAllowed") or watched.window().property("networkAllowed"):
            return False
        parent = watched
        while parent is not None:
            if parent.property("networkAllowed"):
                return False
            parent = parent.parentWidget()
        from PySide6.QtWidgets import QDialog, QMessageBox

        if isinstance(watched.window(), (QMessageBox, QDialog)) and watched.window().isModal():
            return False                                 # notices (e.g. copy received) can be dismissed
        return True
