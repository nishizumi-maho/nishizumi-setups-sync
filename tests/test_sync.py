from __future__ import annotations

import os
import time

import pytest

from nishizumi_sync import sync
from nishizumi_sync.cars import CAR_GROUPS


def test_publishes_source_to_destination(iracing, base_config, ops, make_file):
    make_file(iracing / "ferrari296gt3" / "Private" / "fast.sto")
    stats = sync.perform_sync(ops(), str(iracing), base_config)
    assert (iracing / "ferrari296gt3" / "Shared" / "fast.sto").exists()
    assert stats.files_copied >= 1


def test_delete_extras_keeps_files_that_exist_in_the_source(iracing, base_config, ops, make_file):
    """Regression: 1.x deleted every non-.sto file from the destination."""
    car = iracing / "ferrari296gt3"
    make_file(car / "Private" / "fast.sto")
    make_file(car / "Private" / "notes.txt", "keep me")
    make_file(car / "Shared" / "notes.txt", "keep me")

    sync.perform_sync(ops(), str(iracing), base_config)
    assert (car / "Shared" / "notes.txt").read_text() == "keep me"


def test_delete_extras_removes_setups_missing_from_the_source(iracing, base_config, ops, make_file):
    car = iracing / "ferrari296gt3"
    make_file(car / "Private" / "fast.sto")
    make_file(car / "Shared" / "stale.sto")

    sync.perform_sync(ops(), str(iracing), base_config)
    assert not (car / "Shared" / "stale.sto").exists()
    assert (car / "Shared" / "fast.sto").exists()


def test_unmanaged_files_are_left_alone(iracing, base_config, ops, make_file):
    car = iracing / "ferrari296gt3"
    make_file(car / "Private" / "fast.sto")
    make_file(car / "Shared" / "telemetry.ibt", "not ours")

    sync.perform_sync(ops(), str(iracing), base_config)
    assert (car / "Shared" / "telemetry.ibt").exists()


def test_prune_foreign_removes_unmanaged_files_when_requested(tmp_path, ops, make_file):
    source = tmp_path / "src"
    target = tmp_path / "dst"
    make_file(source / "a.sto")
    make_file(target / "foreign.txt")

    sync.sync_folders(ops(), str(source), str(target), prune_foreign=True)
    assert not (target / "foreign.txt").exists()


def test_disabling_delete_extras_keeps_everything(iracing, base_config, ops, make_file):
    car = iracing / "ferrari296gt3"
    make_file(car / "Private" / "fast.sto")
    make_file(car / "Shared" / "stale.sto")
    base_config["delete_extras"] = False

    sync.perform_sync(ops(), str(iracing), base_config)
    assert (car / "Shared" / "stale.sto").exists()


def test_driver_mode_builds_common_and_driver_folders(iracing, base_config, ops, make_file):
    car = iracing / "ferrari296gt3"
    base_config.update({"use_driver_folders": True, "drivers": ["Ann Lee", "Bob"]})
    make_file(car / "Private" / "Common Setups" / "base.sto")
    make_file(car / "Private" / "Drivers" / "Ann Lee" / "ann.sto")

    sync.perform_sync(ops(), str(iracing), base_config)
    assert (car / "Shared" / "Common Setups" / "base.sto").exists()
    assert (car / "Shared" / "Drivers" / "Ann Lee" / "ann.sto").exists()
    # Bob has no folder of his own, so he inherits the common setups.
    assert (car / "Shared" / "Drivers" / "Bob" / "base.sto").exists()


def test_driver_folders_of_unknown_drivers_are_removed(iracing, base_config, ops, make_file):
    car = iracing / "ferrari296gt3"
    base_config.update({"use_driver_folders": True, "drivers": ["Ann Lee"]})
    make_file(car / "Private" / "Common Setups" / "base.sto")
    make_file(car / "Shared" / "Drivers" / "Departed" / "old.sto")

    sync.perform_sync(ops(), str(iracing), base_config)
    assert not (car / "Shared" / "Drivers" / "Departed").exists()


def test_data_packs_are_moved_under_common_setups(iracing, base_config, ops, make_file):
    car = iracing / "ferrari296gt3"
    base_config.update({"use_driver_folders": True, "drivers": ["Ann"], "copy_all": True})
    make_file(car / "Private" / "Common Setups" / "base.sto")
    make_file(car / "Private" / "Data packs" / "pack.csv", "data")

    sync.perform_sync(ops(copy_all=True), str(iracing), base_config)
    assert (car / "Shared" / "Common Setups" / "Data packs" / "pack.csv").exists()
    assert (car / "Shared" / "Drivers" / "Ann" / "Data packs" / "pack.csv").exists()
    assert not (car / "Shared" / "Data packs").exists()


