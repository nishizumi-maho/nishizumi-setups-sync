"""Dialogs used by the main window."""

from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from ..cars import known_targets, normalise
from ..config import EXTRA_LOCATIONS
from ..updater import UpdateInfo, summarise_release


class MappingDialog(QtWidgets.QDialog):
    """Editor for ``custom_car_mapping.json``."""

    def __init__(self, mapping: dict[str, str], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit car mapping")
        self.resize(560, 420)

        self.table = QtWidgets.QTableWidget(0, 2, self)
        self.table.setHorizontalHeaderLabels(["Supplier folder", "iRacing car folder"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        for folder, car in sorted(mapping.items()):
            self._add_row(folder, car)

        help_label = QtWidgets.QLabel(
            "Map a supplier's folder name to the iRacing car folder it belongs to. "
            "Folder names are matched case-insensitively."
        )
        help_label.setWordWrap(True)

        add_btn = QtWidgets.QPushButton("Add")
        remove_btn = QtWidgets.QPushButton("Remove selected")
        add_btn.clicked.connect(lambda: self._add_row("", ""))
        remove_btn.clicked.connect(self._remove_rows)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Save | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        row = QtWidgets.QHBoxLayout()
        row.addWidget(add_btn)
        row.addWidget(remove_btn)
        row.addStretch(1)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(help_label)
        layout.addWidget(self.table)
        layout.addLayout(row)
        layout.addWidget(buttons)

    def _add_row(self, folder: str, car: str) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QtWidgets.QTableWidgetItem(folder))
        editor = QtWidgets.QComboBox()
        editor.setEditable(True)
        editor.addItems(known_targets())
        editor.setCurrentText(car)
        self.table.setCellWidget(row, 1, editor)

    def _remove_rows(self) -> None:
        for row in sorted({index.row() for index in self.table.selectedIndexes()}, reverse=True):
            self.table.removeRow(row)

    def mapping(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for row in range(self.table.rowCount()):
            folder_item = self.table.item(row, 0)
            editor = self.table.cellWidget(row, 1)
            folder = normalise(folder_item.text() if folder_item else "")
            car = editor.currentText().strip() if editor else ""
            if folder and car:
                result[folder] = car
        return result


class ExtraFolderDialog(QtWidgets.QDialog):
    """Create or edit an extra sync folder."""

    def __init__(self, definition: dict | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Extra sync folder")
        definition = definition or {}

        self.name_edit = QtWidgets.QLineEdit(str(definition.get("name", "")))
        self.location_combo = QtWidgets.QComboBox()
        self.location_combo.addItem("Car root (e.g. <car>/Garage 61)", "car")
        self.location_combo.addItem("Inside the sync destination", "dest")
        index = self.location_combo.findData(definition.get("location", "car"))
        self.location_combo.setCurrentIndex(max(0, index))

        form = QtWidgets.QFormLayout()
        form.addRow("Folder name:", self.name_edit)
        form.addRow("Location:", self.location_combo)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def value(self) -> dict[str, str] | None:
        from ..cars import clean_name

        name = clean_name(self.name_edit.text())
        if not name:
            return None
        location = self.location_combo.currentData()
        return {"name": name, "location": location if location in EXTRA_LOCATIONS else "car"}


class UnknownFolderDialog(QtWidgets.QDialog):
    """Ask which car an unrecognised supplier folder belongs to."""

    def __init__(self, folder: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Unrecognised folder")
        self.resize(480, 180)

        label = QtWidgets.QLabel(
            f"The folder <b>{folder}</b> could not be matched to a car.<br>"
            "Choose the iRacing car folder it belongs to, or skip it."
        )
        label.setWordWrap(True)

        self.combo = QtWidgets.QComboBox()
        self.combo.setEditable(True)
        self.combo.addItem("")
        self.combo.addItems(known_targets())

        self.remember = QtWidgets.QCheckBox("Remember this choice")
        self.remember.setChecked(True)

        buttons = QtWidgets.QDialogButtonBox()
        buttons.addButton("Use this car", QtWidgets.QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton("Skip folder", QtWidgets.QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(label)
        layout.addWidget(self.combo)
        layout.addWidget(self.remember)
        layout.addWidget(buttons)

    def choice(self) -> tuple[str, bool]:
        return self.combo.currentText().strip(), self.remember.isChecked()


class UpdateDialog(QtWidgets.QDialog):
    """Presents a release and installs it on request."""

    INSTALL = 1
    SKIP = 2
    LATER = 3

    def __init__(self, info: UpdateInfo, current_version: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Update available")
        self.resize(560, 420)
        self.info = info
        self.outcome = self.LATER

        heading = QtWidgets.QLabel(
            f"<h3>Version {info.version} is available</h3>"
            f"You are running {current_version}."
        )
        heading.setTextFormat(QtCore.Qt.TextFormat.RichText)

        notes = QtWidgets.QPlainTextEdit(summarise_release(info, max_lines=200) or "No release notes.")
        notes.setReadOnly(True)

        link = QtWidgets.QLabel(f'<a href="{info.html_url}">Open the release page</a>')
        link.setOpenExternalLinks(True)

        self.progress = QtWidgets.QProgressBar()
        self.progress.setVisible(False)

        self.install_btn = QtWidgets.QPushButton("Install now")
        skip_btn = QtWidgets.QPushButton("Skip this version")
        later_btn = QtWidgets.QPushButton("Remind me later")
        self.install_btn.setDefault(True)
        self.install_btn.clicked.connect(self._install)
        skip_btn.clicked.connect(self._skip)
        later_btn.clicked.connect(self.reject)

        row = QtWidgets.QHBoxLayout()
        row.addWidget(later_btn)
        row.addWidget(skip_btn)
        row.addStretch(1)
        row.addWidget(self.install_btn)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(heading)
        layout.addWidget(notes)
        layout.addWidget(link)
        layout.addWidget(self.progress)
        layout.addLayout(row)

    def _install(self) -> None:
        self.outcome = self.INSTALL
        self.accept()

    def _skip(self) -> None:
        self.outcome = self.SKIP
        self.accept()
