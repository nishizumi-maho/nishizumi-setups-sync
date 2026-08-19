#!/usr/bin/env python3
"""Build a standalone executable with PyInstaller.

Used by the release workflow and usable locally::

    python scripts/build_exe.py

The produced file is named so the in-app updater can recognise it:
``NishizumiSync-windows.exe``, ``NishizumiSync-linux`` or ``NishizumiSync-macos``.
"""

from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENTRY_POINT = ROOT / "nishizumi_setups_sync.py"
ICON_PNG = ROOT / "icon.png"


def platform_suffix() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def make_icon() -> Path | None:
    """Convert icon.png to the format PyInstaller wants on this platform."""
    if not ICON_PNG.is_file():
        return None
    if sys.platform.startswith("win"):
        target = ROOT / "build" / "icon.ico"
    elif sys.platform == "darwin":
        target = ROOT / "build" / "icon.icns"
    else:
        return None
    try:
        from PIL import Image
    except ImportError:
        print("Pillow is not installed; building without a custom icon")
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    image = Image.open(ICON_PNG).convert("RGBA")
    if target.suffix == ".ico":
        image.save(target, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    else:
        image.save(target)
    return target


def build(name: str | None = None, *, clean: bool = True) -> Path:
    output_name = name or f"NishizumiSync-{platform_suffix()}"
    if clean:
        for folder in (ROOT / "build", ROOT / "dist"):
            shutil.rmtree(folder, ignore_errors=True)

    separator = ";" if sys.platform.startswith("win") else ":"
    command = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--noconfirm",
        "--clean",
        "--name", output_name,
        "--add-data", f"{ICON_PNG}{separator}.",
        "--collect-submodules", "nishizumi_sync",
    ]
    if sys.platform.startswith("win") or sys.platform == "darwin":
        command.append("--windowed")
    icon = make_icon()
    if icon:
        command += ["--icon", str(icon)]
    command.append(str(ENTRY_POINT))

    print("Running:", " ".join(command))
    subprocess.run(command, check=True, cwd=ROOT)

    produced = ROOT / "dist" / (output_name + (".exe" if sys.platform.startswith("win") else ""))
    if not produced.exists():
        raise SystemExit(f"PyInstaller did not produce {produced}")
    print(f"Built {produced} ({produced.stat().st_size / 1024 / 1024:.1f} MB)")
    return produced


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", help="override the output file name")
    parser.add_argument("--no-clean", action="store_true", help="keep previous build artefacts")
    args = parser.parse_args()
    print(f"Building on {platform.platform()} with Python {sys.version.split()[0]}")
    build(args.name, clean=not args.no_clean)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
