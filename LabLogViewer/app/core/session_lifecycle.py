"""Crash-loop protection for volatile application-session restoration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import logging
from pathlib import Path
import sys
import threading
from typing import Any
from uuid import uuid4

from app import __version__
from app.core.external_state import atomic_write_json, default_state_path, load_json_state


logger = logging.getLogger(__name__)
LIFECYCLE_SCHEMA = 1


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def default_lifecycle_path() -> Path:
    return default_state_path("session_lifecycle.json")


@dataclass(frozen=True)
class StartupRecovery:
    safe_recovery: bool = False
    previous_status: str = "missing"
    last_operation: str = "Unknown"
    last_operation_time: str = "Unknown"
    last_checkpoint: str = "Unknown"
    last_exception: str | None = None
    database_path: str | None = None
    lifecycle_corrupt: bool = False
    session_corrupt: bool = False
    session_state_unavailable: bool = False
    native_crash: str | None = None


class SessionLifecycleStore:
    """Atomically records RUNNING/CLEAN state and sparse operation checkpoints."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else default_lifecycle_path()
        self._lock = threading.RLock()
        self._payload: dict[str, Any] = {}
        self._started = False

    def begin(self) -> StartupRecovery:
        loaded = load_json_state(self.path, {})
        raw = loaded.value if isinstance(loaded.value, dict) else {}
        lifecycle_corrupt = bool(loaded.recovered_from_corruption)
        schema = raw.get("schema_version", LIFECYCLE_SCHEMA)
        if not isinstance(schema, int) or schema > LIFECYCLE_SCHEMA:
            lifecycle_corrupt = True
        previous_status = str(raw.get("status", "missing"))
        safe = lifecycle_corrupt or previous_status not in {"missing", "clean"}
        checkpoint = raw.get("last_safe_checkpoint")
        checkpoint_label = (
            str(checkpoint.get("label", "Unknown"))
            if isinstance(checkpoint, dict) else "Unknown"
        )
        context = StartupRecovery(
            safe_recovery=safe,
            previous_status=previous_status,
            last_operation=str(raw.get("last_operation", "Unknown")),
            last_operation_time=str(raw.get("last_operation_time", "Unknown")),
            last_checkpoint=checkpoint_label,
            last_exception=raw.get("last_exception") if isinstance(raw.get("last_exception"), str) else None,
            database_path=raw.get("safe_database_path") if isinstance(raw.get("safe_database_path"), str) else None,
            lifecycle_corrupt=lifecycle_corrupt,
            native_crash=self._take_native_crash() if safe else None,
        )
        with self._lock:
            self._payload = {
                "schema_version": LIFECYCLE_SCHEMA,
                "status": "running",
                "session_id": uuid4().hex,
                "started_at": _now(),
                "application_version": __version__,
                "last_operation": "Application started",
                "last_operation_time": _now(),
                "last_safe_checkpoint": checkpoint if isinstance(checkpoint, dict) else None,
                "safe_database_path": context.database_path,
                "last_exception": None,
            }
            self._started = True
            self._write_locked()
        return context

    @property
    def native_crash_path(self) -> Path:
        return self.path.with_name("native_crash.txt")

    def _take_native_crash(self) -> str | None:
        """The Python/native stacks faulthandler wrote when the previous run died (a segfault
        leaves no Python exception behind); kept as native_crash_previous.txt."""
        path = self.native_crash_path
        try:
            text = path.read_text(encoding="utf-8", errors="replace").strip()
            if text:
                path.replace(path.with_name("native_crash_previous.txt"))
                return text[-12000:]
        except OSError:
            pass
        return None

    def enable_native_crash_log(self) -> None:
        """Make a hard crash (segmentation fault, abort) write every thread's stack to a file."""
        import faulthandler
        try:
            self.native_crash_path.parent.mkdir(parents=True, exist_ok=True)
            self._native_stream = self.native_crash_path.open("w", encoding="utf-8")
            faulthandler.enable(file=self._native_stream, all_threads=True)
        except (OSError, RuntimeError, ValueError):
            logger.exception("Could not enable the native crash log.")

    def clear_native_crash_log(self) -> None:
        """A normal exit leaves nothing for the next start to report."""
        stream = getattr(self, "_native_stream", None)
        if stream is not None:
            try:
                import faulthandler
                faulthandler.disable()
                stream.close()
                self.native_crash_path.unlink(missing_ok=True)
            except OSError:
                pass

    def set_session_corrupt(self, context: StartupRecovery, *, session_corrupt: bool) -> StartupRecovery:
        return StartupRecovery(
            safe_recovery=context.safe_recovery or bool(session_corrupt),
            previous_status=context.previous_status,
            last_operation=context.last_operation,
            last_operation_time=context.last_operation_time,
            last_checkpoint=context.last_checkpoint,
            last_exception=context.last_exception,
            database_path=context.database_path,
            lifecycle_corrupt=context.lifecycle_corrupt,
            session_corrupt=bool(session_corrupt),
            session_state_unavailable=context.session_state_unavailable or bool(session_corrupt),
            native_crash=context.native_crash,
        )

    def record_operation(self, label: str, **details: Any) -> None:
        safe_details = {
            str(key): str(value)[:200]
            for key, value in details.items()
            if isinstance(key, str) and isinstance(value, (str, int, float, bool))
        }
        with self._lock:
            self._payload.update(
                status="running",
                last_operation=str(label)[:160],
                last_operation_time=_now(),
                operation_details=safe_details,
            )
            self._write_locked()

    def record_checkpoint(self, label: str, *, database_path: str | None = None) -> None:
        checkpoint = {"label": str(label)[:160], "time": _now()}
        with self._lock:
            self._payload["last_safe_checkpoint"] = checkpoint
            if database_path:
                self._payload["safe_database_path"] = str(Path(database_path).expanduser().resolve())
            self._write_locked()

    def record_exception(self, exception_text: str) -> None:
        with self._lock:
            self._payload.update(
                status="running",
                last_operation="Unhandled Python exception",
                last_operation_time=_now(),
                last_exception=str(exception_text)[-12000:],
            )
            self._write_locked()

    def mark_clean(self) -> None:
        self.clear_native_crash_log()
        with self._lock:
            if not self._started:
                return
            self._payload.update(
                status="clean",
                closed_at=_now(),
                last_operation="Application closed normally",
                last_operation_time=_now(),
                operation_details={},
                last_exception=None,
            )
            self._write_locked()

    def _write_locked(self) -> None:
        try:
            atomic_write_json(self.path, self._payload)
        except OSError:
            logger.exception("Could not persist application lifecycle state.")


def install_exception_hooks(store: SessionLifecycleStore) -> None:
    """Record uncaught Python exceptions without replacing normal reporting."""
    previous_sys_hook = sys.excepthook

    def sys_hook(exc_type, exc, tb):
        try:
            import traceback
            store.record_exception("".join(traceback.format_exception(exc_type, exc, tb)))
        except Exception:
            logger.exception("Could not record an uncaught exception.")
        previous_sys_hook(exc_type, exc, tb)

    sys.excepthook = sys_hook

    if hasattr(threading, "excepthook"):
        previous_thread_hook = threading.excepthook

        def thread_hook(args):
            try:
                import traceback
                store.record_exception("".join(traceback.format_exception(
                    args.exc_type, args.exc_value, args.exc_traceback
                )))
            except Exception:
                logger.exception("Could not record an uncaught thread exception.")
            previous_thread_hook(args)

        threading.excepthook = thread_hook