def test_grouped_cars_share_their_source_folders(tmp_path, base_config, ops, make_file):
    root = tmp_path / "grouped"
    trucks = CAR_GROUPS["nascar trucks"]
    for car in trucks:
        (root / car).mkdir(parents=True)
    make_file(root / trucks[0] / "Private" / "truck.sto")
    base_config["iracing_folder"] = str(root)

    sync.perform_sync(ops(), str(root), base_config)
    for car in trucks:
        assert (root / car / "Private" / "truck.sto").exists(), car
        assert (root / car / "Shared" / "truck.sto").exists(), car


def test_bidirectional_sync_prefers_the_newest_file(tmp_path, ops, make_file):
    left = tmp_path / "a"
    right = tmp_path / "b"
    make_file(left / "s.sto", "old")
    make_file(right / "s.sto", "new")
    old_time = time.time() - 600
    os.utime(left / "s.sto", (old_time, old_time))

    sync.sync_bidirectional(ops(), str(left), str(right))
    assert (left / "s.sto").read_text() == "new"


def test_extra_folders_are_merged_into_the_source(iracing, base_config, ops, make_file):
    car = iracing / "ferrari296gt3"
    base_config.update({"use_external": True, "extra_folders": [{"name": "Garage 61", "location": "car"}]})
    make_file(car / "Garage 61" / "g61.sto")

    sync.perform_sync(ops(), str(iracing), base_config)
    assert (car / "Private" / "Garage 61" / "g61.sto").exists()
    assert (car / "Garage 61" / "g61.sto").exists()  # car-root extras are not deleted


def test_extra_folders_inside_the_destination_are_absorbed_by_the_source(iracing, base_config, ops, make_file):
    """A "dest" extra folder is moved into the source, then re-published from it."""
    car = iracing / "ferrari296gt3"
    base_config.update({"use_external": True, "extra_folders": [{"name": "Inbox", "location": "dest"}]})
    make_file(car / "Shared" / "Inbox" / "new.sto")

    sync.perform_sync(ops(), str(iracing), base_config)
    assert (car / "Private" / "Inbox" / "new.sto").exists()
    assert (car / "Shared" / "Inbox" / "new.sto").exists()


def test_same_source_and_destination_is_rejected(iracing, base_config, ops):
    base_config["sync_destination"] = base_config["sync_source"]
    with pytest.raises(sync.SyncError):
        sync.perform_sync(ops(), str(iracing), base_config)


def test_missing_iracing_folder_is_rejected(base_config, ops, tmp_path):
    with pytest.raises(sync.SyncError):
        sync.perform_sync(ops(), str(tmp_path / "nope"), base_config)


def test_unconfigured_sync_folders_are_skipped_not_crashed(iracing, base_config, ops):
    base_config["sync_source"] = ""
    stats = sync.perform_sync(ops(), str(iracing), base_config)
    assert stats.changed == 0


def test_dry_run_changes_nothing(iracing, base_config, ops, make_file, logger):
    car = iracing / "ferrari296gt3"
    make_file(car / "Private" / "fast.sto")
    before = sorted(str(p.relative_to(iracing)) for p in iracing.rglob("*"))

    stats = sync.run_sync(base_config, logger, dry_run=True)
    after = sorted(str(p.relative_to(iracing)) for p in iracing.rglob("*"))
    assert before == after
    assert stats.files_copied > 0


def test_run_sync_creates_backups(iracing, base_config, ops, make_file, logger, tmp_path):
    make_file(iracing / "ferrari296gt3" / "Private" / "fast.sto")
    backup = tmp_path / "backup"
    base_config.update({"backup_enabled": True, "backup_before_folder": str(backup)})

    sync.run_sync(base_config, logger)
    assert (backup / "ferrari296gt3" / "Private" / "fast.sto").exists()


def test_run_sync_requires_a_configured_folder(base_config, logger):
    base_config["iracing_folder"] = ""
    with pytest.raises(sync.SyncError):
        sync.run_sync(base_config, logger)


def test_garage61_failure_keeps_the_existing_driver_list(base_config, logger, monkeypatch):
    base_config.update({"use_garage61": True, "garage61_team_id": "42", "drivers": ["Ann"]})
    monkeypatch.setattr("nishizumi_sync.garage61.fetch_drivers", lambda *a, **k: None)
    assert sync.refresh_drivers_from_garage61(base_config, logger) is False
    assert base_config["drivers"] == ["Ann"]


def test_garage61_success_updates_the_driver_list(base_config, logger, monkeypatch, tmp_path):
    base_config.update({"use_garage61": True, "garage61_team_id": "42", "drivers": ["Ann"]})
    monkeypatch.setattr("nishizumi_sync.garage61.fetch_drivers", lambda *a, **k: ["Ann", "Bob"])
    path = tmp_path / "cfg.json"
    assert sync.refresh_drivers_from_garage61(base_config, logger, config_path=path) is True
    assert base_config["drivers"] == ["Ann", "Bob"]
    assert path.exists()
