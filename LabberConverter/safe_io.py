"""Bounded I/O recovery and read-only source checks. No mount discovery or probing."""
from __future__ import annotations

from dataclasses import dataclass
import errno
import hashlib
import os
from pathlib import Path
import time


STABILITY_SECONDS = 1.0


class SourceBusy(ValueError):
    pass


class Cancelled(Exception):
    pass


@dataclass(frozen=True)
class RetryPolicy:
    # 0.4.2: NAS (SMB) hiccups often last a few seconds; 5 attempts with doubling waits ≈ 0.5+1+2+4 s.
    attempts: int = 5
    delay: float = .5
    backoff: float = 2.


def check_cancel(cancel):
    if cancel and cancel():
        raise Cancelled()


def pause(seconds, cancel=None):
    end = time.monotonic()+seconds
    while time.monotonic() < end:
        check_cancel(cancel)
        time.sleep(min(.1, max(0, end-time.monotonic())))


# Windows network errors (SMB): network name gone / not found / unexpected network error / semaphore timeout /
# connection reset / network unreachable / remote closed / remote host not reachable.
WIN_NETWORK = {53, 59, 64, 67, 121, 1231, 1236, 2250, 1232, 6, 31}
TRANSIENT_WORDS = ["operation timed out", "timed out", "connection reset", "stale file handle", "host is down",
                   "network", "semaphore timeout", "specified network name", "not connected", "broken pipe",
                   "resource temporarily unavailable", "input/output error"]


def transient(error):
    codes = {errno.ETIMEDOUT, errno.EIO, errno.ECONNRESET, errno.ECONNABORTED,
             errno.ENETDOWN, errno.ENETUNREACH, errno.EHOSTUNREACH, errno.ENOTCONN, errno.EPIPE}
    for name in ("ESTALE", "EHOSTDOWN", "ENETRESET"):
        if hasattr(errno, name):
            codes.add(getattr(errno, name))
    return (getattr(error,"errno",None) in codes or getattr(error,"winerror",None) in WIN_NETWORK
            or any(term in str(error).lower() for term in TRANSIENT_WORDS))


def retry_io(operation, stage, policy=RetryPolicy(), cancel=None, notify=None):
    for attempt in range(1,policy.attempts+1):
        check_cancel(cancel)
        try:
            return operation()
        except (OSError, RuntimeError) as error:
            if not transient(error) or attempt == policy.attempts:
                raise OSError(getattr(error,"errno",None) or errno.EIO,
                              f"{stage}: {error} (attempt {attempt}/{policy.attempts})") from error
            if notify:
                notify({"stage":"retry", "detail":stage, "attempt":attempt, "total":policy.attempts})
            pause(policy.delay*(getattr(policy,"backoff",1.)**(attempt-1)),cancel)


def identity(path):
    stat = Path(path).stat()
    return {"size":stat.st_size,"mtime_ns":stat.st_mtime_ns,"ctime_ns":stat.st_ctime_ns,
            "device":stat.st_dev,"inode":stat.st_ino}


def require_unchanged(path, expected):
    if identity(path) != expected:
        raise SourceBusy(f"Source is changing or being measured; refused to copy or publish. Retry after measurement stops: {path}")


def stable_source(path, policy=RetryPolicy(), cancel=None, notify=None):
    if notify:
        notify({"stage":"checking"})
    before = retry_io(lambda:identity(path),"source stat",policy,cancel,notify)
    pause(STABILITY_SECONDS,cancel)
    retry_io(lambda:require_unchanged(path,before),"source stability check",policy,cancel,notify)
    return before


def sha256(path, cancel=None):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while block := stream.read(1024*1024):
            check_cancel(cancel)
            digest.update(block)
    return digest.hexdigest()


def copy_bytes(source, destination, cancel=None):
    with Path(source).open("rb") as incoming, Path(destination).open("wb") as outgoing:
        while block := incoming.read(1024*1024):
            check_cancel(cancel)
            outgoing.write(block)
        outgoing.flush()
        os.fsync(outgoing.fileno())


def verified_copy(source, destination, stage, policy=RetryPolicy(), cancel=None, notify=None):
    """Retry the whole copy into a private temporary path, including read-back."""
    expected = retry_io(lambda:sha256(source,cancel),stage+" source checksum",policy,cancel,notify)
    def transfer():
        copy_bytes(source,destination,cancel)
        if sha256(destination,cancel) != expected:
            raise OSError(errno.EIO, "Read-back checksum mismatch")
        return expected
    return retry_io(transfer,stage,policy,cancel,notify)


def explicitly_active(file):
    active_words = {"running","writing","acquiring","measuring","active","in progress","in_progress"}
    for node in [file, file.get("Data")]:
        if node is None:
            continue
        for key in ["writing","is_writing","running","is_running","acquiring","is_acquiring",
                    "measurement_active","status","state","run_state"]:
            if key not in node.attrs:
                continue
            value = node.attrs[key]
            if getattr(value,"size",1) != 1:
                continue
            if hasattr(value,"item"):
                value = value.item()
            if isinstance(value,bytes):
                value = value.decode("utf-8",errors="replace")
            if isinstance(value,str):
                word = value.strip().lower()
                if word in active_words or (key.startswith("is_") or key in {"writing","running","acquiring","measurement_active"}) and word in {"true","1","yes"}:
                    return True
            elif key not in {"status","state","run_state"} and bool(value):
                return True
    # Completed=False alone can also mean a stopped, partial acquisition.
    return False


def atomic_json(path, value, policy=RetryPolicy(), cancel=None, notify=None):
    import json
    import tempfile
    path = Path(path)
    data = json.dumps(value,ensure_ascii=False,indent=2).encode("utf-8")
    def write():
        fd, name = tempfile.mkstemp(prefix=".progress-",suffix=".tmp",dir=path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(fd,"wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            if temporary.read_bytes() != data:
                raise OSError(errno.EIO,"Progress read-back mismatch")
            os.replace(temporary,path)
            if path.read_bytes() != data:
                raise OSError(errno.EIO,"Published progress read-back mismatch")
        finally:
            temporary.unlink(missing_ok=True)
    retry_io(write,"progress save",policy,cancel,notify)
