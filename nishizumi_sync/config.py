"""Configuration loading, migration and persistence.

Configuration lives in ``user_config.json``.  Older releases wrote several
different shapes of that file, so :func:`load_config` migrates legacy keys
instead of ignoring them.  Writes are atomic — the previous implementation
truncated the file in place and silently swallowed every error, which could
leave users with an empty configuration after a crash or a full disk.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from . import GITHUB_REPOSITORY
from .paths import config_file, default_log_file

#: Keys that are mirrored between the active profile and the flat config.
PROFILE_KEYS = ("team_folder", "personal_folder", "supplier_folder", "season_folder")

HASH_ALGORITHMS = ("md5", "sha256")
SOURCE_TYPES = ("zip", "folder", "none")
EXTRA_LOCATIONS = ("car", "dest")
UPDATE_CHANNELS = ("stable", "prerelease")

DEFAULT_UPDATE_SETTINGS: dict[str, Any] = {
    "check_on_startup": True,
    "check_interval_hours": 24,
    "auto_install": False,
    "channel": "stable",
    "repository": GITHUB_REPOSITORY,
}

DEFAULT_CONFIG: dict[str, Any] = {
    "iracing_folder": "",
    "source_type": "zip",
    "zip_file": "",
    "source_folder": "",
    "team_folder": "Example Team",
    "personal_folder": "My Personal Folder",
    "supplier_folder": "Example Supplier",
    "season_folder": "Example Season",
    "sync_source": "Example Source",
    "sync_destination": "Example Destination",
    "hash_algorithm": "md5",
    "copy_all": False,
    "delete_extras": True,
    "run_on_startup": False,
    "tray_mode": False,
    "tray_interval": 2,
    "use_external": False,
    "extra_folders": [],
    "backup_enabled": False,
    "backup_before_folder": "",
    "backup_after_folder": "",
    "enable_logging": False,
    "log_file": "",
    "log_level": "INFO",
    "use_driver_folders": False,
    "drivers": [],
    "use_garage61": False,
    "garage61_team_id": "",
    "garage61_api_key": "",
    "enable_plugins": False,
    "active_profile": 0,
    "profiles": [
        {
            "name": "Default",
            "team_folder": "Example Team",
            "personal_folder": "My Personal Folder",
            "supplier_folder": "Example Supplier",
            "season_folder": "Example Season",
        }
    ],
    "updates": dict(DEFAULT_UPDATE_SETTINGS),
}

#: Keys removed from the persisted file once migrated.
_LEGACY_KEYS = ("external_folder", "backup_folder", "driver_folder")


class ConfigError(Exception):
    """Raised when a configuration file cannot be read or written."""


def default_config() -> dict[str, Any]:
    """A fresh deep copy of the built-in defaults."""
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["log_file"] = str(default_log_file())
    return cfg


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------

def normalise_extra_folders(value: Any) -> list[dict[str, str]]:
    """Accept the historic string list and the current dict list alike."""
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, Iterable) or isinstance(value, (bytes, dict)):
        return []
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in value:
        if isinstance(item, str):
            name, location = item, "car"
        elif isinstance(item, dict):
            name = item.get("name") or item.get("folder") or ""
            location = item.get("location", "car")
        else:
            continue
        name = str(name).strip()
        if not name:
            continue
        location = location if location in EXTRA_LOCATIONS else "car"
        key = (name.lower(), location)
        if key in seen:
            continue
        seen.add(key)
        result.append({"name": name, "location": location})
    return result


def normalise_drivers(value: Any) -> list[str]:
    """Return a de-duplicated list of clean driver names, preserving order."""
    from .cars import clean_name

    if isinstance(value, str):
        value = [value]
    if not isinstance(value, Iterable) or isinstance(value, (bytes, dict)):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        name = clean_name(item if isinstance(item, str) else str(item or ""))
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        result.append(name)
    return result


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return default


def _coerce_int(value: Any, default: int, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    if minimum is not None:
        number = max(minimum, number)
    if maximum is not None:
        number = min(maximum, number)
    return number


def normalise_profiles(cfg: dict[str, Any]) -> None:
    """Ensure ``profiles``/``active_profile`` are consistent and in range."""
    profiles = cfg.get("profiles")
    if not isinstance(profiles, list):
        profiles = []
    cleaned: list[dict[str, Any]] = []
    for index, profile in enumerate(profiles):
        if not isinstance(profile, dict):
            continue
        entry = {key: str(profile.get(key, "") or "") for key in PROFILE_KEYS}
        entry["name"] = str(profile.get("name") or f"Profile {index + 1}")
        cleaned.append(entry)
    if not cleaned:
        cleaned = [
            {
                "name": "Default",
                **{key: str(cfg.get(key, DEFAULT_CONFIG.get(key, "")) or "") for key in PROFILE_KEYS},
            }
        ]
    cfg["profiles"] = cleaned
    cfg["active_profile"] = _coerce_int(cfg.get("active_profile", 0), 0, 0, len(cleaned) - 1)


def apply_active_profile(cfg: dict[str, Any]) -> dict[str, Any]:
    """Copy the active profile's folder names onto the flat config keys."""
    normalise_profiles(cfg)
    profile = cfg["profiles"][cfg["active_profile"]]
    for key in PROFILE_KEYS:
        cfg[key] = profile.get(key, cfg.get(key, ""))
    return cfg


