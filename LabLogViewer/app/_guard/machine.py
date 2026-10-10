"""Machine code: a short, stable name for this computer (used to bind licences).

Derived from the operating system's hardware / installation id; the raw id is
never shown or stored, only a hash of it:

    macOS    IOPlatformUUID (ioreg)
    Windows  HKLM\\SOFTWARE\\Microsoft\\Cryptography\\MachineGuid
    Linux    /etc/machine-id (or /var/lib/dbus/machine-id)

Format: LLV-XXXX-XXXX-XXXX-XXXX (base32, no 0/1/8/O/I/B look-alikes).
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

_ALPHABET = "ACDEFGHJKLMNPQRSTUVWXYZ2345679"
_SALT = b"LabLogViewer machine code v1"
PATTERN = re.compile(r"^LLV(-[ACDEFGHJKLMNPQRSTUVWXYZ2-79]{4}){4}$")


def _raw_id() -> str:
    try:
        if sys.platform == "darwin":
            output = subprocess.run(["/usr/sbin/ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                                    capture_output=True, text=True, timeout=5).stdout
            found = re.search(r'"IOPlatformUUID"\s*=\s*"([^"]+)"', output)
            if found:
                return found.group(1)
        elif sys.platform.startswith("win"):
            import winreg

            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography", 0,
                                 winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0))
            return str(winreg.QueryValueEx(key, "MachineGuid")[0])
        else:
            for candidate in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
                path = Path(candidate)
                if path.is_file() and path.read_text().strip():
                    return path.read_text().strip()
    except Exception:
        pass
    import uuid

    return f"fallback-{uuid.getnode():012x}"


def format_code(digest: bytes) -> str:
    number = int.from_bytes(digest[:12], "big")
    characters = []
    for _ in range(16):
        number, index = divmod(number, len(_ALPHABET))
        characters.append(_ALPHABET[index])
    groups = ["".join(characters[i:i + 4]) for i in range(0, 16, 4)]
    return "LLV-" + "-".join(groups)


@lru_cache(maxsize=1)
def machine_code() -> str:
    return format_code(hashlib.sha256(_SALT + _raw_id().strip().lower().encode("utf-8")).digest())


def normalize(code: str) -> str:
    return re.sub(r"\s+", "", str(code)).upper()


def is_machine_code(code: str) -> bool:
    return bool(PATTERN.match(normalize(code)))
