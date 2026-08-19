"""Background workers so the interface never freezes during a sync.

The old GUI called ``run_silent`` straight from the button handler, which froze
the window for the whole run and showed nothing until it finished.
"""

from __future__ import annotations

import logging
from typing import Any

from PySide6 import QtCore

from ..fileops import SyncStats
from ..logs import add_callback_handler, get_logger, remove_handler


class SyncWorker(QtCore.QThread):
    """Runs a synchronisation and streams its log output."""

    finished_ok = QtCore.Signal(object)
    failed = QtCore.Signal(str)
    log = QtCore.Signal(str, int)

    def __init__(self, cfg: dict, config_path=None, *, dry_run: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.config_path = config_path
        self.dry_run = dry_run

    def run(self) -> None:  # pragma: no cover - requires Qt
        from ..sync import SyncError, run_sync

        logger = get_logger()
        # Emitting a signal from a worker thread is safe: Qt queues the call to
        # the receiving slot in the GUI thread.
        handler = add_callback_handler(
            lambda text, level: self.log.emit(text, level),
            level=logging.DEBUG,
            logger=logger,
        )
        try:
            stats = run_sync(
                self.cfg,
                logger,
                dry_run=self.dry_run,
                config_path=self.config_path,
            )
            self.finished_ok.emit(stats)
        except SyncError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Unexpected error during sync")
            self.failed.emit(f"Unexpected error: {exc}")
        finally:
            remove_handler(handler, logger)


class UpdateCheckWorker(QtCore.QThread):
    """Checks GitHub for a new release without blocking the UI."""

    result = QtCore.Signal(object)

    def __init__(self, cfg: dict, *, force: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.force = force

    def run(self) -> None:  # pragma: no cover - requires Qt
        from ..updater import Updater

        try:
            info = Updater(self.cfg, get_logger()).check(force=self.force)
        except Exception as exc:  # pragma: no cover - defensive
            get_logger().warning("Update check failed: %s", exc)
            info = None
        self.result.emit(info)


class UpdateInstallWorker(QtCore.QThread):
    """Downloads and installs a release, reporting download progress."""

    progress = QtCore.Signal(int, int)
    result = QtCore.Signal(object)

    def __init__(self, cfg: dict, info: Any, parent=None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.info = info

    def run(self) -> None:  # pragma: no cover - requires Qt
        from ..updater import InstallResult, Updater

        try:
            updater = Updater(self.cfg, get_logger())
            outcome = updater.install(self.info, progress=lambda d, t: self.progress.emit(d, t))
        except Exception as exc:  # pragma: no cover - defensive
            outcome = InstallResult(False, f"Update failed: {exc}")
        self.result.emit(outcome)


def make_stats_message(stats: SyncStats, dry_run: bool) -> str:
    """Human readable summary shown when a run finishes."""
    prefix = "Dry run finished — nothing was changed.\n\n" if dry_run else "Sync finished.\n\n"
    return prefix + stats.summary().replace(", ", "\n")