def store_active_profile(cfg: dict[str, Any]) -> dict[str, Any]:
    """Copy the flat folder names back into the active profile."""
    normalise_profiles(cfg)
    profile = cfg["profiles"][cfg["active_profile"]]
    for key in PROFILE_KEYS:
        profile[key] = str(cfg.get(key, "") or "")
    return cfg


def normalise_updates(cfg: dict[str, Any]) -> None:
    raw = cfg.get("updates")
    updates = dict(DEFAULT_UPDATE_SETTINGS)
    if isinstance(raw, dict):
        updates.update({k: v for k, v in raw.items() if k in DEFAULT_UPDATE_SETTINGS})
    updates["check_on_startup"] = _coerce_bool(updates["check_on_startup"], True)
    updates["auto_install"] = _coerce_bool(updates["auto_install"], False)
    updates["check_interval_hours"] = _coerce_int(updates["check_interval_hours"], 24, 0, 24 * 30)
    if updates.get("channel") not in UPDATE_CHANNELS:
        updates["channel"] = "stable"
    repository = str(updates.get("repository") or "").strip()
    updates["repository"] = repository or GITHUB_REPOSITORY
    cfg["updates"] = updates


def migrate(data: dict[str, Any]) -> dict[str, Any]:
    """Upgrade a raw configuration mapping to the current schema."""
    cfg = default_config()
    if not isinstance(data, dict):
        return cfg

    data = dict(data)

    # ``driver_folder`` was renamed to ``supplier_folder``.
    if "supplier_folder" not in data and data.get("driver_folder"):
        data["supplier_folder"] = data["driver_folder"]
    # A single ``external_folder`` became a list of ``extra_folders``.
    if "extra_folders" not in data and data.get("external_folder"):
        data["extra_folders"] = [data["external_folder"]]
    # One backup folder became a before/after pair.
    if data.get("backup_folder"):
        data.setdefault("backup_before_folder", data["backup_folder"])

    for key in _LEGACY_KEYS:
        data.pop(key, None)

    cfg.update({k: v for k, v in data.items() if k in cfg or k == "updates"})

    cfg["extra_folders"] = normalise_extra_folders(cfg.get("extra_folders"))
    cfg["drivers"] = normalise_drivers(cfg.get("drivers"))

    if cfg.get("hash_algorithm") not in HASH_ALGORITHMS:
        cfg["hash_algorithm"] = "md5"
    if cfg.get("source_type") not in SOURCE_TYPES:
        cfg["source_type"] = "zip"
    from .logs import LEVELS

    if str(cfg.get("log_level", "")).upper() not in LEVELS:
        cfg["log_level"] = "INFO"
    else:
        cfg["log_level"] = str(cfg["log_level"]).upper()

    for key in (
        "copy_all",
        "delete_extras",
        "run_on_startup",
        "tray_mode",
        "use_external",
        "backup_enabled",
        "enable_logging",
        "use_driver_folders",
        "use_garage61",
        "enable_plugins",
    ):
        cfg[key] = _coerce_bool(cfg.get(key), bool(DEFAULT_CONFIG.get(key)))

    cfg["tray_interval"] = _coerce_int(cfg.get("tray_interval"), 2, 1, 24 * 7)

    for key in (
        "iracing_folder",
        "zip_file",
        "source_folder",
        "backup_before_folder",
        "backup_after_folder",
        "log_file",
        "garage61_team_id",
        "garage61_api_key",
    ):
        cfg[key] = str(cfg.get(key) or "").strip()
    if not cfg["log_file"]:
        cfg["log_file"] = str(default_log_file())

    normalise_updates(cfg)
    # ``profiles`` wins over the flat keys: the flat keys are only a view of the
    # active profile.  A config written before profiles existed keeps its values
    # because ``normalise_profiles`` seeds the first profile from them.
    if not isinstance(data.get("profiles"), list) or not data.get("profiles"):
        cfg["profiles"] = []
    apply_active_profile(cfg)
    return cfg


