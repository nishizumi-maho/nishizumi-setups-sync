"""Interface smoke tests.

Skipped when PySide6 is not installed, so the core suite still runs anywhere.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtWidgets  # noqa: E402

from nishizumi_sync import config  # noqa: E402
from nishizumi_sync.gui.dialogs import (  # noqa: E402
    ExtraFolderDialog,
    MappingDialog,
    UnknownFolderDialog,
    UpdateDialog,
)
from nishizumi_sync.gui.main_window import MainWindow  # noqa: E402
from nishizumi_sync.gui.worker import SyncWorker, make_stats_message  # noqa: E402
from nishizumi_sync.updater import UpdateInfo  # noqa: E402


@pytest.fixture(scope="session")
def qt_app():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app


@pytest.fixture
def window(qt_app, logger, base_config):
    cfg = config.migrate(base_config)
    cfg["updates"]["check_on_startup"] = False
    win = MainWindow(cfg, logger)
    yield win
    win.close()


def test_window_builds_with_every_tab(window):
    assert [window.tabs.tabText(i) for i in range(window.tabs.count())] == [
        "Import", "Sync", "Drivers", "Backup && logs", "Automation", "Updates",
    ]


def test_config_survives_a_widget_round_trip(window, base_config):
    collected = window.collect_config()
    assert collected["iracing_folder"] == base_config["iracing_folder"]
    assert collected["sync_source"] == "Private"
    assert collected["sync_destination"] == "Shared"
    assert collected["hash_algorithm"] == "md5"


def test_extra_folders_and_drivers_round_trip(window):
    window._add_extra_item({"name": "Garage 61", "location": "dest"})
    window.driver_list.addItem("Miho N")
    collected = window.collect_config()
    assert collected["extra_folders"] == [{"name": "Garage 61", "location": "dest"}]
    assert collected["drivers"] == ["Miho N"]


def test_profile_switching_keeps_both_profiles(window):
    window.cfg["profiles"].append(
        {"name": "Second", "team_folder": "T2", "personal_folder": "P2",
         "supplier_folder": "S2", "season_folder": "2026S1"}
    )
    window._apply_config(window.cfg)
    window.profile_combo.setCurrentIndex(1)
    assert window.team_edit.text() == "T2"
    window.profile_combo.setCurrentIndex(0)
    assert window.team_edit.text() == "Team"


def test_validation_catches_bad_configurations(window, base_config):
    assert window._validate({**base_config, "iracing_folder": ""}) is not None
    assert window._validate({**base_config, "iracing_folder": "/nope/nope"}) is not None
    assert window._validate({**base_config, "sync_destination": "Private"}) is not None
    assert window._validate(window.collect_config()) is None


def test_import_mode_shows_only_the_relevant_field(window):
    window.source_type_combo.setCurrentIndex(window.source_type_combo.findData("zip"))
    assert window.zip_row.isVisibleTo(window) and not window.source_row.isVisibleTo(window)
    window.source_type_combo.setCurrentIndex(window.source_type_combo.findData("folder"))
    assert window.source_row.isVisibleTo(window) and not window.zip_row.isVisibleTo(window)


def test_log_pane_escapes_markup(window):
    window.append_log("plain", 20)
    window.append_log("<b>error</b>", 40)
    text = window.log_view.toPlainText()
    assert "plain" in text and "<b>error</b>" in text


def test_saving_writes_the_configuration(window, tmp_path):
    window.config_path = tmp_path / "saved.json"
    assert window.save_config_action() is True
    assert config.load_config(window.config_path)["sync_source"] == "Private"


def test_mapping_dialog_round_trip(qt_app):
    dialog = MappingDialog({"my folder": "ferrari296gt3"})
    assert dialog.mapping() == {"my folder": "ferrari296gt3"}


def test_extra_folder_dialog_round_trip(qt_app):
    assert ExtraFolderDialog({"name": "Garage 61", "location": "dest"}).value() == {
        "name": "Garage 61",
        "location": "dest",
    }
    assert ExtraFolderDialog({"name": "  "}).value() is None


def test_unknown_folder_dialog(qt_app):
    dialog = UnknownFolderDialog("07 - Mystery")
    dialog.combo.setCurrentText("ferrari296gt3")
    assert dialog.choice() == ("ferrari296gt3", True)


def test_update_dialog_defaults_to_later(qt_app):
    info = UpdateInfo.from_api(
        {"tag_name": "v9.9.9", "name": "R", "body": "notes", "html_url": "https://example.invalid/r"}
    )
    assert UpdateDialog(info, "2.0.0").outcome == UpdateDialog.LATER


def test_sync_worker_streams_logs_and_finishes(qt_app, base_config, iracing, make_file):
    make_file(iracing / "ferrari296gt3" / "Private" / "fast.sto")
    lines: list[str] = []
    results: list[object] = []

    worker = SyncWorker(config.migrate(base_config), None, dry_run=False)
    worker.log.connect(lambda text, level: lines.append(text))
    worker.finished_ok.connect(results.append)
    worker.finished.connect(qt_app.quit)
    worker.start()
    QtCore.QTimer.singleShot(20000, qt_app.quit)
    qt_app.exec()
    assert worker.wait(5000)

    assert results, "the worker produced no result"
    assert results[0].files_copied >= 1
    assert any("Sync finished" in line for line in lines)
    assert (iracing / "ferrari296gt3" / "Shared" / "fast.sto").exists()


def test_stats_message_mentions_dry_run():
    from nishizumi_sync.fileops import SyncStats

    assert "nothing was changed" in make_stats_message(SyncStats(), dry_run=True)
    assert "Sync finished" in make_stats_message(SyncStats(), dry_run=False)


def test_join_workers_waits_for_a_running_thread(window, base_config, iracing, make_file):
    """Qt tears down a running QThread noisily; the window must join them first."""
    make_file(iracing / "ferrari296gt3" / "Private" / "fast.sto")
    worker = SyncWorker(config.migrate(base_config), None, dry_run=True)
    worker.start()
    window.worker = worker

    window.join_workers(timeout_ms=10000)
    assert not worker.isRunning()


def test_join_workers_is_a_no_op_when_idle(window):
    window.join_workers(timeout_ms=100)  # must not raise or block
