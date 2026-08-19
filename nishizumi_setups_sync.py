#!/usr/bin/env python3
"""Nishizumi Setups Sync — launcher.

The application lives in the :mod:`nishizumi_sync` package; this file keeps the
historic entry point working (``python nishizumi_setups_sync.py run``) and is
what PyInstaller builds.  It also re-exports the handful of names that older
scripts imported from here.
"""

from __future__ import annotations

import logging
import os
import sys

# When frozen by PyInstaller the package is bundled; when run from a checkout we
# make sure this file's directory is importable even if the CWD is elsewhere.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nishizumi_sync import __version__  # noqa: E402
from nishizumi_sync.cli import main  # noqa: E402
from nishizumi_sync.config import load_config, save_config  # noqa: E402
from nishizumi_sync.logs import configure_from_config, get_logger  # noqa: E402
from nishizumi_sync.sync import run_sync  # noqa: E402

__all__ = [
    "__version__",
    "main",
    "load_config",
    "save_config",
    "run_sync",
    "run_silent",
    "Logger",
]


def Logger(cfg: dict | None = None) -> logging.Logger:  # noqa: N802 - 1.x compatibility
    """Backwards-compatible replacement for the old ``Logger`` class."""
    logger = get_logger()
    if cfg:
        configure_from_config(cfg, logger)
    return logger


def run_silent(cfg: dict, logger: logging.Logger | None = None, ask: bool = False, dry_run: bool = False):
    """Backwards-compatible wrapper around :func:`nishizumi_sync.sync.run_sync`.

    ``ask`` is accepted for compatibility; use the ``import`` command or the GUI
    to map unknown folders interactively.
    """
    return run_sync(cfg, logger or get_logger(), dry_run=dry_run)


if __name__ == "__main__":
    sys.exit(main())
