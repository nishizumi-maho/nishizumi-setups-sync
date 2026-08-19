from __future__ import annotations

import json

import pytest

from nishizumi_sync import config


def test_defaults_are_independent_copies():
    first = config.default_config()
    first["profiles"][0]["team_folder"] = "changed"
    assert config.default_config()["profiles"][0]["team_folder"] != "changed"


def test_legacy_keys_are_migrated():
    cfg = config.migrate(
        {
            "driver_folder": "Supplier X",
            "external_folder": "Garage 61",
            "backup_folder": "D:/backup",
            "team_folder": "Old Team",
        }
    )
    assert cfg["supplier_folder"] == "Supplier X"
    assert cfg["extra_folders"] == [{"name": "Garage 61", "location": "car"}]
    assert cfg["backup_before_folder"] == "D:/backup"
    # Flat folder names seed the first profile instead of being lost.
    assert cfg["profiles"][0]["team_folder"] == "Old Team"
    assert "driver_folder" not in cfg and "external_folder" not in cfg


def test_extra_folders_normalisation_dedupes_and_validates():
    cfg = config.migrate(
        {
            "extra_folders": [
                "Garage 61",
                {"name": "Garage 61", "location": "car"},
                {"folder": "Telemetry", "location": "dest"},
                {"name": "Bad", "location": "nowhere"},
                {"name": ""},
                42,
            ]
        }
    )
    assert cfg["extra_folders"] == [
        {"name": "Garage 61", "location": "car"},
        {"name": "Telemetry", "location": "dest"},
        {"name": "Bad", "location": "car"},
    ]


def test_out_of_range_active_profile_is_clamped():
    cfg = config.migrate({"active_profile": 9, "profiles": [{"name": "A"}, {"name": "B"}]})
    assert cfg["active_profile"] == 1


def test_invalid_scalars_fall_back_to_defaults():
    cfg = config.migrate(
        {"hash_algorithm": "crc32", "source_type": "ftp", "log_level": "LOUD", "tray_interval": "not-a-number"}
    )
    assert cfg["hash_algorithm"] == "md5"
    assert cfg["source_type"] == "zip"
    assert cfg["log_level"] == "INFO"
    assert cfg["tray_interval"] == 2


def test_drivers_are_cleaned_and_deduped():
    cfg = config.migrate({"drivers": ["Ann/Lee", "ann lee", " Bob ", "Bob", ""]})
    assert cfg["drivers"] == ["AnnLee", "ann lee", "Bob"]


def test_profile_round_trip(tmp_path):
    path = tmp_path / "cfg.json"
    cfg = config.default_config()
    cfg["profiles"] = [
        {"name": "One", "team_folder": "T1", "personal_folder": "P1", "supplier_folder": "S1", "season_folder": "X"},
        {"name": "Two", "team_folder": "T2", "personal_folder": "P2", "supplier_folder": "S2", "season_folder": "Y"},
    ]
    cfg["active_profile"] = 1
    config.apply_active_profile(cfg)
    assert cfg["team_folder"] == "T2"

    cfg["team_folder"] = "Renamed"
    config.save_config(cfg, path)
    reloaded = config.load_config(path)
    assert reloaded["profiles"][1]["team_folder"] == "Renamed"
    assert reloaded["team_folder"] == "Renamed"
    assert reloaded["profiles"][0]["team_folder"] == "T1"


def test_save_is_atomic_and_valid_json(tmp_path):
    path = tmp_path / "cfg.json"
    config.save_config(config.default_config(), path)
    assert json.loads(path.read_text())["hash_algorithm"] == "md5"
    assert list(tmp_path.glob("*.tmp")) == []


def test_corrupt_config_is_quarantined(tmp_path, logger):
    path = tmp_path / "cfg.json"
    path.write_text("{not json", encoding="utf-8")
    cfg = config.load_config(path, logger=logger)
    assert cfg["hash_algorithm"] == "md5"
    assert not path.exists()
    assert list(tmp_path.glob("cfg.json.corrupt-*"))


def test_save_reports_failure_instead_of_swallowing_it(tmp_path):
    # A directory cannot be replaced by a file: the write must raise, not pretend
    # to have succeeded the way the 1.x code did.
    target = tmp_path / "in-the-way"
    target.mkdir()
    (target / "keep").write_text("x", encoding="utf-8")
    with pytest.raises(config.ConfigError):
        config.save_config(config.default_config(), target)


def test_updates_section_is_validated():
    cfg = config.migrate({"updates": {"channel": "nightly", "check_interval_hours": -5, "auto_install": "yes"}})
    assert cfg["updates"]["channel"] == "stable"
    assert cfg["updates"]["check_interval_hours"] == 0
    assert cfg["updates"]["auto_install"] is True
