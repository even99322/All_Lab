"""Display-name and persistent-identity helpers for Labber logs.

The filename is the operator-controlled name of a log. Labber's embedded
``log_name`` attribute can be older than a renamed file, so it remains useful
metadata but must not silently replace the filename in the user interface.
Persistent state uses the resolved source path instead of either display name.
"""

from __future__ import annotations

from pathlib import Path


def display_name_for_source(source_path: str | Path, metadata_log_name: object = None) -> str:
    """Return the complete human-facing filename stem when it is available."""
    stem = Path(source_path).stem.strip() if source_path else ""
    if stem:
        return stem
    metadata = str(metadata_log_name or "").strip()
    return metadata or "(unnamed)"


_NETWORK_IDENTITIES: dict[str, str] = {}
_NETWORK_FOLDERS: dict[str, str] = {}
_NETWORK_LIMIT = 50000


def _remember(cache: dict[str, str], key: str, value: str) -> None:
    if len(cache) >= _NETWORK_LIMIT:
        cache.clear()
    cache[key] = value


def _known_folder(folder: Path) -> str | None:
    """The resolved form of ``folder`` from it or the nearest resolved folder above it (no server call)."""
    import os

    rest: list[str] = []
    current = folder
    while True:
        known = _NETWORK_FOLDERS.get(str(current))
        if known is not None:
            return os.path.join(known, *reversed(rest)) if rest else known
        if current.parent == current or len(rest) > 64:
            return None
        rest.append(current.name)
        current = current.parent


def remember_network_root(root: str | Path, resolved: str | None = None) -> None:
    """Remember a database root on a network drive as resolved (``resolved`` when the caller already
    has it); files below it then get their identity without asking the server."""
    try:
        from app.core.net_cache import is_network_path

        if not is_network_path(str(root)):
            return
        path = Path(root).expanduser()
        if _NETWORK_FOLDERS.get(str(path)) is None:
            _remember(_NETWORK_FOLDERS, str(path), resolved or str(path.resolve()))
    except Exception:
        pass


def stable_data_identity(source_path: str | Path) -> str:
    """Return a resolved source key, never a mutable display name.

    v1.0.2: resolving a path on a network drive asks the server (each folder of the path over VPN),
    and the Browser does it several times per click — the answer for the same path string is
    remembered for the session.
    v1.0.4: on a network drive only the folder is resolved — or, below an already resolved folder such
    as the database root, nothing at all — and the file name is joined to it, so clicking in a newly
    opened folder does not wait for the server."""
    import os

    text = str(source_path)
    known = _NETWORK_IDENTITIES.get(text)
    if known is not None:
        return known
    try:
        from app.core.net_cache import is_network_path

        network = is_network_path(text)
    except Exception:
        network = False
    if not network:
        return str(Path(source_path).expanduser().resolve())
    path = Path(source_path).expanduser()
    folder_text, name = str(path.parent), path.name
    folder = _known_folder(path.parent)
    if folder is None:
        folder = str(path.parent.resolve())
        _remember(_NETWORK_FOLDERS, folder_text, folder)
    resolved = os.path.join(folder, name) if name else folder
    _remember(_NETWORK_IDENTITIES, text, resolved)
    return resolved
