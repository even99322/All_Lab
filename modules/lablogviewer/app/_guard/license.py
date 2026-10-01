"""Licence files (.llvkey): signed by the developer, checked with the public key.

File (JSON):
    {"format": "llv-license-1", "payload": <base64 JSON>, "signature": <base64 Ed25519>}
Payload:
    {"id", "holder", "machine" (LLV-...), "features" [...], "issued", "expires"}  (dates: YYYY-MM-DD)

A licence is valid only on the machine it names, only until its expiry date,
and only if the signature matches the public key in trusted_key.py.
Installed licences are copied to <data folder>/licenses/.
"""

from __future__ import annotations

import base64
import binascii
import json
import shutil
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

FORMAT = "llv-license-1"
SUFFIX = ".llvkey"
FEATURES = ("personal_colors", "relink", "migrate")
MAX_FILE = 16 * 1024


class LicenseError(ValueError):
    """Reason is one of: format, signature, machine, expired, no_key, clock."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(detail or reason)
        self.reason = reason


@dataclass(frozen=True)
class License:
    id: str
    holder: str
    machine: str
    features: tuple[str, ...]
    issued: date
    expires: date
    path: Path | None = None

    def days_left(self, today: date) -> int:
        return (self.expires - today).days


def canonical(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


# Filled in by build_guard.py when packaging (then trusted_key.py is not shipped):
# every trusted key, comma separated.
_EMBEDDED_KEY = ""


_key_file_stamp: float | None = None


def _installed_key_hex() -> str:
    """The first issuer's key (see _trusted_hex_keys for all of them)."""
    keys = _trusted_hex_keys()
    return keys[0] if keys else ""


def _trusted_hex_keys() -> list[str]:
    """Every trusted key: embedded when packaged, else trusted_key.py, read again when
    DevTools has rewritten the file (a key installed while LabLogViewer is open works
    without a restart)."""
    if _EMBEDDED_KEY:
        return [k for k in (part.strip() for part in _EMBEDDED_KEY.split(",")) if len(k) == 64]
    global _key_file_stamp
    import importlib
    import os

    from app._guard import trusted_key

    try:
        stamp = os.path.getmtime(trusted_key.__file__)
    except (OSError, TypeError):
        stamp = None
    if _key_file_stamp is None:
        _key_file_stamp = stamp
    elif stamp is not None and stamp != _key_file_stamp:
        _key_file_stamp = stamp
        trusted_key = importlib.reload(trusted_key)
    keys = [str(getattr(trusted_key, "PUBLIC_KEY_HEX", "")).strip()]
    extra = getattr(trusted_key, "ADDITIONAL_KEYS", {})
    if isinstance(extra, dict):
        keys += [str(value).strip() for value in extra.values()]
    seen: list[str] = []
    for key in keys:
        if len(key) == 64 and key not in seen:
            seen.append(key)
    return seen


def _public_keys() -> list:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    keys = []
    for hex_key in _trusted_hex_keys():
        try:
            keys.append(Ed25519PublicKey.from_public_bytes(bytes.fromhex(hex_key)))
        except ValueError:
            continue
    return keys


def _public_key():
    keys = _public_keys()
    return keys[0] if keys else None


def key_status() -> tuple[bool, str]:
    """(a public key is installed, the short fingerprints) for the Licences window."""
    keys = [k for k in _trusted_hex_keys()]
    if not _public_keys():
        return False, ""
    return True, ", ".join(f"{k[:4]}…{k[-4:]}" for k in keys)


def licenses_dir() -> Path:
    from app.core.data_location import data_root

    return data_root() / "licenses"


def _today(now: float | None) -> date:
    from app._guard import experience

    return datetime.fromtimestamp(experience.trusted_now(now)).date()


def parse(raw: bytes, *, machine: str | None = None, now: float | None = None, path: Path | None = None) -> License:
    """Verify ``raw`` completely; raises LicenseError with a reason."""
    from cryptography.exceptions import InvalidSignature

    from app._guard.machine import machine_code, normalize

    if len(raw) > MAX_FILE:
        raise LicenseError("format")
    try:
        document = json.loads(raw.decode("utf-8"))
        if document.get("format") != FORMAT:
            raise LicenseError("format")
        payload_bytes = base64.b64decode(document["payload"], validate=True)
        signature = base64.b64decode(document["signature"], validate=True)
    except (ValueError, KeyError, TypeError, AttributeError, binascii.Error, UnicodeDecodeError) as error:
        if isinstance(error, LicenseError):
            raise
        raise LicenseError("format") from None
    keys = _public_keys()
    if not keys:
        raise LicenseError("no_key")
    for key in keys:                                   # any trusted issuer
        try:
            key.verify(signature, payload_bytes)
            break
        except InvalidSignature:
            continue
    else:
        raise LicenseError("signature")
    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
        if canonical(payload) != payload_bytes:
            raise LicenseError("format")
        result = License(
            id=str(payload["id"]), holder=str(payload.get("holder", "")),
            machine=normalize(payload["machine"]),
            features=tuple(sorted(str(f) for f in payload["features"] if str(f) in FEATURES)),
            issued=date.fromisoformat(payload["issued"]), expires=date.fromisoformat(payload["expires"]),
            path=path)
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        if isinstance(error, LicenseError):
            raise
        raise LicenseError("format") from None
    if result.machine != normalize(machine or machine_code()):
        raise LicenseError("machine")
    today = _today(now)
    if today > result.expires:
        raise LicenseError("expired")
    if today < result.issued:
        # Issued "in the future": this computer's clock is behind the developer's.
        raise LicenseError("clock")
    return result


def read(path: str | Path, **kwargs) -> License:
    path = Path(path)
    try:
        if path.stat().st_size > MAX_FILE:
            raise LicenseError("format")
        raw = path.read_bytes()
    except OSError:
        raise LicenseError("format") from None
    return parse(raw, path=path, **kwargs)


def install(path: str | Path, **kwargs) -> License:
    """Verify, then copy into the licences folder. Raises LicenseError."""
    found = read(path, **kwargs)
    folder = licenses_dir()
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{found.id}{SUFFIX}"
    if Path(path).resolve() != target.resolve():
        shutil.copyfile(path, target)
    return License(**{**found.__dict__, "path": target})


def installed(**kwargs) -> list[License]:
    """Every installed licence that is valid right now."""
    folder = licenses_dir()
    valid = []
    if folder.is_dir():
        for path in sorted(folder.glob(f"*{SUFFIX}")):
            try:
                valid.append(read(path, **kwargs))
            except LicenseError:
                continue
    return valid


def all_installed(**kwargs) -> list[tuple[Path, License | None, str | None]]:
    """(path, licence or None, error reason or None) for the licence list in Settings."""
    folder = licenses_dir()
    rows = []
    if folder.is_dir():
        for path in sorted(folder.glob(f"*{SUFFIX}")):
            try:
                rows.append((path, read(path, **kwargs), None))
            except LicenseError as error:
                rows.append((path, None, error.reason))
    return rows


def remove(path: str | Path) -> None:
    path = Path(path)
    if path.parent.resolve() == licenses_dir().resolve() and path.suffix == SUFFIX:
        path.unlink(missing_ok=True)


def feature_granted(feature: str, **kwargs) -> License | None:
    for found in installed(**kwargs):
        if feature in found.features:
            return found
    return None
