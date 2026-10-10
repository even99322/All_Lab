"""A small cross-device lock for one data file: a ``<name>.lock`` file next to it.

HDF5's own locking does not work between computers on a network drive, so two devices could write the
same file at once. The lock file holds the computer name and the time; whoever creates it first writes,
everyone else waits and retries. A lock older than STALE_SECONDS (its owner crashed) is taken over.
"""

from __future__ import annotations

import json
import os
import socket
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

STALE_SECONDS = 120.0


class FileBusyError(OSError):
    """Another device holds the lock (or the lock file cannot be created)."""


def lock_path(path: str | Path) -> Path:
    path = Path(path)
    return path.with_name(path.name + ".lock")


def _holder(lock: Path) -> str:
    try:
        return str(json.loads(lock.read_text(encoding="utf-8")).get("host", "another device"))
    except (OSError, ValueError, AttributeError):
        return "another device"


@contextmanager
def file_lock(path: str | Path, wait: float = 6.0, stale: float = STALE_SECONDS):
    lock = lock_path(path)
    token = uuid.uuid4().hex
    body = json.dumps({"host": socket.gethostname(), "pid": os.getpid(), "time": time.time(), "token": token})
    deadline = time.monotonic() + wait
    while True:
        try:
            descriptor = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                age = time.time() - lock.stat().st_mtime
            except OSError:
                continue                                   # released a moment ago: try again at once
            if age > stale:
                try:
                    lock.unlink()
                except OSError:
                    pass
                continue
            if time.monotonic() >= deadline:
                raise FileBusyError(f"The file is in use by {_holder(lock)} (lock file {lock.name}).")
            time.sleep(0.25)
            continue
        except OSError as error:
            raise FileBusyError(f"The lock file {lock.name} cannot be created ({error}).") from error
        try:
            os.write(descriptor, body.encode("utf-8"))
        finally:
            os.close(descriptor)
        break
    try:
        yield
    finally:
        try:
            if json.loads(lock.read_text(encoding="utf-8")).get("token") == token:
                lock.unlink()
        except (OSError, ValueError):
            pass
