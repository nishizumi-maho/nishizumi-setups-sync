"""Optional user hooks executed around a sync.

Drop a ``before_sync.py`` or ``after_sync.py`` file exposing ``execute(config,
logger)`` into the ``plugins`` folder next to the configuration file.  Hooks are
opt-in (``enable_plugins``) because they run arbitrary code.
"""

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
from typing import Any

from .paths import plugins_dir

HOOK_NAMES = ("before_sync", "after_sync")


def available_hooks(directory: Path | None = None) -> list[str]:
    """Names of the hooks that exist on disk."""
    folder = directory or plugins_dir()
    if not folder.is_dir():
        return []
    return [name for name in HOOK_NAMES if (folder / f"{name}.py").is_file()]


def run_hook(
    name: str,
    cfg: dict[str, Any],
    logger: logging.Logger,
    *,
    enabled: bool = False,
    directory: Path | None = None,
) -> bool:
    """Execute ``<name>.py`` if present.  Returns True when it ran."""
    if not enabled:
        return False
    if name not in HOOK_NAMES:
        raise ValueError(f"Unknown hook '{name}'")

    folder = directory or plugins_dir()
    script = folder / f"{name}.py"
    if not script.is_file():
        return False

    try:
        spec = importlib.util.spec_from_file_location(f"nishizumi_plugins.{name}", script)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load '{script}'")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        entry = getattr(module, "execute", None)
        if entry is None:
            logger.warning("Plugin '%s' has no execute(config, logger) function", script)
            return False
        logger.info("Running plugin hook '%s'", name)
        entry(cfg, logger)
        return True
    except Exception as exc:
        logger.error("Plugin hook '%s' failed: %s", name, exc)
        return False
