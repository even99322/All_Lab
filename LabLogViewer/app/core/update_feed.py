"""Finding out that a newer version exists, from a folder on THIS computer.

A "feed" is ``latest.json`` in a local folder, or at the one allowed GitHub address::

    {"version": "2.0.1", "date": "2026-10-20", "package": "LabLogViewer-2.0.1.zip", "sha256": "...",
     "items": [{"category": "new", "en": "Colour range spin boxes", "zh": "色階範圍可手動輸入"}, ...]}

``category`` is ``new``, ``improved`` or ``fixed``; ``package`` and ``sha256`` are optional, and without
``items`` only the version is announced. Network-share paths are refused. The ONLY web address allowed is the project's own GitHub feed
(``raw.githubusercontent.com``, https); other addresses are refused. Fetching
only COPIES the package to a folder you choose after checking its hash; it never installs or restarts.
The feed folder is chosen by whoever wires it in (tests use temporary folders).
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from app.core.app_update import version_key

FEED_FILE = "latest.json"
CATEGORIES = ("new", "improved", "fixed")
MAX_ITEMS = 500
MAX_FEED_BYTES = 1_000_000
DEFAULT_FEED_URL = "https://raw.githubusercontent.com/edison413220047-byte/logbrowser_update/main/latest.json"
ALLOWED_FEED_HOSTS = ("raw.githubusercontent.com",)
FEED_TIMEOUT = 6.0


class FeedError(ValueError):
    pass


@dataclass
class UpdateInfo:
    version: str
    date: str = ""
    package: str | None = None
    sha256: str | None = None
    items: list[dict] = field(default_factory=list)      # {"category", "en", "zh"}
    line: int = 0                                        # 1.0.0 正式版起的 feed 帶 "line": 1（舊的 2.x 沒有）

    def details(self) -> dict:
        return {"version": self.version, "date": self.date, "items": [dict(i) for i in self.items]}


def is_local_folder(location: str | Path) -> bool:
    """Only an ordinary folder path: no web address, no ``\\\\server\\share`` / ``//server/share``."""
    text = str(location).strip()
    if not text or "://" in text or text.startswith(("\\\\", "//")):
        return False
    return True


def is_feed_url(text: str | Path) -> bool:
    """Only https on an allowed GitHub host (no other website, no network share)."""
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(str(text).strip())
    except ValueError:
        return False
    return parts.scheme == "https" and (parts.hostname or "") in ALLOWED_FEED_HOSTS and not parts.username


def _tls_context():
    """Verified TLS. Python from python.org on macOS ships without a CA list, so fall back to certifi
    or the system bundle when the default context has none."""
    import ssl

    context = ssl.create_default_context()
    if context.cert_store_stats().get("x509_ca"):
        return context
    paths = []
    try:
        import certifi
        paths.append(certifi.where())
    except ImportError:
        pass
    paths += ["/etc/ssl/cert.pem", "/private/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt"]
    for path in paths:
        if Path(path).is_file():
            return ssl.create_default_context(cafile=path)
    return context


