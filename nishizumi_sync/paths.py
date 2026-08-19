"""Filesystem locations used by the application.

The application is distributed both as a one-file PyInstaller executable and as
a plain Python package.  Historically every data file was written next to the
script, which breaks as soon as the program lives in a read-only location such
as ``C:\\Program Files``.  ``data_dir()`` keeps the portable behaviour when the
install directory is writable and falls back to the per-user data directory
otherwise.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from . import APP_SLUG

#: Environment variable used to force a specific data directory.
HOME_ENV_VAR = "NISHIZUMI_SYNC_HOME"

CONFIG_FILENAME = "user_config.json"
MAPPING_FILENAME = "custom_car_mapping.json"
UPDATE_STATE_FILENAME = "update_state.json"
LOG_FILENAME = "nishizumi_setups_sync.log"
PLUGINS_DIRNAME = "plugins"


def is_frozen() -> bool:
    """True when running from a PyInstaller (or similar) bundle."""
    return bool(getattr(sys, "frozen", False))


def install_dir() -> Path:
    """Directory holding the executable (frozen) or the package (source)."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def bundle_dir() -> Path:
    """Directory holding read-only bundled resources such as ``icon.png``."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return install_dir()


def user_data_dir() -> Path:
    """Per-user writable directory following platform conventions."""
    if sys.platform.startswith("win"):
        root = os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming"
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        root = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(root) / APP_SLUG


def _is_writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    probe = path / ".write-test"
    try:
        probe.touch()
        probe.unlink()
    except OSError:
        return False
    return True


def data_dir() -> Path:
    """Directory used for configuration, mapping and log files.

    Resolution order: ``NISHIZUMI_SYNC_HOME`` → install directory (portable) →
    per-user data directory.
    """
    override = os.environ.get(HOME_ENV_VAR)
    if override:
        path = Path(override).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        return path

    portable = install_dir()
    if _is_writable(portable):
        return portable

    fallback = user_data_dir()
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def config_file() -> Path:
    return data_dir() / CONFIG_FILENAME


def mapping_file() -> Path:
    return data_dir() / MAPPING_FILENAME


def update_state_file() -> Path:
    return data_dir() / UPDATE_STATE_FILENAME


def default_log_file() -> Path:
    return data_dir() / LOG_FILENAME


def plugins_dir() -> Path:
    return data_dir() / PLUGINS_DIRNAME


def icon_file() -> Path | None:
    for candidate in (bundle_dir() / "icon.png", install_dir() / "icon.png"):
        if candidate.is_file():
            return candidate
    return None
