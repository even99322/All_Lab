"""Packaging step: compile app/_guard to binary modules in a BUILD COPY.

    python build_guard.py <build copy of LabLogViewer>

The public key from trusted_key.py is first written into license.py, then each
app/_guard/*.py (except __init__.py) is compiled with Cython into a native
extension (.so / .pyd) and the .py sources, including trusted_key.py, are
removed from the build copy. A shared copy then has no readable lock code and
no key file that could be swapped for another key.

Refuses to run on the development folder itself (sources would be deleted).
Needs Cython and a C compiler (Xcode Command Line Tools / MSVC Build Tools);
run it on each target platform (macOS Intel, macOS Apple silicon, Windows).
Nuitka users can instead pass --include-package=app._guard with the module
compiled the same way; the rule stays: no _guard/*.py in the shipped build.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

KEEP_AS_SOURCE = {"__init__.py", "trusted_key.py"}      # trusted_key.py is embedded, then deleted
DEVELOPMENT = Path(__file__).resolve().parent
# developer-only material that must never be inside a shipped build
FORBIDDEN_NAMES = {"LabLogViewer_Plugins", "LabLogViewer_DevTools", "DevTools", "_sandbox"}
FORBIDDEN_SUFFIXES = {".llvdev", ".llvkey"}          # developer key; a licence belongs to one person


def forbidden_content(build_root: str | Path) -> list[Path]:
    """Plugin folders, sandbox and developer key files found inside a build copy."""
    found = []
    for path in Path(build_root).rglob("*"):
        if path.name in FORBIDDEN_NAMES or path.suffix.lower() in FORBIDDEN_SUFFIXES:
            found.append(path)
    return found


def trusted_keys_in(text: str) -> list[str]:
    """Every public key in trusted_key.py: PUBLIC_KEY_HEX first, then ADDITIONAL_KEYS."""
    first = re.search(r'^PUBLIC_KEY_HEX = "([0-9a-f]{64})"$', text, re.M)
    keys = [first.group(1)] if first else []
    extra = text.split("ADDITIONAL_KEYS", 1)[1] if "ADDITIONAL_KEYS" in text else ""
    for key in re.findall(r'"([0-9a-f]{64})"', extra):
        if key not in keys:
            keys.append(key)
    return keys


def compile_guard(build_root: str | Path) -> list[Path]:
    build_root = Path(build_root).resolve()
    guard = build_root / "app" / "_guard"
    if build_root == DEVELOPMENT:
        raise SystemExit("Give a build COPY, not the development folder.")
    if not guard.is_dir():
        raise SystemExit(f"{guard} not found.")
    leaked = forbidden_content(build_root)
    if leaked:
        raise SystemExit("Developer-only files must not be packaged:\n" + "\n".join(map(str, leaked)))
    key_text = (guard / "trusted_key.py").read_text(encoding="utf-8")
    keys = trusted_keys_in(key_text)
    if not keys:
        raise SystemExit("No public key installed: run LabLogViewer_Plugins/DevTools > Install public key first.")
    license_py = guard / "license.py"
    text, count = re.subn(r'^_EMBEDDED_KEY = ""$', f'_EMBEDDED_KEY = "{",".join(keys)}"',
                          license_py.read_text(encoding="utf-8"), flags=re.M)
    if count != 1:
        raise SystemExit("license.py has no _EMBEDDED_KEY line.")
    license_py.write_text(text, encoding="utf-8")
    sources = sorted(p for p in guard.glob("*.py") if p.name not in KEEP_AS_SOURCE)
    built: list[Path] = []
    with tempfile.TemporaryDirectory() as work:
        setup = Path(work) / "setup.py"
        setup.write_text(
            "from setuptools import setup\nfrom Cython.Build import cythonize\n"
            f"setup(ext_modules=cythonize({[str(p) for p in sources]!r}, "
            "compiler_directives={'language_level': 3, 'embedsignature': False}, quiet=True))\n",
            encoding="utf-8")
        subprocess.run([sys.executable, str(setup), "build_ext", "--build-lib", str(Path(work) / "lib"),
                        "--build-temp", str(Path(work) / "tmp")], check=True, cwd=work)
        for source in sources:
            found = [p for p in (Path(work) / "lib").rglob(f"{source.stem}.*") if p.suffix in (".so", ".pyd")]
            if not found:
                raise SystemExit(f"Compiling {source.name} failed.")
            target = guard / found[0].name
            shutil.copy2(found[0], target)
            built.append(target)
    for source in sources:
        source.unlink()
        source.with_suffix(".c").unlink(missing_ok=True)
    (guard / "trusted_key.py").unlink()
    shutil.rmtree(guard / "__pycache__", ignore_errors=True)
    return built


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    for path in compile_guard(sys.argv[1]):
        print("built", path.name)