# ---------------------------------------------------------------------------
# Load / save
# ---------------------------------------------------------------------------

def load_config(
    path: str | os.PathLike[str] | None = None,
    *,
    logger: logging.Logger | None = None,
) -> dict[str, Any]:
    """Read the configuration file, migrating and validating its contents.

    A missing file yields the defaults.  An unreadable or corrupt file is moved
    aside (so the user does not lose it) and the defaults are returned.
    """
    target = Path(path) if path is not None else config_file()
    if not target.exists():
        return default_config()

    try:
        raw = target.read_text(encoding="utf-8")
        data = json.loads(raw) if raw.strip() else {}
    except (OSError, ValueError) as exc:
        if logger:
            logger.error("Could not read configuration '%s': %s", target, exc)
        _quarantine(target, logger)
        return default_config()

    if not isinstance(data, dict):
        if logger:
            logger.error("Configuration '%s' is not a JSON object; using defaults", target)
        _quarantine(target, logger)
        return default_config()

    return migrate(data)


def _quarantine(path: Path, logger: logging.Logger | None) -> None:
    """Move a broken configuration file aside so it is not silently lost."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = path.with_suffix(path.suffix + f".corrupt-{stamp}")
    try:
        path.replace(backup)
    except OSError:
        return
    if logger:
        logger.warning("Broken configuration moved to '%s'", backup)


def save_config(
    cfg: dict[str, Any],
    path: str | os.PathLike[str] | None = None,
    *,
    logger: logging.Logger | None = None,
) -> Path:
    """Persist the configuration atomically.

    Raises :class:`ConfigError` when the file cannot be written so callers can
    tell the user instead of pretending the save succeeded.
    """
    target = Path(path) if path is not None else config_file()
    data = prepare_for_save(cfg)

    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=str(target.parent), prefix=target.name, suffix=".tmp", delete=False
        ) as handle:
            tmp_path = handle.name
            json.dump(data, handle, ensure_ascii=False, indent=4, sort_keys=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, target)
        tmp_path = None
    except OSError as exc:
        raise ConfigError(f"Could not write configuration to '{target}': {exc}") from exc
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    if logger:
        logger.debug("Configuration saved to '%s'", target)
    return target


def prepare_for_save(cfg: dict[str, Any]) -> dict[str, Any]:
    """Return a cleaned copy of ``cfg`` ready to be serialised."""
    data = copy.deepcopy(dict(cfg))
    for key in _LEGACY_KEYS:
        data.pop(key, None)
    data["extra_folders"] = normalise_extra_folders(data.get("extra_folders"))
    data["drivers"] = normalise_drivers(data.get("drivers"))
    normalise_updates(data)
    store_active_profile(data)
    return data


def export_config(cfg: dict[str, Any], path: str | os.PathLike[str]) -> Path:
    """Write the configuration to an arbitrary location (GUI export)."""
    return save_config(cfg, path)
