"""Step 4 (macOS): fuse an Apple silicon build and an Intel build into one universal app.

    python packaging/merge_universal.py <arm64 .app> <x86_64 .app> <output .app>

Both builds come from the same source and the same pinned package versions, so they
contain the same files; only compiled code (Mach-O files) differs. Every Mach-O file is
joined with ``lipo -create``; everything else is taken from the arm64 build. The result
is signed ad hoc (Apple silicon refuses unsigned code) and checked: every Mach-O file
must contain both architectures.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import re

# Programs that ship one file per chip (each chip starts its own); they are not merged.
PER_CHIP = re.compile(r"imageio_ffmpeg/binaries/ffmpeg-macos-(x86_64|aarch64|arm64)")
MAGICS = {b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca"}


def is_macho(path: Path) -> bool:
    try:
        with path.open("rb") as stream:
            return stream.read(4) in MAGICS
    except OSError:
        return False


def archs(path: Path) -> set[str]:
    result = subprocess.run(["lipo", "-archs", str(path)], capture_output=True, text=True)
    return set(result.stdout.split()) if result.returncode == 0 else set()


def merge(arm: Path, intel: Path, out: Path) -> dict:
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(arm, out, symlinks=True)
    joined, arm_only, copied_intel = 0, [], []
    for path in sorted(out.rglob("*")):
        if path.is_symlink() or not path.is_file() or not is_macho(path):
            continue
        relative = path.relative_to(out)
        other = intel / relative
        have = archs(path)
        if {"arm64", "x86_64"} <= have:
            continue
        if other.is_file() and not other.is_symlink() and is_macho(other):
            if archs(other) & have:
                continue                                  # the same slice twice: keep one
            subprocess.run(["lipo", "-create", str(path), str(other), "-output", str(path)], check=True)
            joined += 1
        else:
            arm_only.append(str(relative))
    for path in sorted(intel.rglob("*")):                 # compiled files only the Intel build has
        relative = path.relative_to(intel)
        target = out / relative
        if not target.exists() and not target.is_symlink():
            target.parent.mkdir(parents=True, exist_ok=True)
            if path.is_symlink():
                target.symlink_to(path.readlink())
            elif path.is_file():
                shutil.copy2(path, target)
                copied_intel.append(str(relative))
    subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(out)], check=True)
    missing = []
    for path in out.rglob("*"):
        if path.is_file() and not path.is_symlink() and is_macho(path):
            have = archs(path)
            if not {"arm64", "x86_64"} <= have and not PER_CHIP.search(path.relative_to(out).as_posix()):
                missing.append(f"{path.relative_to(out)}: {' '.join(sorted(have)) or '?'}")
    return {"joined": joined, "arm_only": arm_only, "intel_only": copied_intel, "not_universal": missing}


if __name__ == "__main__":
    arm_app, intel_app, out_app = map(Path, sys.argv[1:4])
    report = merge(arm_app, intel_app, out_app)
    print(f"Joined {report['joined']} compiled files; files only in the Intel build: {len(report['intel_only'])}")
    for line in report["not_universal"]:
        print("NOT UNIVERSAL:", line)
    sys.exit(1 if report["not_universal"] else 0)
