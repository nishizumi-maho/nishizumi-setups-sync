"""The main application window."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from .. import APP_NAME, PROJECT_URL, __version__
from ..cars import clean_name, identify_setup, load_custom_mapping, normalise, save_custom_mapping
from ..config import (
    ConfigError,
    HASH_ALGORITHMS,
    apply_active_profile,
    default_config,
    export_config,
    load_config,
    save_config,
    store_active_profile,
)
from ..logs import LEVELS, configure_from_config
from ..paths import config_file, data_dir, icon_file
from .dialogs import ExtraFolderDialog, MappingDialog, UnknownFolderDialog, UpdateDialog
from .worker import SyncWorker, UpdateCheckWorker, UpdateInstallWorker, make_stats_message

LOG_COLOURS = {
    logging.ERROR: "#c0392b",
    logging.WARNING: "#c87f0a",
    logging.DEBUG: "#7f8c8d",
}


class MainWindow(QtWidgets.QMainWindow):
    """Configuration editor and run console."""

    def __init__(self, cfg: dict, logger: logging.Logger, config_path: Path | None = None) -> None:
        super().__init__()
        self.cfg = cfg
        self.logger = logger
        self.config_path = Path(config_path) if config_path else config_file()
        self.worker: SyncWorker | None = None
        self.update_worker: UpdateCheckWorker | None = None
        self.install_worker: UpdateInstallWorker | None = None
        self.pending_update = None

        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.resize(920, 780)
        icon = icon_file()
        if icon:
            self.setWindowIcon(QtGui.QIcon(str(icon)))

        self._build_menu()
        self._build_ui()
        self._apply_config(self.cfg)
        self._update_mode_fields()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    def _build_menu(self) -> None:
        menu = self.menuBar()

        file_menu = menu.addMenu("&File")
        self._add_action(file_menu, "&Save configuration", self.save_config_action,
                         QtGui.QKeySequence.StandardKey.Save)
        self._add_action(file_menu, "&Load configuration…", self.load_config_action)
        self._add_action(file_menu, "&Export configuration…", self.export_config_action)
        file_menu.addSeparator()
        self._add_action(file_menu, "Reset to &defaults", self.reset_config_action)
        file_menu.addSeparator()
        self._add_action(file_menu, "&Quit", self.close, QtGui.QKeySequence.StandardKey.Quit)

        tools_menu = menu.addMenu("&Tools")
        self._add_action(tools_menu, "Edit &car mapping…", self.edit_mapping_action)
        self._add_action(tools_menu, "Open &data folder", self.open_data_folder_action)

        help_menu = menu.addMenu("&Help")
        self._add_action(help_menu, "Check for &updates…", lambda: self.check_updates(force=True))
        self._add_action(help_menu, "&Project page",
                         lambda: QtGui.QDesktopServices.openUrl(QtCore.QUrl(PROJECT_URL)))
        self._add_action(help_menu, "&About", self.about_action)

    def _add_action(self, menu, text, handler, shortcut=None) -> QtGui.QAction:
        """Qt 6 changed the addAction() convenience overloads; be explicit."""
        action = QtGui.QAction(text, self)
        if shortcut is not None:
            action.setShortcut(shortcut)
        action.triggered.connect(lambda *_: handler())
        menu.addAction(action)
        return action

    def _build_ui(self) -> None:
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        layout = QtWidgets.QVBoxLayout(central)

        layout.addLayout(self._build_profile_bar())

        self.tabs = QtWidgets.QTabWidget()
        self.tabs.addTab(self._build_import_tab(), "Import")
        self.tabs.addTab(self._build_sync_tab(), "Sync")
        self.tabs.addTab(self._build_drivers_tab(), "Drivers")
        self.tabs.addTab(self._build_backup_tab(), "Backup && logs")
        self.tabs.addTab(self._build_automation_tab(), "Automation")
        self.tabs.addTab(self._build_updates_tab(), "Updates")
        layout.addWidget(self.tabs, 1)

        self.log_view = QtWidgets.QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        self.log_view.setPlaceholderText("Run output appears here.")
        self.log_view.setMinimumHeight(160)
        font = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.SystemFont.FixedFont)
        self.log_view.setFont(font)

        log_box = QtWidgets.QGroupBox("Output")
        log_layout = QtWidgets.QVBoxLayout(log_box)
        log_layout.addWidget(self.log_view)
        clear_btn = QtWidgets.QPushButton("Clear")
        clear_btn.clicked.connect(self.log_view.clear)
        log_buttons = QtWidgets.QHBoxLayout()
        log_buttons.addStretch(1)
        log_buttons.addWidget(clear_btn)
        log_layout.addLayout(log_buttons)
        layout.addWidget(log_box, 1)

        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)

        self.save_btn = QtWidgets.QPushButton("Save configuration")
        self.dry_btn = QtWidgets.QPushButton("Dry run")
        self.run_btn = QtWidgets.QPushButton("Run now")
        self.run_btn.setDefault(True)
        self.save_btn.clicked.connect(self.save_config_action)
        self.dry_btn.clicked.connect(lambda: self.start_sync(dry_run=True))
        self.run_btn.clicked.connect(lambda: self.start_sync(dry_run=False))

        buttons = QtWidgets.QHBoxLayout()
        buttons.addWidget(self.progress, 1)
        buttons.addWidget(self.save_btn)
        buttons.addWidget(self.dry_btn)
        buttons.addWidget(self.run_btn)
        layout.addLayout(buttons)

        self.statusBar().showMessage(f"Configuration: {self.config_path}")

    def _build_profile_bar(self) -> QtWidgets.QHBoxLayout:
        self.profile_combo = QtWidgets.QComboBox()
        self.profile_combo.setMinimumWidth(220)
        self.profile_combo.currentIndexChanged.connect(self._on_profile_changed)
        self._suspend_profile_signal = False

        new_btn = QtWidgets.QPushButton("New")
        rename_btn = QtWidgets.QPushButton("Rename")
        delete_btn = QtWidgets.QPushButton("Delete")
        new_btn.clicked.connect(self.new_profile_action)
        rename_btn.clicked.connect(self.rename_profile_action)
        delete_btn.clicked.connect(self.delete_profile_action)

        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Profile:"))
        row.addWidget(self.profile_combo)
        row.addWidget(new_btn)
        row.addWidget(rename_btn)
        row.addWidget(delete_btn)
        row.addStretch(1)
        return row

    # -- tabs -----------------------------------------------------------
    def _scrollable(self, form: QtWidgets.QLayout) -> QtWidgets.QWidget:
        content = QtWidgets.QWidget()
        content.setLayout(form)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        return scroll

    def _path_row(self, line_edit: QtWidgets.QLineEdit, handler) -> QtWidgets.QWidget:
        widget = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(widget)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(line_edit)
        button = QtWidgets.QPushButton("Browse…")
        button.clicked.connect(handler)
        row.addWidget(button)
        return widget

    def _build_import_tab(self) -> QtWidgets.QWidget:
        form = QtWidgets.QFormLayout()
        form.setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        self.iracing_edit = QtWidgets.QLineEdit()
        self.iracing_edit.setPlaceholderText(r"C:\Users\<you>\Documents\iRacing\setups")
        form.addRow(
            "iRacing setups folder:",
            self._path_row(self.iracing_edit, lambda: self._pick_dir(self.iracing_edit, "Select the iRacing setups folder")),
        )

        self.source_type_combo = QtWidgets.QComboBox()
        for value, label in (
            ("zip", "Archive (.zip / .rar)"),
            ("folder", "Folder"),
            ("none", "Do not import, only sync"),
        ):
            self.source_type_combo.addItem(label, value)
        self.source_type_combo.currentIndexChanged.connect(self._update_mode_fields)
        form.addRow("Import mode:", self.source_type_combo)

        self.zip_edit = QtWidgets.QLineEdit()
        self.zip_row = self._path_row(
            self.zip_edit,
            lambda: self._pick_file(self.zip_edit, "Select an archive", "Archives (*.zip *.rar)"),
        )
        self.zip_label = QtWidgets.QLabel("Archive to import:")
        form.addRow(self.zip_label, self.zip_row)

        self.source_edit = QtWidgets.QLineEdit()
        self.source_row = self._path_row(
            self.source_edit, lambda: self._pick_dir(self.source_edit, "Select the folder to import")
        )
        self.source_label = QtWidgets.QLabel("Folder to import:")
        form.addRow(self.source_label, self.source_row)

        note = QtWidgets.QLabel(
            "The four fields below are folder <i>names</i>, not paths. They are created inside "
            "each car folder of your iRacing setups directory and are stored per profile."
        )
        note.setWordWrap(True)
        form.addRow(note)

        self.team_edit = QtWidgets.QLineEdit()
        self.personal_edit = QtWidgets.QLineEdit()
        self.supplier_edit = QtWidgets.QLineEdit()
        self.season_edit = QtWidgets.QLineEdit()
        form.addRow("Team folder name:", self.team_edit)
        form.addRow("Personal folder name:", self.personal_edit)
        form.addRow("Supplier folder name:", self.supplier_edit)
        form.addRow("Season folder name:", self.season_edit)
        return self._scrollable(form)

    def _build_sync_tab(self) -> QtWidgets.QWidget:
        form = QtWidgets.QFormLayout()
        form.setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        self.sync_source_edit = QtWidgets.QLineEdit()
        self.sync_dest_edit = QtWidgets.QLineEdit()
        form.addRow("Sync source (copy from):", self.sync_source_edit)
        form.addRow("Sync destination (copy to):", self.sync_dest_edit)

        self.hash_combo = QtWidgets.QComboBox()
        self.hash_combo.addItems(list(HASH_ALGORITHMS))
        form.addRow("File comparison:", self.hash_combo)

        self.copy_all_check = QtWidgets.QCheckBox("Copy every file type, not just .sto setups")
        self.delete_extras_check = QtWidgets.QCheckBox(
            "Remove files in the destination that no longer exist in the source"
        )
        form.addRow(self.copy_all_check)
        form.addRow(self.delete_extras_check)

        self.use_extra_check = QtWidgets.QCheckBox("Merge extra folders from other tools into the sync source")
        form.addRow(self.use_extra_check)

        self.extra_list = QtWidgets.QListWidget()
        self.extra_list.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        self.extra_list.itemDoubleClicked.connect(lambda _item: self.edit_extra_action())
        form.addRow("Extra folders:", self.extra_list)

        add_btn = QtWidgets.QPushButton("Add…")
        edit_btn = QtWidgets.QPushButton("Edit…")
        remove_btn = QtWidgets.QPushButton("Remove")
        add_btn.clicked.connect(self.add_extra_action)
        edit_btn.clicked.connect(self.edit_extra_action)
        remove_btn.clicked.connect(self.remove_extra_action)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(add_btn)
        row.addWidget(edit_btn)
        row.addWidget(remove_btn)
        row.addStretch(1)
        form.addRow("", self._wrap(row))
        return self._scrollable(form)

    def _build_drivers_tab(self) -> QtWidgets.QWidget:
        form = QtWidgets.QFormLayout()
        form.setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        self.driver_folders_check = QtWidgets.QCheckBox(
            "Use per-driver folders (Common Setups + Drivers/<name>)"
        )
        self.driver_folders_check.toggled.connect(self._update_driver_fields)
        form.addRow(self.driver_folders_check)

        self.driver_list = QtWidgets.QListWidget()
        self.driver_list.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        form.addRow("Drivers:", self.driver_list)

        add_btn = QtWidgets.QPushButton("Add driver…")
        remove_btn = QtWidgets.QPushButton("Remove selected")
        add_btn.clicked.connect(self.add_driver_action)
        remove_btn.clicked.connect(self.remove_driver_action)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(add_btn)
        row.addWidget(remove_btn)
        row.addStretch(1)
        form.addRow("", self._wrap(row))

        self.garage61_check = QtWidgets.QCheckBox("Fetch the driver list from Garage 61 before each sync")
        self.garage61_check.toggled.connect(self._update_driver_fields)
        form.addRow(self.garage61_check)

        self.team_id_edit = QtWidgets.QLineEdit()
        self.api_key_edit = QtWidgets.QLineEdit()
        self.api_key_edit.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        form.addRow("Garage 61 team ID:", self.team_id_edit)
        form.addRow("Garage 61 API key:", self.api_key_edit)

        fetch_btn = QtWidgets.QPushButton("Fetch drivers now")
        fetch_btn.clicked.connect(self.fetch_drivers_action)
        form.addRow("", fetch_btn)

        warning = QtWidgets.QLabel(
            "The API key is stored in plain text in the configuration file — keep that file private."
        )
        warning.setWordWrap(True)
        form.addRow(warning)
        return self._scrollable(form)

    def _build_backup_tab(self) -> QtWidgets.QWidget:
        form = QtWidgets.QFormLayout()
        form.setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        self.backup_check = QtWidgets.QCheckBox("Copy new setups to a backup folder around each run")
        form.addRow(self.backup_check)

        self.backup_before_edit = QtWidgets.QLineEdit()
        self.backup_after_edit = QtWidgets.QLineEdit()
        form.addRow(
            "Backup before sync:",
            self._path_row(self.backup_before_edit, lambda: self._pick_dir(self.backup_before_edit, "Backup folder (before)")),
        )
        form.addRow(
            "Backup after sync:",
            self._path_row(self.backup_after_edit, lambda: self._pick_dir(self.backup_after_edit, "Backup folder (after)")),
        )

        self.log_check = QtWidgets.QCheckBox("Write the log to a file")
        form.addRow(self.log_check)
        self.log_file_edit = QtWidgets.QLineEdit()
        form.addRow(
            "Log file:",
            self._path_row(self.log_file_edit, lambda: self._pick_save_file(self.log_file_edit, "Select the log file")),
        )
        self.log_level_combo = QtWidgets.QComboBox()
        self.log_level_combo.addItems(["DEBUG", "INFO", "WARN", "ERROR"])
        form.addRow("Log level:", self.log_level_combo)
        return self._scrollable(form)

    def _build_automation_tab(self) -> QtWidgets.QWidget:
        form = QtWidgets.QFormLayout()
        form.setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        self.startup_check = QtWidgets.QCheckBox("Run silently when started without a console (startup shortcut)")
        form.addRow(self.startup_check)

        self.tray_check = QtWidgets.QCheckBox("Stay in the system tray and sync periodically")
        self.tray_interval_spin = QtWidgets.QSpinBox()
        self.tray_interval_spin.setRange(1, 168)
        self.tray_interval_spin.setSuffix(" hour(s)")
        form.addRow(self.tray_check)
        form.addRow("Sync every:", self.tray_interval_spin)

        self.plugins_check = QtWidgets.QCheckBox("Run before_sync / after_sync plugins")
        form.addRow(self.plugins_check)
        hint = QtWidgets.QLabel(
            f"Plugins are Python files in <code>{data_dir() / 'plugins'}</code> exposing "
            "<code>execute(config, logger)</code>. They run with your user's permissions."
        )
        hint.setWordWrap(True)
        hint.setTextFormat(QtCore.Qt.TextFormat.RichText)
        form.addRow(hint)
        return self._scrollable(form)

    def _build_updates_tab(self) -> QtWidgets.QWidget:
        from ..updater import detect_install_mode

        form = QtWidgets.QFormLayout()
        form.setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        form.addRow("Installed version:", QtWidgets.QLabel(__version__))
        form.addRow("Installation type:", QtWidgets.QLabel(detect_install_mode()))

        self.update_startup_check = QtWidgets.QCheckBox("Check for updates when the application starts")
        form.addRow(self.update_startup_check)

        self.update_interval_spin = QtWidgets.QSpinBox()
        self.update_interval_spin.setRange(0, 720)
        self.update_interval_spin.setSuffix(" hour(s)")
        self.update_interval_spin.setSpecialValueText("every start")
        form.addRow("Check at most every:", self.update_interval_spin)

        self.update_channel_combo = QtWidgets.QComboBox()
        self.update_channel_combo.addItem("Stable releases only", "stable")
        self.update_channel_combo.addItem("Include pre-releases", "prerelease")
        form.addRow("Channel:", self.update_channel_combo)

        self.update_auto_check = QtWidgets.QCheckBox("Download and install updates automatically")
        form.addRow(self.update_auto_check)

        self.update_status_label = QtWidgets.QLabel("")
        self.update_status_label.setWordWrap(True)
        form.addRow("Status:", self.update_status_label)

        check_btn = QtWidgets.QPushButton("Check for updates now")
        check_btn.clicked.connect(lambda: self.check_updates(force=True))
        form.addRow("", check_btn)
        return self._scrollable(form)

    def _wrap(self, layout: QtWidgets.QLayout) -> QtWidgets.QWidget:
        widget = QtWidgets.QWidget()
        widget.setLayout(layout)
        return widget

    # ------------------------------------------------------------------
    # Config <-> widgets
    # ------------------------------------------------------------------
    def _apply_config(self, cfg: dict) -> None:
        apply_active_profile(cfg)
        self.iracing_edit.setText(cfg.get("iracing_folder", ""))
        index = self.source_type_combo.findData(cfg.get("source_type", "zip"))
        self.source_type_combo.setCurrentIndex(max(0, index))
        self.zip_edit.setText(cfg.get("zip_file", ""))
        self.source_edit.setText(cfg.get("source_folder", ""))
        self.team_edit.setText(cfg.get("team_folder", ""))
        self.personal_edit.setText(cfg.get("personal_folder", ""))
        self.supplier_edit.setText(cfg.get("supplier_folder", ""))
        self.season_edit.setText(cfg.get("season_folder", ""))

        self.sync_source_edit.setText(cfg.get("sync_source", ""))
        self.sync_dest_edit.setText(cfg.get("sync_destination", ""))
        self.hash_combo.setCurrentText(cfg.get("hash_algorithm", "md5"))
        self.copy_all_check.setChecked(bool(cfg.get("copy_all")))
        self.delete_extras_check.setChecked(bool(cfg.get("delete_extras", True)))
        self.use_extra_check.setChecked(bool(cfg.get("use_external")))
        self.extra_list.clear()
        for definition in cfg.get("extra_folders", []):
            self._add_extra_item(definition)

        self.driver_folders_check.setChecked(bool(cfg.get("use_driver_folders")))
        self.driver_list.clear()
        self.driver_list.addItems(cfg.get("drivers", []))
        self.garage61_check.setChecked(bool(cfg.get("use_garage61")))
        self.team_id_edit.setText(cfg.get("garage61_team_id", ""))
        self.api_key_edit.setText(cfg.get("garage61_api_key", ""))

        self.backup_check.setChecked(bool(cfg.get("backup_enabled")))
        self.backup_before_edit.setText(cfg.get("backup_before_folder", ""))
        self.backup_after_edit.setText(cfg.get("backup_after_folder", ""))
        self.log_check.setChecked(bool(cfg.get("enable_logging")))
        self.log_file_edit.setText(cfg.get("log_file", ""))
        level = str(cfg.get("log_level", "INFO")).upper()
        self.log_level_combo.setCurrentText(level if level in LEVELS else "INFO")

        self.startup_check.setChecked(bool(cfg.get("run_on_startup")))
        self.tray_check.setChecked(bool(cfg.get("tray_mode")))
        self.tray_interval_spin.setValue(int(cfg.get("tray_interval", 2) or 2))
        self.plugins_check.setChecked(bool(cfg.get("enable_plugins")))

        updates = cfg.get("updates", {})
        self.update_startup_check.setChecked(bool(updates.get("check_on_startup", True)))
        self.update_interval_spin.setValue(int(updates.get("check_interval_hours", 24) or 0))
        channel_index = self.update_channel_combo.findData(updates.get("channel", "stable"))
        self.update_channel_combo.setCurrentIndex(max(0, channel_index))
        self.update_auto_check.setChecked(bool(updates.get("auto_install")))

        self._reload_profiles()
        self._update_driver_fields()

    def collect_config(self) -> dict:
        cfg = dict(self.cfg)
        cfg["iracing_folder"] = self.iracing_edit.text().strip()
        cfg["source_type"] = self.source_type_combo.currentData() or "zip"
        cfg["zip_file"] = self.zip_edit.text().strip()
        cfg["source_folder"] = self.source_edit.text().strip()
        cfg["team_folder"] = clean_name(self.team_edit.text())
        cfg["personal_folder"] = clean_name(self.personal_edit.text())
        cfg["supplier_folder"] = clean_name(self.supplier_edit.text())
        cfg["season_folder"] = clean_name(self.season_edit.text())

        cfg["sync_source"] = clean_name(self.sync_source_edit.text())
        cfg["sync_destination"] = clean_name(self.sync_dest_edit.text())
        cfg["hash_algorithm"] = self.hash_combo.currentText()
        cfg["copy_all"] = self.copy_all_check.isChecked()
        cfg["delete_extras"] = self.delete_extras_check.isChecked()
        cfg["use_external"] = self.use_extra_check.isChecked()
        cfg["extra_folders"] = [
            self.extra_list.item(row).data(QtCore.Qt.ItemDataRole.UserRole) for row in range(self.extra_list.count())
        ]

        cfg["use_driver_folders"] = self.driver_folders_check.isChecked()
        cfg["drivers"] = [self.driver_list.item(row).text() for row in range(self.driver_list.count())]
        cfg["use_garage61"] = self.garage61_check.isChecked()
        cfg["garage61_team_id"] = self.team_id_edit.text().strip()
        cfg["garage61_api_key"] = self.api_key_edit.text().strip()

        cfg["backup_enabled"] = self.backup_check.isChecked()
        cfg["backup_before_folder"] = self.backup_before_edit.text().strip()
        cfg["backup_after_folder"] = self.backup_after_edit.text().strip()
        cfg["enable_logging"] = self.log_check.isChecked()
        cfg["log_file"] = self.log_file_edit.text().strip()
        cfg["log_level"] = self.log_level_combo.currentText()

        cfg["run_on_startup"] = self.startup_check.isChecked()
        cfg["tray_mode"] = self.tray_check.isChecked()
        cfg["tray_interval"] = self.tray_interval_spin.value()
        cfg["enable_plugins"] = self.plugins_check.isChecked()

        cfg["updates"] = {
            **cfg.get("updates", {}),
            "check_on_startup": self.update_startup_check.isChecked(),
            "check_interval_hours": self.update_interval_spin.value(),
            "channel": self.update_channel_combo.currentData() or "stable",
            "auto_install": self.update_auto_check.isChecked(),
        }
        store_active_profile(cfg)
        return cfg

    # ------------------------------------------------------------------
    # Profiles
    # ------------------------------------------------------------------
    def _reload_profiles(self) -> None:
        self._suspend_profile_signal = True
        self.profile_combo.clear()
        for index, profile in enumerate(self.cfg.get("profiles", [])):
            self.profile_combo.addItem(profile.get("name") or f"Profile {index + 1}", index)
        self.profile_combo.setCurrentIndex(int(self.cfg.get("active_profile", 0)))
        self._suspend_profile_signal = False

    def _on_profile_changed(self, index: int) -> None:
        if self._suspend_profile_signal or index < 0:
            return
        cfg = self.collect_config()
        cfg["active_profile"] = index
        apply_active_profile(cfg)
        self.cfg = cfg
        self._apply_config(self.cfg)
        self.statusBar().showMessage(f"Switched to profile '{self.profile_combo.currentText()}'", 4000)

    def new_profile_action(self) -> None:
        name, ok = QtWidgets.QInputDialog.getText(self, "New profile", "Profile name:")
        if not ok or not name.strip():
            return
        cfg = self.collect_config()
        cfg.setdefault("profiles", []).append(
            {
                "name": name.strip(),
                "team_folder": cfg.get("team_folder", ""),
                "personal_folder": cfg.get("personal_folder", ""),
                "supplier_folder": cfg.get("supplier_folder", ""),
                "season_folder": cfg.get("season_folder", ""),
            }
        )
        cfg["active_profile"] = len(cfg["profiles"]) - 1
        self.cfg = cfg
        self._apply_config(self.cfg)

    def rename_profile_action(self) -> None:
        cfg = self.collect_config()
        index = int(cfg.get("active_profile", 0))
        profiles = cfg.get("profiles", [])
        if not profiles:
            return
        current = profiles[index].get("name", "")
        name, ok = QtWidgets.QInputDialog.getText(self, "Rename profile", "Profile name:", text=current)
        if not ok or not name.strip():
            return
        profiles[index]["name"] = name.strip()
        self.cfg = cfg
        self._apply_config(self.cfg)

    def delete_profile_action(self) -> None:
        cfg = self.collect_config()
        profiles = cfg.get("profiles", [])
        if len(profiles) <= 1:
            QtWidgets.QMessageBox.information(self, "Profiles", "At least one profile is required.")
            return
        index = int(cfg.get("active_profile", 0))
        name = profiles[index].get("name", "")
        answer = QtWidgets.QMessageBox.question(self, "Delete profile", f"Delete profile '{name}'?")
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        profiles.pop(index)
        cfg["active_profile"] = max(0, index - 1)
        apply_active_profile(cfg)
        self.cfg = cfg
        self._apply_config(self.cfg)

    # ------------------------------------------------------------------
    # Small actions
    # ------------------------------------------------------------------
    def _pick_dir(self, edit: QtWidgets.QLineEdit, title: str) -> None:
        start = edit.text().strip() or str(Path.home())
        path = QtWidgets.QFileDialog.getExistingDirectory(self, title, start)
        if path:
            edit.setText(path)

    def _pick_file(self, edit: QtWidgets.QLineEdit, title: str, filters: str) -> None:
        start = edit.text().strip() or str(Path.home())
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, title, start, filters)
        if path:
            edit.setText(path)

    def _pick_save_file(self, edit: QtWidgets.QLineEdit, title: str) -> None:
        start = edit.text().strip() or str(Path.home())
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, title, start)
        if path:
            edit.setText(path)

    def _update_mode_fields(self) -> None:
        mode = self.source_type_combo.currentData()
        self.zip_label.setVisible(mode == "zip")
        self.zip_row.setVisible(mode == "zip")
        self.source_label.setVisible(mode == "folder")
        self.source_row.setVisible(mode == "folder")

    def _update_driver_fields(self) -> None:
        manual = self.driver_folders_check.isChecked()
        api = self.garage61_check.isChecked()
        self.driver_list.setEnabled(manual)
        self.team_id_edit.setEnabled(api)
        self.api_key_edit.setEnabled(api)

    def _add_extra_item(self, definition: dict) -> None:
        label = f"{definition.get('name')}  ({'car root' if definition.get('location') == 'car' else 'sync destination'})"
        item = QtWidgets.QListWidgetItem(label)
        item.setData(QtCore.Qt.ItemDataRole.UserRole, dict(definition))
        self.extra_list.addItem(item)

    def add_extra_action(self) -> None:
        dialog = ExtraFolderDialog(parent=self)
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        value = dialog.value()
        if value:
            self._add_extra_item(value)

    def edit_extra_action(self) -> None:
        item = self.extra_list.currentItem()
        if item is None:
            return
        dialog = ExtraFolderDialog(item.data(QtCore.Qt.ItemDataRole.UserRole), parent=self)
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        value = dialog.value()
        if not value:
            return
        item.setData(QtCore.Qt.ItemDataRole.UserRole, value)
        item.setText(f"{value['name']}  ({'car root' if value['location'] == 'car' else 'sync destination'})")

    def remove_extra_action(self) -> None:
        for item in self.extra_list.selectedItems():
            self.extra_list.takeItem(self.extra_list.row(item))

    def add_driver_action(self) -> None:
        name, ok = QtWidgets.QInputDialog.getText(self, "Add driver", "Driver name:")
        if not ok:
            return
        cleaned = clean_name(name)
        if not cleaned:
            return
        existing = {self.driver_list.item(row).text().lower() for row in range(self.driver_list.count())}
        if cleaned.lower() in existing:
            return
        self.driver_list.addItem(cleaned)

    def remove_driver_action(self) -> None:
        for item in self.driver_list.selectedItems():
            self.driver_list.takeItem(self.driver_list.row(item))

    def fetch_drivers_action(self) -> None:
        from ..garage61 import fetch_drivers

        team_id = self.team_id_edit.text().strip()
        if not team_id:
            QtWidgets.QMessageBox.warning(self, "Garage 61", "Enter the team ID first.")
            return
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.CursorShape.WaitCursor)
        try:
            names = fetch_drivers(team_id, self.api_key_edit.text().strip(), self.logger)
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        if names is None:
            QtWidgets.QMessageBox.warning(
                self, "Garage 61", "Could not fetch the driver list. Check the output panel for details."
            )
            return
        self.driver_list.clear()
        self.driver_list.addItems(names)
        QtWidgets.QMessageBox.information(self, "Garage 61", f"Loaded {len(names)} driver(s).")

    def edit_mapping_action(self) -> None:
        mapping = load_custom_mapping(logger=self.logger)
        dialog = MappingDialog(mapping, parent=self)
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        try:
            save_custom_mapping(dialog.mapping(), logger=self.logger)
        except OSError as exc:
            QtWidgets.QMessageBox.critical(self, "Car mapping", f"Could not save the mapping:\n{exc}")
            return
        self.statusBar().showMessage("Car mapping saved", 4000)

    def open_data_folder_action(self) -> None:
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(data_dir())))

    def about_action(self) -> None:
        QtWidgets.QMessageBox.about(
            self,
            f"About {APP_NAME}",
            f"<h3>{APP_NAME}</h3><p>Version {__version__}</p>"
            f"<p>Synchronises iRacing setups between personal, team and supplier folders.</p>"
            f'<p><a href="{PROJECT_URL}">{PROJECT_URL}</a></p>',
        )

    # ------------------------------------------------------------------
    # Configuration files
    # ------------------------------------------------------------------
    def save_config_action(self) -> bool:
        cfg = self.collect_config()
        try:
            save_config(cfg, self.config_path, logger=self.logger)
        except ConfigError as exc:
            QtWidgets.QMessageBox.critical(self, "Save failed", str(exc))
            return False
        self.cfg = cfg
        configure_from_config(self.cfg, self.logger)
        self.statusBar().showMessage(f"Saved to {self.config_path}", 5000)
        return True

    def load_config_action(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Load configuration", str(self.config_path.parent), "JSON files (*.json)"
        )
        if not path:
            return
        self.cfg = load_config(path, logger=self.logger)
        self._apply_config(self.cfg)
        self.statusBar().showMessage(f"Loaded {path}", 5000)

    def export_config_action(self) -> None:
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export configuration", str(self.config_path.parent / "nishizumi_config.json"), "JSON files (*.json)"
        )
        if not path:
            return
        try:
            export_config(self.collect_config(), path)
        except ConfigError as exc:
            QtWidgets.QMessageBox.critical(self, "Export failed", str(exc))
            return
        self.statusBar().showMessage(f"Exported to {path}", 5000)

    def reset_config_action(self) -> None:
        if QtWidgets.QMessageBox.question(
            self, "Reset configuration", "Discard every setting and restore the defaults?"
        ) != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        self.cfg = default_config()
        self._apply_config(self.cfg)
        self.statusBar().showMessage("Defaults restored — press Save to keep them", 6000)

    # ------------------------------------------------------------------
    # Running
    # ------------------------------------------------------------------
    def _validate(self, cfg: dict) -> str | None:
        folder = cfg.get("iracing_folder", "").strip()
        if not folder:
            return "Select your iRacing setups folder first."
        if not os.path.isdir(folder):
            return f"The iRacing setups folder does not exist:\n{folder}"
        if not cfg.get("sync_source") or not cfg.get("sync_destination"):
            return "Fill in both the sync source and the sync destination."
        if cfg["sync_source"].lower() == cfg["sync_destination"].lower():
            return "The sync source and destination must be different folders."
        if cfg.get("source_type") == "zip" and cfg.get("zip_file") and not os.path.isfile(cfg["zip_file"]):
            return f"The archive to import does not exist:\n{cfg['zip_file']}"
        if cfg.get("source_type") == "folder" and cfg.get("source_folder") and not os.path.isdir(cfg["source_folder"]):
            return f"The folder to import does not exist:\n{cfg['source_folder']}"
        if cfg.get("use_driver_folders") and not cfg.get("drivers") and not cfg.get("use_garage61"):
            return "Driver folders are enabled but no driver has been added."
        return None

    def _resolve_unknown_folders(self, cfg: dict) -> bool:
        """Ask about unrecognised supplier folders before the run starts."""
        from ..importer import ImportError_, configured_setup_folders

        if cfg.get("source_type") not in {"zip", "folder"}:
            return True
        mapping = load_custom_mapping(logger=self.logger)
        try:
            folders = configured_setup_folders(cfg, mapping)
        except (ImportError_, OSError) as exc:
            QtWidgets.QMessageBox.critical(self, "Import", str(exc))
            return False

        unknown = [name for name in folders if not identify_setup(name, mapping)]
        if not unknown:
            return True

        changed = False
        for folder in unknown:
            dialog = UnknownFolderDialog(folder, parent=self)
            if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
                continue
            car, remember = dialog.choice()
            if not car:
                continue
            mapping[normalise(folder)] = car
            changed = bool(remember) or changed
        if changed:
            try:
                save_custom_mapping(mapping, logger=self.logger)
            except OSError as exc:
                QtWidgets.QMessageBox.warning(self, "Car mapping", f"Could not save the mapping:\n{exc}")
        return True

    def start_sync(self, *, dry_run: bool) -> None:
        if self.worker is not None and self.worker.isRunning():
            return
        cfg = self.collect_config()
        problem = self._validate(cfg)
        if problem:
            QtWidgets.QMessageBox.warning(self, "Check the configuration", problem)
            return
        if not self.save_config_action():
            return
        if not self._resolve_unknown_folders(self.cfg):
            return

        self.log_view.clear()
        self._set_running(True)
        self.worker = SyncWorker(dict(self.cfg), self.config_path, dry_run=dry_run)
        self.worker.log.connect(self.append_log)
        self.worker.finished_ok.connect(lambda stats: self._sync_finished(stats, dry_run))
        self.worker.failed.connect(self._sync_failed)
        self.worker.finished.connect(lambda: self._set_running(False))
        self.worker.start()

    def _set_running(self, running: bool) -> None:
        self.run_btn.setEnabled(not running)
        self.dry_btn.setEnabled(not running)
        self.save_btn.setEnabled(not running)
        self.progress.setVisible(running)
        self.statusBar().showMessage("Working…" if running else "Ready")

    def append_log(self, text: str, level: int) -> None:
        colour = LOG_COLOURS.get(level)
        if colour:
            self.log_view.appendHtml(f'<span style="color:{colour}">{_escape(text)}</span>')
        else:
            self.log_view.appendPlainText(text)
        self.log_view.verticalScrollBar().setValue(self.log_view.verticalScrollBar().maximum())

    def _sync_finished(self, stats, dry_run: bool) -> None:
        message = make_stats_message(stats, dry_run)
        if stats.errors:
            QtWidgets.QMessageBox.warning(self, "Finished with errors", message)
        else:
            QtWidgets.QMessageBox.information(self, "Finished", message)

    def _sync_failed(self, message: str) -> None:
        QtWidgets.QMessageBox.critical(self, "Sync failed", message)

    # ------------------------------------------------------------------
    # Updates
    # ------------------------------------------------------------------
    def check_updates(self, *, force: bool) -> None:
        if self.update_worker is not None and self.update_worker.isRunning():
            return
        if force:
            self.update_status_label.setText("Checking…")
        self.update_worker = UpdateCheckWorker(dict(self.cfg), force=force)
        self.update_worker.result.connect(lambda info: self._update_result(info, force))
        self.update_worker.start()

    def _update_result(self, info, force: bool) -> None:
        if info is None:
            if force:
                self.update_status_label.setText(f"You are running the latest version ({__version__}).")
                QtWidgets.QMessageBox.information(
                    self, "No update", f"{APP_NAME} {__version__} is up to date."
                )
            return
        self.pending_update = info
        self.update_status_label.setText(f"Version {info.version} is available.")
        self.statusBar().showMessage(f"Update {info.version} available", 10000)

        if self.cfg.get("updates", {}).get("auto_install"):
            self._install_update(info)
            return

        dialog = UpdateDialog(info, __version__, parent=self)
        dialog.exec()
        if dialog.outcome == UpdateDialog.INSTALL:
            self._install_update(info)
        elif dialog.outcome == UpdateDialog.SKIP:
            from ..updater import Updater

            Updater(self.cfg, self.logger).skip(info)
            self.update_status_label.setText(f"Version {info.version} skipped.")

    def _install_update(self, info) -> None:
        if self.install_worker is not None and self.install_worker.isRunning():
            return
        self.update_status_label.setText(f"Downloading {info.version}…")
        self.progress.setVisible(True)
        self.progress.setRange(0, 100)
        self.install_worker = UpdateInstallWorker(dict(self.cfg), info)
        self.install_worker.progress.connect(self._download_progress)
        self.install_worker.result.connect(self._install_finished)
        self.install_worker.start()

    def _download_progress(self, done: int, total: int) -> None:
        if total:
            self.progress.setRange(0, 100)
            self.progress.setValue(int(done * 100 / total))
        else:
            self.progress.setRange(0, 0)

    def _install_finished(self, result) -> None:
        self.progress.setVisible(False)
        self.progress.setRange(0, 0)
        self.update_status_label.setText(result.message)
        if not result.ok:
            QtWidgets.QMessageBox.warning(self, "Update", result.message)
            return
        if result.restart_scheduled:
            QtWidgets.QMessageBox.information(self, "Update", result.message)
            QtWidgets.QApplication.quit()
        else:
            QtWidgets.QMessageBox.information(self, "Update", result.message)

    # ------------------------------------------------------------------
    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # noqa: N802 - Qt naming
        if self.worker is not None and self.worker.isRunning():
            answer = QtWidgets.QMessageBox.question(
                self, "Sync running", "A sync is still running. Quit anyway?"
            )
            if answer != QtWidgets.QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.worker.requestInterruption()
        event.accept()


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
