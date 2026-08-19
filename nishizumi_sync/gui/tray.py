"""System tray mode: stay resident and sync on a timer.

The 1.x code had a ``run_tray`` helper that nothing ever called, so the
"stay in tray" checkbox did nothing.  This one is wired to the CLI ``tray``
command and to ``tray_mode`` in the configuration.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from .. import APP_NAME, __version__
from ..paths import icon_file
from .worker import SyncWorker

HOUR_IN_MS = 60 * 60 * 1000


class TrayController(QtCore.QObject):
    """Owns the tray icon, the periodic timer and the settings window."""

    def __init__(
        self,
        cfg: dict,
        logger: logging.Logger,
        config_path: Path | None = None,
        *,
        app: QtWidgets.QApplication,
    ) -> None:
        super().__init__()
        self.cfg = cfg
        self.logger = logger
        self.config_path = config_path
        self.app = app
        self.window = None
        self.worker: SyncWorker | None = None

        icon_path = icon_file()
        icon = QtGui.QIcon(str(icon_path)) if icon_path else app.style().standardIcon(
            QtWidgets.QStyle.StandardPixmap.SP_DriveNetIcon
        )
        self.tray = QtWidgets.QSystemTrayIcon(icon)
        self.tray.setToolTip(f"{APP_NAME} {__version__}")

        self.menu = QtWidgets.QMenu()
        self.status_action = QtGui.QAction("Idle", self)
        self.status_action.setEnabled(False)
        self.menu.addAction(self.status_action)
        self.menu.addSeparator()
        self._add_action("Sync now", self.sync_now)
        self._add_action("Dry run", lambda: self.sync_now(dry_run=True))
        self._add_action("Open settings…", self.show_window)
        self.menu.addSeparator()
        self._add_action("Check for updates…", self.check_updates)
        self.menu.addSeparator()
        self._add_action("Quit", self.quit)
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._on_activated)
        self.tray.show()

        interval_hours = max(1, int(self.cfg.get("tray_interval", 2) or 2))
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(interval_hours * HOUR_IN_MS)
        self.timer.timeout.connect(lambda: self.sync_now(notify=False))
        self.timer.start()
        self.logger.info("Tray mode active — syncing every %d hour(s)", interval_hours)

    # ------------------------------------------------------------------
    def _add_action(self, text: str, handler) -> QtGui.QAction:
        action = QtGui.QAction(text, self)
        action.triggered.connect(lambda *_: handler())
        self.menu.addAction(action)
        return action

    def _on_activated(self, reason) -> None:
        if reason == QtWidgets.QSystemTrayIcon.ActivationReason.DoubleClick:
            self.show_window()

    def show_window(self) -> None:
        from .main_window import MainWindow

        if self.window is None:
            self.window = MainWindow(self.cfg, self.logger, self.config_path)
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    def sync_now(self, *, dry_run: bool = False, notify: bool = True) -> None:
        if self.worker is not None and self.worker.isRunning():
            self.logger.info("A sync is already running")
            return
        from ..config import load_config

        # Reload so edits made in the settings window (or by hand) are picked up.
        if self.config_path:
            self.cfg = load_config(self.config_path, logger=self.logger)
        self.status_action.setText("Syncing…")
        self.worker = SyncWorker(dict(self.cfg), self.config_path, dry_run=dry_run)
        self.worker.finished_ok.connect(lambda stats: self._finished(stats, notify))
        self.worker.failed.connect(self._failed)
        self.worker.start()

    def _finished(self, stats, notify: bool) -> None:
        self.status_action.setText(f"Last run: {stats.files_copied} copied")
        if notify or stats.errors:
            self.tray.showMessage(APP_NAME, stats.summary(), QtWidgets.QSystemTrayIcon.MessageIcon.Information, 5000)

    def _failed(self, message: str) -> None:
        self.status_action.setText("Last run failed")
        self.tray.showMessage(APP_NAME, message, QtWidgets.QSystemTrayIcon.MessageIcon.Critical, 8000)

    def check_updates(self) -> None:
        from ..updater import Updater
        from .dialogs import UpdateDialog

        info = Updater(self.cfg, self.logger).check(force=True)
        if info is None:
            self.tray.showMessage(
                APP_NAME, f"{APP_NAME} {__version__} is up to date.",
                QtWidgets.QSystemTrayIcon.MessageIcon.Information, 4000,
            )
            return
        dialog = UpdateDialog(info, __version__)
        dialog.exec()
        if dialog.outcome == UpdateDialog.INSTALL:
            result = Updater(self.cfg, self.logger).install(info)
            self.tray.showMessage(APP_NAME, result.message, QtWidgets.QSystemTrayIcon.MessageIcon.Information, 8000)
            if result.restart_scheduled:
                self.quit()
        elif dialog.outcome == UpdateDialog.SKIP:
            Updater(self.cfg, self.logger).skip(info)

    def quit(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            self.worker.requestInterruption()
            self.worker.wait(3000)
        self.tray.hide()
        self.app.quit()
