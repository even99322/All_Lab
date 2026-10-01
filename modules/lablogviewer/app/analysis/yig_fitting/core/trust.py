"""Trust check for Python model files (.py) before they are imported.

A model file is ordinary Python and runs with the user's permissions, so it
cannot be sandboxed without removing the "load any .py model" feature.
Instead:

1. Every file is scanned statically (never executed). Clearly dangerous code
   (process / file-system / network / reflection escapes such as
   ``subprocess``, ``os``, ``socket``, ``eval``, ``exec``, ``__import__``,
   ``np.load``, dunder attributes) is BLOCKED: the file is never loaded and
   cannot be trusted.
2. Otherwise the file must be trusted before import. Trusted automatically:
   bundled models, Formula Builder files whose code is exactly what the
   builder generates from the spec inside them (their expressions pass the
   safe_expr whitelist), and files already used before v0.18D (library and
   sessions; recorded once). Anything else asks the user once; the answer is
   remembered by the file's SHA-256, so an edited file asks again.

Trusted hashes live in ``<data folder>/state/trusted_formulas.json``.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from app.core.external_state import atomic_write_json, default_state_path, load_json_state


BUNDLED_DIR = Path(__file__).resolve().parent.parent / "models"

SAFE_MODULES = {
    "numpy", "scipy", "math", "cmath", "__future__", "typing", "functools", "warnings", "numbers",
    "fractions", "decimal", "itertools", "dataclasses", "collections", "statistics", "operator",
}
BLOCKED_MODULES = {
    "os", "sys", "subprocess", "shutil", "socket", "ssl", "urllib", "urllib3", "http", "ftplib",
    "smtplib", "poplib", "imaplib", "telnetlib", "requests", "httpx", "aiohttp", "ctypes", "cffi",
    "importlib", "pickle", "cPickle", "marshal", "shelve", "dill", "joblib", "multiprocessing",
    "threading", "_thread", "concurrent", "asyncio", "signal", "pty", "pathlib", "glob", "tempfile",
    "io", "builtins", "runpy", "code", "codeop", "webbrowser", "pip", "setuptools", "inspect", "gc",
    "platform", "getpass", "pwd", "grp", "winreg", "_winapi", "msvcrt", "nt", "posix", "zipfile",
    "tarfile", "sqlite3", "xmlrpc", "socketserver", "select", "selectors", "mmap", "resource",
    "atexit", "sysconfig", "site", "zipimport", "pkgutil", "keyring", "paramiko",
}
BLOCKED_CALLS = {"eval", "exec", "compile", "__import__", "breakpoint", "globals", "locals", "vars",
                 "getattr", "setattr", "delattr", "memoryview"}
WARNING_CALLS = {"open", "input", "print", "exit", "quit", "help"}
# Attribute names that reach files, processes or code objects on any object.
BLOCKED_ATTRIBUTES = {
    "system", "popen", "spawn", "spawnv", "spawnl", "execv", "execl", "execve", "fork", "kill",
    "remove", "unlink", "rmtree", "rmdir", "rename", "replace", "chmod", "chown",
    "load", "loads", "save", "savez", "savez_compressed", "loadtxt", "savetxt", "genfromtxt",
    "fromfile", "tofile", "memmap", "fromregex", "DataSource", "ctypeslib", "f2py", "distutils",
    "npyio", "urlopen", "Request",
}
BLOCKED_NAMES = {"__builtins__", "__import__", "__loader__", "__spec__"}

_Prompt = Callable[[Path, "ScanReport"], bool]
_prompt: _Prompt | None = None


class UntrustedFormulaError(PermissionError):
    """The model file was not loaded (blocked, or not trusted by the user)."""


@dataclass
class ScanReport:
    blocked: list[tuple[int, str]] = field(default_factory=list)
    warnings: list[tuple[int, str]] = field(default_factory=list)

    def describe(self, entries=None) -> str:
        return "\n".join(f"Line {line}: {text}" for line, text in (entries or self.blocked + self.warnings))


# -- static scan ---------------------------------------------------------------
def read_source(path: str | os.PathLike) -> str:
    """Model file text decoded the way Python's import decodes it.

    UTF-8 by default; a ``# -*- coding: cp950 -*-`` / Big5 declaration or a
    UTF-8 BOM is honoured (Windows editors often save that way).
    """
    import tokenize

    with open(path, "rb") as stream:
        encoding, _lines = tokenize.detect_encoding(stream.readline)
        stream.seek(0)
        return stream.read().decode(encoding)


def scan_source(source: str) -> ScanReport:
    report = ScanReport()
    try:
        tree = ast.parse(source)
    except SyntaxError as error:
        report.blocked.append((error.lineno or 0, f"Python syntax error: {error.msg}"))
        return report
    for node in ast.walk(tree):
        line = getattr(node, "lineno", 0)
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [alias.name for alias in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            if isinstance(node, ast.ImportFrom) and node.level:
                report.warnings.append((line, "relative import"))
            for name in names:
                root = name.split(".")[0]
                if root in BLOCKED_MODULES:
                    report.blocked.append((line, f"imports '{name}' (system / file / network access)"))
                elif root not in SAFE_MODULES:
                    report.warnings.append((line, f"imports '{name}' (not a math library)"))
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name in BLOCKED_ATTRIBUTES or alias.name in BLOCKED_CALLS:
                        report.blocked.append((line, f"imports '{alias.name}' from '{node.module}'"))
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in BLOCKED_CALLS:
                report.blocked.append((line, f"calls {node.func.id}()"))
            elif node.func.id in WARNING_CALLS:
                report.warnings.append((line, f"calls {node.func.id}()"))
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("__") and node.attr.endswith("__"):
                report.blocked.append((line, f"uses internal attribute '{node.attr}'"))
            elif node.attr in BLOCKED_ATTRIBUTES:
                report.blocked.append((line, f"uses '.{node.attr}' (file / process access)"))
        elif isinstance(node, ast.Name) and node.id in BLOCKED_NAMES:
            report.blocked.append((line, f"uses '{node.id}'"))
    report.blocked.sort()
    report.warnings.sort()
    return report


# -- trust store -----------------------------------------------------------------
def store_path() -> Path:
    return default_state_path("trusted_formulas.json")


def _read_store() -> dict:
    data = load_json_state(store_path(), None).value
    if not isinstance(data, dict) or not isinstance(data.get("files"), dict):
        data = None
    return data


def _load_store() -> dict:
    data = _read_store()
    if data is None:
        data = {"version": 1, "files": {}}
        _seed_existing(data)
        _write_store(data)
    return data


def _write_store(data: dict) -> None:
    atomic_write_json(store_path(), data)


def file_hash(path: str | os.PathLike) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _record(data: dict, path: Path, digest: str, reason: str) -> None:
    data["files"][digest] = {"path": str(path), "reason": reason,
                             "trusted_at": datetime.now().isoformat(timespec="seconds")}


def trust_file(path: str | os.PathLike, reason: str = "user") -> None:
    path = Path(path)
    data = _load_store()
    _record(data, path, file_hash(path), reason)
    _write_store(data)


def _seed_existing(data: dict) -> None:
    """Once: trust model files already in use before v0.18D (library, sessions)."""
    from .paths import config_dir

    data["seeded"] = datetime.now().isoformat(timespec="seconds")
    root = Path(config_dir())
    candidates: set[Path] = set(root.rglob("*.py"))
    for document in root.rglob("*.json"):
        try:
            value = json.loads(document.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        stack = [value]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                stack.extend(item.values())
            elif isinstance(item, list):
                stack.extend(item)
            elif isinstance(item, str) and item.endswith(".py"):
                candidate = Path(item)
                candidates.add(candidate if candidate.is_absolute() else document.parent / candidate)
    for candidate in candidates:
        try:
            if candidate.is_file() and not scan_source(read_source(candidate)).blocked:
                _record(data, candidate, file_hash(candidate), "in use before v0.18D")
        except (OSError, UnicodeDecodeError, SyntaxError, LookupError):
            continue


# -- decisions -------------------------------------------------------------------
def _is_bundled(path: Path) -> bool:
    try:
        return path.resolve().parent == BUNDLED_DIR
    except OSError:
        return False


def _is_builder_output(text: str) -> bool:
    """The file is exactly what the Formula Builder generates from its own spec."""
    from .codegen import SPEC_TAG, generate_code

    for line in text.splitlines():
        if line.startswith(SPEC_TAG):
            try:
                spec = json.loads(line[len(SPEC_TAG):])
                return generate_code(spec).rstrip("\n") == text.rstrip("\n")
            except Exception:
                return False
    return False


def check(path: str | os.PathLike) -> tuple[bool, str, ScanReport]:
    """(trusted, reason, report). A blocked file is never trusted."""
    path = Path(path)
    text = read_source(path)
    report = scan_source(text)
    if report.blocked:
        return False, "blocked", report
    digest = file_hash(path)
    data = _load_store()
    if digest in data["files"]:
        return True, data["files"][digest].get("reason", "user"), report
    reason = "bundled" if _is_bundled(path) else "formula builder" if _is_builder_output(text) else None
    if reason is None:
        return False, "unknown", report
    # Remember the content so library copies of this file are trusted too.
    _record(data, path, digest, reason)
    _write_store(data)
    return True, reason, report


def set_prompt(prompt: _Prompt | None) -> None:
    """GUI hook: ask the user to trust an unknown (not blocked) file."""
    global _prompt
    _prompt = prompt


def require_trusted(path: str | os.PathLike) -> None:
    """Raise :class:`UntrustedFormulaError` unless ``path`` may be imported."""
    path = Path(path)
    trusted, _reason, report = check(path)
    if trusted:
        return
    if report.blocked:
        raise UntrustedFormulaError(
            f"{path.name} was not loaded: it contains code that can access files, programs or the "
            f"network, which a fit model never needs.\n\n{report.describe(report.blocked)}")
    if _prompt is not None and _prompt(path, report):
        trust_file(path, "user")
        return
    raise UntrustedFormulaError(f"{path.name} was not loaded because it is not trusted.")
