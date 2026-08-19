from __future__ import annotations

import json

import pytest

from nishizumi_sync import cli
from nishizumi_sync.config import save_config


@pytest.fixture
def config_path(tmp_path, base_config):
    path = tmp_path / "cfg.json"
    save_config(base_config, path)
    return path


def test_version_flag_exits_cleanly(capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--version"])
    assert exit_info.value.code == 0
    assert "Nishizumi Setups Sync" in capsys.readouterr().out


def test_config_path_command(capsys, config_path):
    assert cli.main(["--config", str(config_path), "config", "path"]) == cli.EXIT_OK
    assert str(config_path) in capsys.readouterr().out


def test_config_show_masks_the_api_key(capsys, tmp_path, base_config):
    base_config["garage61_api_key"] = "super-secret"
    path = tmp_path / "cfg.json"
    save_config(base_config, path)
    cli.main(["--config", str(path), "config", "show"])
    output = capsys.readouterr().out
    assert "super-secret" not in output
    assert json.loads(output)["garage61_api_key"] == "***"


def test_config_export(tmp_path, config_path, capsys):
    target = tmp_path / "exported.json"
    assert cli.main(["--config", str(config_path), "config", "export", str(target)]) == cli.EXIT_OK
    assert json.loads(target.read_text())["sync_source"] == "Private"


def test_mapping_set_list_and_remove(capsys):
    assert cli.main(["mapping", "set", "My Folder", "ferrari296gt3"]) == cli.EXIT_OK
    cli.main(["mapping", "list"])
    assert "my folder -> ferrari296gt3" in capsys.readouterr().out
    assert cli.main(["mapping", "remove", "My Folder"]) == cli.EXIT_OK
    cli.main(["mapping", "list"])
    assert "No custom mappings" in capsys.readouterr().out


def test_run_syncs_and_reports(capsys, tmp_path, base_config, iracing, make_file):
    make_file(iracing / "ferrari296gt3" / "Private" / "fast.sto")
    path = tmp_path / "cfg.json"
    save_config(base_config, path)

    assert cli.main(["--config", str(path), "--no-update-check", "run"]) == cli.EXIT_OK
    assert (iracing / "ferrari296gt3" / "Shared" / "fast.sto").exists()
    assert "file(s) copied" in capsys.readouterr().out


def test_dry_run_alias_changes_nothing(tmp_path, base_config, iracing, make_file):
    make_file(iracing / "ferrari296gt3" / "Private" / "fast.sto")
    path = tmp_path / "cfg.json"
    save_config(base_config, path)

    assert cli.main(["--config", str(path), "--no-update-check", "dry-run"]) == cli.EXIT_OK
    assert not (iracing / "ferrari296gt3" / "Shared").exists()


def test_run_without_a_folder_fails(tmp_path, base_config):
    base_config["iracing_folder"] = ""
    path = tmp_path / "cfg.json"
    save_config(base_config, path)
    assert cli.main(["--config", str(path), "--no-update-check", "run"]) == cli.EXIT_ERROR


def test_no_update_check_skips_the_network(monkeypatch, tmp_path, base_config, iracing):
    def boom(*args, **kwargs):
        raise AssertionError("the updater must not be contacted")

    monkeypatch.setattr("nishizumi_sync.updater.get", boom)
    path = tmp_path / "cfg.json"
    save_config(base_config, path)
    assert cli.main(["--config", str(path), "--no-update-check", "run"]) == cli.EXIT_OK


def test_import_command(tmp_path, base_config, iracing):
    import zipfile

    archive = tmp_path / "pack.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("Ferrari GT3/fast.sto", "data")
    path = tmp_path / "cfg.json"
    save_config(base_config, path)

    assert cli.main(["--config", str(path), "import", str(archive)]) == cli.EXIT_OK
    assert (iracing / "ferrari296gt3" / "Team" / "Supplier" / "2025S2" / "fast.sto").exists()


def test_import_of_a_missing_path_fails(tmp_path, config_path):
    assert cli.main(["--config", str(config_path), "import", str(tmp_path / "nope.zip")]) == cli.EXIT_ERROR


def test_check_update_reports_up_to_date(monkeypatch, capsys, config_path):
    monkeypatch.setattr("nishizumi_sync.updater.Updater.check", lambda self, **kwargs: None)
    assert cli.main(["--config", str(config_path), "check-update"]) == cli.EXIT_OK
    assert "up to date" in capsys.readouterr().out


def test_check_update_reports_a_new_version(monkeypatch, capsys, config_path):
    from nishizumi_sync.updater import UpdateInfo

    info = UpdateInfo.from_api(
        {"tag_name": "v9.9.9", "name": "Release", "html_url": "https://example.invalid/9", "body": ""}
    )
    monkeypatch.setattr("nishizumi_sync.updater.Updater.check", lambda self, **kwargs: info)
    assert cli.main(["--config", str(config_path), "check-update"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "Update available: v9.9.9" in out
    assert "https://example.invalid/9" in out


def test_legacy_silent_flag_still_runs(tmp_path, base_config, iracing, make_file):
    make_file(iracing / "ferrari296gt3" / "Private" / "fast.sto")
    path = tmp_path / "cfg.json"
    save_config(base_config, path)
    assert cli.main(["--config", str(path), "--no-update-check", "--silent"]) == cli.EXIT_OK
    assert (iracing / "ferrari296gt3" / "Shared" / "fast.sto").exists()


def test_log_level_override_is_applied(tmp_path, base_config):
    import logging

    path = tmp_path / "cfg.json"
    save_config(base_config, path)
    cli.main(["--config", str(path), "--log-level", "DEBUG", "config", "path"])
    assert logging.getLogger("nishizumi_sync").level == logging.DEBUG


def test_import_errors_are_reported_without_a_traceback(tmp_path, base_config, iracing, capsys):
    """A RAR without rarfile, or a broken archive, is a user error not a crash."""
    archive = tmp_path / "pack.rar"
    archive.write_bytes(b"not really a rar")
    base_config.update({"source_type": "zip", "zip_file": str(archive)})
    path = tmp_path / "cfg.json"
    save_config(base_config, path)
    assert cli.main(["--config", str(path), "--no-update-check", "run"]) == cli.EXIT_ERROR


def test_dry_run_never_installs_an_update(monkeypatch, tmp_path, base_config):
    base_config["updates"] = {"check_on_startup": True, "check_interval_hours": 0, "auto_install": True}
    path = tmp_path / "cfg.json"
    save_config(base_config, path)

    from nishizumi_sync.updater import UpdateInfo

    info = UpdateInfo.from_api({"tag_name": "v9.9.9", "name": "R", "body": "", "html_url": ""})
    monkeypatch.setattr("nishizumi_sync.updater.Updater.check", lambda self, **kw: info)
    monkeypatch.setattr("nishizumi_sync.updater.Updater.due_for_check", lambda self, **kw: True)

    def boom(self, info, **kwargs):
        raise AssertionError("a dry run must not install anything")

    monkeypatch.setattr("nishizumi_sync.updater.Updater.install", boom)
    assert cli.main(["--config", str(path), "dry-run"]) == cli.EXIT_OK
