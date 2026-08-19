"""PySide6 interface.

Importing this package raises :class:`ImportError` when PySide6 is missing, so
callers can fall back to the command line.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from PySide6 import QtGui, QtWidgets  # noqa: F401 - import guard, see docstring

from .. import APP_NAME
from ..paths import icon_file

__all__ = ["run_gui", "run_tray"]


def _application() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("Nishizumi")
    icon = icon_file()
    if icon:
        app.setWindowIcon(QtGui.QIcon(str(icon)))
    return app


def run_gui(cfg: dict, logger: logging.Logger, config_path: Path | None = None) -> int:
    """Open the main window and run the Qt event loop."""
    from .main_window import MainWindow

    app = _application()
    window = MainWindow(cfg, logger, config_path)
    window.show()

    from ..updater import Updater

    updater = Updater(cfg, logger)
    applied = updater.note_startup()
    if applied:
        window.statusBar().showMessage(applied, 10000)
    if updater.due_for_check():
        window.check_updates(force=False)
    return app.exec()


def run_tray(cfg: dict, logger: logging.Logger, config_path: Path | None = None) -> int:
    """Run in the system tray, syncing on a timer."""
    from .tray import TrayController

    app = _application()
    if not QtWidgets.QSystemTrayIcon.isSystemTrayAvailable():
        logger.error("No system tray is available on this desktop")
        return 1
    app.setQuitOnLastWindowClosed(False)
    controller = TrayController(cfg, logger, config_path, app=app)
    controller.sync_now(notify=False)
    return app.exec()
