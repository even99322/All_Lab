"""Tell the operating system that ``labbrowser://...`` (etc.) links belong to a program.

One small interface, one implementation per system. Every real system call goes through an
injectable backend so tests never touch the real registry, files or ``xdg-mime``.
Registration only affects the current user and is done once (installer or first start).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Callable, Sequence


def launch_command(executable: str | None = None, script: str | None = None) -> list[str]:
    """The command that starts this program: its packaged file, or ``python main.py``."""
    if getattr(sys, "frozen", False) or script is None:
        return [executable or sys.executable]
    return [executable or sys.executable, script]


def plist_entry(scheme: str, name: str) -> dict:
    """The macOS ``CFBundleURLTypes`` item to put in Info.plist when the app is built."""
    return {"CFBundleURLName": name, "CFBundleURLSchemes": [scheme]}


class Registrar:
    """register()/is_registered()/unregister() for one scheme. register() returns True on success."""

    def register(self, scheme: str, name: str, command: Sequence[str]) -> bool:
        raise NotImplementedError

    def is_registered(self, scheme: str) -> bool:
        raise NotImplementedError

    def unregister(self, scheme: str) -> bool:
        raise NotImplementedError


class WindowsRegistrar(Registrar):
    """HKEY_CURRENT_USER\\Software\\Classes\\<scheme> (no administrator rights needed)."""

    def __init__(self, registry=None):
        self._registry = registry          # object with set_value(path, value, name=None), has_key, delete_tree

    def _backend(self):
        if self._registry is None:
            self._registry = _WinRegistry()
        return self._registry

    def register(self, scheme: str, name: str, command: Sequence[str]) -> bool:
        base = f"Software\\Classes\\{scheme}"
        line = " ".join(f'"{part}"' for part in command) + ' "%1"'
        registry = self._backend()
        registry.set_value(base, f"URL:{name}")
        registry.set_value(base, "", "URL Protocol")
        registry.set_value(base + "\\shell\\open\\command", line)
        return True

    def is_registered(self, scheme: str) -> bool:
        return self._backend().has_key(f"Software\\Classes\\{scheme}\\shell\\open\\command")

    def unregister(self, scheme: str) -> bool:
        self._backend().delete_tree(f"Software\\Classes\\{scheme}")
        return True


class _WinRegistry:
    def __init__(self):
        import winreg

        self._w = winreg

    def set_value(self, path: str, value: str, name: str | None = None) -> None:
        with self._w.CreateKey(self._w.HKEY_CURRENT_USER, path) as key:
            self._w.SetValueEx(key, name or "", 0, self._w.REG_SZ, value)

    def has_key(self, path: str) -> bool:
        try:
            self._w.CloseKey(self._w.OpenKey(self._w.HKEY_CURRENT_USER, path))
            return True
        except OSError:
            return False

    def delete_tree(self, path: str) -> None:
        def walk(sub: str) -> None:
            try:
                with self._w.OpenKey(self._w.HKEY_CURRENT_USER, sub) as key:
                    names = []
                    index = 0
                    while True:
                        try:
                            names.append(self._w.EnumKey(key, index))
                            index += 1
                        except OSError:
                            break
                for child in names:
                    walk(sub + "\\" + child)
                self._w.DeleteKey(self._w.HKEY_CURRENT_USER, sub)
            except OSError:
                pass

        walk(path)


class MacRegistrar(Registrar):
    """macOS reads the scheme from the app bundle's Info.plist (see plist_entry), not at run time.

    Registering from inside a running program is not possible, so register() reports False and
    the packaging step must add plist_entry(). ``is_registered`` asks Launch Services when the
    injected ``query`` callable is given.
    """

    def __init__(self, query: Callable[[str], bool] | None = None):
        self._query = query

    def register(self, scheme: str, name: str, command: Sequence[str]) -> bool:
        return False

    def is_registered(self, scheme: str) -> bool:
        return bool(self._query(scheme)) if self._query else False

    def unregister(self, scheme: str) -> bool:
        return False


class LinuxRegistrar(Registrar):
    """A .desktop file in ~/.local/share/applications plus ``xdg-mime default``."""

    def __init__(self, applications: Path | None = None, run: Callable[[list[str]], int] | None = None):
        self._dir = applications or Path.home() / ".local" / "share" / "applications"
        self._run = run or (lambda args: subprocess.run(args, check=False, capture_output=True).returncode)

    def _file(self, scheme: str) -> Path:
        return self._dir / f"{scheme}-handler.desktop"

    def register(self, scheme: str, name: str, command: Sequence[str]) -> bool:
        self._dir.mkdir(parents=True, exist_ok=True)
        line = " ".join(f'"{part}"' for part in command) + " %u"
        self._file(scheme).write_text(
            f"[Desktop Entry]\nType=Application\nName={name}\nExec={line}\nNoDisplay=true\n"
            f"MimeType=x-scheme-handler/{scheme};\n", encoding="utf-8")
        self._run(["xdg-mime", "default", self._file(scheme).name, f"x-scheme-handler/{scheme}"])
        return True

    def is_registered(self, scheme: str) -> bool:
        return self._file(scheme).is_file()

    def unregister(self, scheme: str) -> bool:
        self._file(scheme).unlink(missing_ok=True)
        return True


def current_registrar() -> Registrar:
    if sys.platform == "win32":
        return WindowsRegistrar()
    if sys.platform == "darwin":
        return MacRegistrar()
    return LinuxRegistrar()