def read_feed_url(url: str, timeout: float = FEED_TIMEOUT) -> UpdateInfo | None:
    """Download ``latest.json`` from the allowed address (small, short timeout). None on any problem."""
    import urllib.request

    if not is_feed_url(url):
        raise FeedError("This web address is not an allowed update source.")

    class _SameHostOnly(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return super().redirect_request(req, fp, code, msg, headers, newurl) if is_feed_url(newurl) else None

    try:
        opener = urllib.request.build_opener(_SameHostOnly, urllib.request.HTTPSHandler(context=_tls_context()))
        with opener.open(urllib.request.Request(url, headers={"User-Agent": "LabLogViewer"}), timeout=timeout) as reply:
            raw = reply.read(MAX_FEED_BYTES + 1)
        if len(raw) > MAX_FEED_BYTES:
            return None
        return _parse_feed(json.loads(raw.decode("utf-8")))
    except (OSError, ValueError):
        return None


def read_feed(folder: str | Path) -> UpdateInfo | None:
    """The announced update, or None when the folder has no (valid) feed."""
    if not is_local_folder(folder):
        raise FeedError("The update source must be a folder on this computer.")
    path = Path(folder) / FEED_FILE
    try:
        if not path.is_file() or path.stat().st_size > MAX_FEED_BYTES:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return _parse_feed(data)


def _parse_feed(data) -> UpdateInfo | None:
    if not isinstance(data, dict):
        return None
    version = data.get("version")
    if not isinstance(version, str) or not re.fullmatch(r"[0-9A-Za-z.\-]{1,32}", version.strip()):
        return None
    items = []
    raw = data.get("items")
    for entry in (raw if isinstance(raw, list) else [])[:MAX_ITEMS]:
        if not isinstance(entry, dict):
            continue
        en, zh = entry.get("en"), entry.get("zh")
        en, zh = (en if isinstance(en, str) else ""), (zh if isinstance(zh, str) else "")
        if not (en.strip() or zh.strip()):
            continue
        category = entry.get("category")
        items.append({"category": category if category in CATEGORIES else "improved",
                      "en": en.strip(), "zh": zh.strip()})
    package, digest = data.get("package"), data.get("sha256")
    return UpdateInfo(
        version=version.strip(), date=str(data.get("date") or "")[:32],
        package=package if isinstance(package, str) and package else None,
        sha256=digest.lower() if isinstance(digest, str) and re.fullmatch(r"[0-9a-fA-F]{64}", digest) else None,
        items=items, line=int(data.get("line") or 0) if str(data.get("line") or "0").isdigit() else 0)


def newer_update(folder: str | Path, current_version: str, skipped=()) -> UpdateInfo | None:
    """The feed's update when it is newer than ``current_version`` and was not skipped."""
    return newer_info(read_feed(folder), current_version, skipped)


def newer_info(info: UpdateInfo | None, current_version: str, skipped=()) -> UpdateInfo | None:
    if info is None or info.version in set(skipped):
        return None
    from app.core.app_update import legacy_feed

    if legacy_feed(info, current_version):
        return None                                      # 舊版號的 2.x：不要「更新」回去
    return info if version_key(info.version) > version_key(current_version) else None


def fetch_package(info: UpdateInfo, folder: str | Path, destination: str | Path) -> Path:
    """Copy the package to ``destination`` after checking its hash. Nothing is installed or run."""
    if not is_local_folder(folder) or not is_local_folder(destination):
        raise FeedError("Only folders on this computer are used.")
    if not info.package or info.package != Path(info.package).name or info.package.startswith("."):
        raise FeedError("This update has no package file.")
    source = Path(folder) / info.package
    if not source.is_file():
        raise FeedError("The package file is missing.")
    if info.sha256:
        digest = hashlib.sha256()
        with source.open("rb") as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                digest.update(block)
        if digest.hexdigest() != info.sha256:
            raise FeedError("The package is damaged (its checksum does not match).")
    target_folder = Path(destination)
    target_folder.mkdir(parents=True, exist_ok=True)
    target = target_folder / info.package
    temporary = target.with_name(target.name + ".part")
    shutil.copyfile(source, temporary)
    temporary.replace(target)
    return target


def check_and_notify(folder: str | Path, current_version: str) -> dict | None:
    """Add an "update" notice for a newer version (once per version); returns the notice if one was added."""
    from app.core import notifications

    return notify_info(read_feed(folder), current_version)


def notify_info(feed: UpdateInfo | None, current_version: str) -> dict | None:
    from app.core import notifications

    info = newer_info(feed, current_version, notifications.skipped_versions())
    if info is None:
        return None
    return notifications.add(
        "update", {"version": info.version, "n": len(info.items)}, details=info.details(),
        key=f"update:{info.version}")


def check_configured(current_version: str) -> dict | None:
    """The check of a configured LOCAL folder, if any. Never raises, never goes online (the GitHub feed is
    read by ``check_online``, which the browser window runs in the background)."""
    from app.core import notifications

    folder = notifications.update_folder()
    if not folder or not is_local_folder(folder):
        return None
    try:
        return check_and_notify(folder, current_version)
    except Exception:
        return None


def feed_source() -> str:
    """What the start-up check reads: the configured folder or allowed address, else the project's GitHub feed."""
    from app.core import notifications

    import os

    configured = notifications.update_folder().strip()
    if not configured and os.environ.get("LABLOGVIEWER_NO_UPDATE_CHECK"):
        return ""
    return configured or DEFAULT_FEED_URL

