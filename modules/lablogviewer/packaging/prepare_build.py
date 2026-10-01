"""Step 1 of packaging: a clean build copy with the licence code compiled.

    python packaging/prepare_build.py [build folder]      (default: build/src)

Copies only what the program needs (app, help, icons, main.py), refuses developer-only
material (plugins, sandbox, key and licence files, measurement data), then compiles
app/_guard to native modules with build_guard.py (the public key is embedded and the
readable lock code removed). Runs on the platform being built (macOS Intel, Apple
silicon, Windows): the compiled modules are platform specific.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
INCLUDE = ["app", "help", "icons", "main.py"]
SKIP = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store", "*.hdf5", "*.h5", "*.llvkey", "*.llvdev")


def prepare(out: Path) -> Path:
    import build_guard

    out = out.resolve()
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for name in INCLUDE:
        source = ROOT / name
        if source.is_dir():
            shutil.copytree(source, out / name, ignore=SKIP)
        else:
            shutil.copy2(source, out / name)
    data = [p for p in out.rglob("*") if p.suffix.lower() in {".hdf5", ".h5"}]
    if data:
        raise SystemExit("Measurement data must not be packaged:\n" + "\n".join(map(str, data)))
    built = build_guard.compile_guard(out)
    print(f"Build copy ready: {out} ({len(built)} compiled licence modules)")
    return out


if __name__ == "__main__":
    prepare(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "build" / "src")
