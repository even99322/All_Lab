"""Virtual acceptance: Client that follows the scripted Host (loopback only)."""
import json
import os
import sys
import time

ROOT = str(__import__("pathlib").Path(__file__).resolve().parents[2])
sys.path.insert(0, ROOT)
os.chdir(ROOT)
OUT = os.environ["NET_OUT"]
NAME = os.environ.get("NET_NAME", "學生電腦 1")


def main():
    from PySide6.QtCore import QTimer, QPoint, QEvent, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv[:1])
    app.setApplicationName("LabLogViewer")
    app.setQuitOnLastWindowClosed(False)
    from app.core.data_location import data_root
    data_root()
    from app.localization import initialize_localization
    from app.theme import initialize_theme
    loc = initialize_localization(app)
    initialize_theme(app, loc.store)
    loc.store.set_network_user_name(NAME)
    folders = os.environ.get("NET_SHARED")
    if folders:
        loc.store.set_network_shared_folders([folders])
    from app.core.warmup import warm_now

    warm_now()              # as in the real program: Matplotlib is ready before a session starts
    from app.network.workspace import workspace

    space = workspace()
    for _ in range(900):                               # the Host may be building caches first
        if os.path.exists(os.path.join(OUT, "host.json")):
            break
        time.sleep(0.1)
    info = json.load(open(os.path.join(OUT, "host.json")))
    code = os.environ.get("NET_CODE", info["code"])
    events = []
    space.raw_event.connect(lambda e: events.append({k: v for k, v in e.items() if k in ("event", "state", "reason", "quality")}))
    space.connect_to("127.0.0.1", info["port"], code)
    record = {"name": NAME, "shots": [], "states": [], "max_gap_ms": 0.0}
    beat = {"last": time.monotonic()}
    started = time.monotonic()

    def heartbeat():
        now = time.monotonic()
        gap = (now - beat["last"]) * 1000 - 50
        record["max_gap_ms"] = max(record["max_gap_ms"], gap)
        if gap > 250:
            record.setdefault("stalls", []).append([round(now - started, 2), round(gap)])
        beat["last"] = now

    pulse = QTimer(interval=50, timeout=heartbeat)
    pulse.start()

    def shot(tag):
        mirror = space.mirror
        entry = {"tag": tag, "t": round(time.monotonic() - started, 2), "state": space.client_state, "source": space.data_source,
                 "quality": space.client_quality.as_dict()}
        if mirror is not None and mirror.viewer is not None:
            viewer = mirror.viewer
            entry["mode"] = viewer.mode_combo.currentIndex()
            entry["title"] = viewer.windowTitle()
            entry["cached_bytes"] = mirror.reader.cached_bytes() if mirror.reader else 0
            manager = viewer._mark_managers[0]
            entry["marks"] = [m.x for m in manager.marks()]
            entry["annotations"] = len(manager.annotations())
            viewer.grab().save(os.path.join(OUT, f"{NAME}_{tag}_viewer.png"))
            # a click on the mirror must be ignored (view only)
            before = viewer.mode_combo.currentIndex()
            target = viewer.mode_combo
            event = QMouseEvent(QEvent.Type.MouseButtonPress, target.rect().center(), target.mapToGlobal(target.rect().center()),
                                Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
            entry["click_blocked"] = not app.notify(target, event) or viewer.mode_combo.currentIndex() == before
        if mirror is not None and mirror.yig_window is not None:
            entry["yig_items"] = len(mirror.yig_window.items)
            mirror.yig_window.grab().save(os.path.join(OUT, f"{NAME}_{tag}_yig.png"))
        ink = getattr(mirror, "_ink", None) if mirror else None
        entry["ink_strokes"] = sum(len(c.strokes) for cs in getattr(ink, "_canvases", {}).values() for c in cs) if ink else 0
        record["shots"].append(entry)
        record["events"] = events
        with open(os.path.join(OUT, f"{NAME}_client.json"), "w") as stream:
            json.dump(record, stream, ensure_ascii=False, indent=1, default=str)

    for seconds, tag in ((4, "start"), (9, "after_2d"), (12.5, "after_zoom"), (18, "after_1d_ink"), (26, "after_yig")):
        QTimer.singleShot(int(seconds * 1000), lambda tag=tag: shot(tag))

    def finish():
        record["events"] = events
        record["final_state"] = space.client_state
        record["final_reason"] = space.last_reason
        record["mirror_closed"] = space.mirror is None
        with open(os.path.join(OUT, f"{NAME}_client.json"), "w") as stream:
            json.dump(record, stream, ensure_ascii=False, indent=1, default=str)
        app.quit()

    QTimer.singleShot(int(float(os.environ.get("NET_CLIENT_SECONDS", "40")) * 1000), finish)
    app.exec()


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    main()
    os._exit(0)
