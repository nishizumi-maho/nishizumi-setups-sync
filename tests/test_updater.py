from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from nishizumi_sync import updater
from nishizumi_sync.http import HttpError


class FakeResponse:
    def __init__(self, payload, text=""):
        self._payload = payload
        self.text = text

    def json(self):
        return self._payload


def release(tag, *, prerelease=False, draft=False, assets=(), body="notes"):
    return {
        "tag_name": tag,
        "name": f"Release {tag}",
        "body": body,
        "html_url": f"https://example.invalid/{tag}",
        "prerelease": prerelease,
        "draft": draft,
        "published_at": "2026-01-01T00:00:00Z",
        "zipball_url": f"https://example.invalid/{tag}.zip",
        "assets": list(assets),
    }


def asset(name, *, digest="", size=10):
    return {
        "name": name,
        "browser_download_url": f"https://example.invalid/{name}",
        "size": size,
        "digest": digest,
    }


# ---------------------------------------------------------------------------
# Version comparison
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "candidate,current,expected",
    [
        ("2.0.0", "1.1.0", True),
        ("1.1.0", "2.0.0", False),
        ("2.0.0", "2.0.0", False),
        ("v2.0.1", "2.0.0", True),
        ("2.1", "2.0.9", True),
        # A pre-release is older than the final release of the same number.
        ("1.1.0", "1.1.0-fullgui", True),
        ("1.1.0-fullgui", "1.1.0", False),
        ("2.0.0-beta.10", "2.0.0-beta.2", True),
        ("2.0.0-beta.2", "2.0.0-beta.10", False),
        ("2.0.0", "2.0.0-rc.1", True),
        ("not-a-version", "1.0.0", False),
    ],
)
def test_is_newer(candidate, current, expected):
    assert updater.is_newer(candidate, current) is expected


def test_version_parse_rejects_rubbish():
    assert updater.Version.parse("") is None
    assert updater.Version.parse("banana") is None
    assert updater.Version.parse("v1.2.3").release == (1, 2, 3)


def test_versions_sort_correctly():
    versions = [updater.Version.parse(v) for v in ["1.0.0", "2.0.0-rc.1", "2.0.0", "1.9.9"]]
    assert [str(v) for v in sorted(versions)] == ["1.0.0", "1.9.9", "2.0.0-rc.1", "2.0.0"]


# ---------------------------------------------------------------------------
# Release metadata
# ---------------------------------------------------------------------------

def test_update_info_from_api():
    info = updater.UpdateInfo.from_api(release("v2.1.0", assets=[asset("NishizumiSync.exe")]))
    assert info is not None
    assert str(info.version) == "v2.1.0"
    assert info.assets[0].name == "NishizumiSync.exe"


def test_update_info_ignores_unparseable_tags():
    assert updater.UpdateInfo.from_api({"tag_name": "nightly", "name": "nightly"}) is None


def test_asset_digest_is_normalised():
    parsed = updater.ReleaseAsset.from_api(asset("a.exe", digest="sha256:abcdef"))
    assert parsed.digest == "abcdef"
    assert updater.ReleaseAsset.from_api(asset("a.exe", digest="md5:zzz")).digest == ""


def test_asset_for_platform_prefers_the_matching_artefact(monkeypatch):
    info = updater.UpdateInfo.from_api(
        release(
            "v2.1.0",
            assets=[asset("SHA256SUMS"), asset("NishizumiSync-linux"), asset("NishizumiSync-windows.exe")],
        )
    )
    monkeypatch.setattr(updater.sys, "platform", "win32")
    assert info.asset_for_platform().name == "NishizumiSync-windows.exe"
    monkeypatch.setattr(updater.sys, "platform", "linux")
    assert info.asset_for_platform().name == "NishizumiSync-linux"


def test_checksum_asset_is_recognised():
    info = updater.UpdateInfo.from_api(release("v2.1.0", assets=[asset("SHA256SUMS"), asset("app.exe")]))
    assert info.checksum_asset().name == "SHA256SUMS"


def test_find_checksum_parses_sha256sums_format():
    body = "aaa111  app-linux\nbbb222 *NishizumiSync.exe\n"
    assert updater._find_checksum(body, "NishizumiSync.exe") == "bbb222"
    assert updater._find_checksum(body, "missing.exe") == ""


# ---------------------------------------------------------------------------
# State and throttling
# ---------------------------------------------------------------------------

def test_state_round_trip(tmp_path):
    path = tmp_path / "state.json"
    state = updater.UpdateState(last_check="2026-01-01T00:00:00+00:00", skipped_version="9.9.9")
    state.save(path)
    assert updater.UpdateState.load(path).skipped_version == "9.9.9"


def test_state_tolerates_a_broken_file(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("not json", encoding="utf-8")
    assert updater.UpdateState.load(path).last_check == ""


def test_due_for_check_respects_the_interval(tmp_path):
    cfg = {"updates": {"check_on_startup": True, "check_interval_hours": 24}}
    state_path = tmp_path / "state.json"
    up = updater.Updater(cfg, state_path=state_path)
    assert up.due_for_check() is True

    up.state.last_check = datetime.now(timezone.utc).isoformat(timespec="seconds")
    assert up.due_for_check() is False

    up.state.last_check = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat(timespec="seconds")
    assert up.due_for_check() is True


def test_check_on_startup_disabled_blocks_scheduled_checks(tmp_path):
    up = updater.Updater({"updates": {"check_on_startup": False}}, state_path=tmp_path / "s.json")
    assert up.due_for_check() is False


def test_interval_zero_means_every_start(tmp_path):
    up = updater.Updater(
        {"updates": {"check_on_startup": True, "check_interval_hours": 0}}, state_path=tmp_path / "s.json"
    )
    up.state.last_check = datetime.now(timezone.utc).isoformat(timespec="seconds")
    assert up.due_for_check() is True


# ---------------------------------------------------------------------------
# Checking against the API
# ---------------------------------------------------------------------------

def _updater(monkeypatch, payload, tmp_path, cfg=None, version="2.0.0"):
    monkeypatch.setattr(updater, "get", lambda *a, **k: FakeResponse(payload))
    return updater.Updater(
        cfg or {"updates": {"check_on_startup": True}},
        current_version=version,
        state_path=tmp_path / "state.json",
    )


def test_check_finds_a_newer_release(monkeypatch, tmp_path, logger):
    up = _updater(monkeypatch, [release("v2.1.0"), release("v2.0.0")], tmp_path)
    up.logger = logger
    info = up.check(force=True)
    assert info is not None and str(info.version) == "v2.1.0"
    assert up.state.last_seen_version == "v2.1.0"


def test_check_returns_none_when_up_to_date(monkeypatch, tmp_path, logger):
    up = _updater(monkeypatch, [release("v2.0.0")], tmp_path)
    up.logger = logger
    assert up.check(force=True) is None


def test_prereleases_are_hidden_on_the_stable_channel(monkeypatch, tmp_path, logger):
    payload = [release("v2.1.0-beta.1", prerelease=True), release("v2.0.0")]
    up = _updater(monkeypatch, payload, tmp_path)
    up.logger = logger
    assert up.check(force=True) is None

    up.cfg = {"updates": {"check_on_startup": True, "channel": "prerelease"}}
    assert str(up.check(force=True).version) == "v2.1.0-beta.1"


def test_drafts_are_ignored(monkeypatch, tmp_path, logger):
    up = _updater(monkeypatch, [release("v3.0.0", draft=True), release("v2.0.0")], tmp_path)
    up.logger = logger
    assert up.check(force=True) is None


def test_skipped_versions_are_not_offered_again(monkeypatch, tmp_path, logger):
    up = _updater(monkeypatch, [release("v2.1.0")], tmp_path)
    up.logger = logger
    info = up.check(force=True)
    up.skip(info)
    assert up.check(force=False, record=False) is None
    # Asking explicitly still surfaces it.
    assert up.check(force=True) is not None


def test_network_failure_is_reported_as_no_update(monkeypatch, tmp_path, logger):
    def boom(*args, **kwargs):
        raise HttpError("no network")

    monkeypatch.setattr(updater, "get", boom)
    up = updater.Updater({}, logger, current_version="2.0.0", state_path=tmp_path / "s.json")
    assert up.check(force=True) is None


def test_unexpected_payload_is_handled(monkeypatch, tmp_path, logger):
    up = _updater(monkeypatch, {"message": "Not Found"}, tmp_path)
    up.logger = logger
    assert up.check(force=True) is None


def test_check_records_the_timestamp(monkeypatch, tmp_path, logger):
    up = _updater(monkeypatch, [release("v2.0.0")], tmp_path)
    up.logger = logger
    up.check(force=True)
    assert up.state.last_check
    assert json.loads((tmp_path / "state.json").read_text())["last_check"]


# ---------------------------------------------------------------------------
# Installing
# ---------------------------------------------------------------------------

def test_git_checkouts_are_told_to_pull(monkeypatch, tmp_path, logger):
    monkeypatch.setattr(updater, "detect_install_mode", lambda: updater.MODE_GIT)
    up = updater.Updater({}, logger, state_path=tmp_path / "s.json")
    info = updater.UpdateInfo.from_api(release("v2.1.0"))
    result = up.install(info)
    assert result.ok is False
    assert "git pull" in result.message


def test_pip_installs_are_told_to_upgrade(monkeypatch, tmp_path, logger):
    monkeypatch.setattr(updater, "detect_install_mode", lambda: updater.MODE_PIP)
    up = updater.Updater({}, logger, state_path=tmp_path / "s.json")
    result = up.install(updater.UpdateInfo.from_api(release("v2.1.0")))
    assert result.ok is False
    assert "pip install" in result.message


def test_frozen_install_without_a_matching_asset_fails_clearly(monkeypatch, tmp_path, logger):
    monkeypatch.setattr(updater, "detect_install_mode", lambda: updater.MODE_FROZEN)
    up = updater.Updater({}, logger, state_path=tmp_path / "s.json")
    info = updater.UpdateInfo.from_api(release("v2.1.0"))
    result = up.install(info)
    assert result.ok is False
    assert "no download for this platform" in result.message


def test_checksum_mismatch_aborts_the_update(monkeypatch, tmp_path, logger):
    target = tmp_path / "app.exe"
    target.write_bytes(b"payload")
    monkeypatch.setattr(updater, "download", lambda url, dest, **k: dest.write_bytes(b"payload") or dest)
    up = updater.Updater({}, logger, state_path=tmp_path / "s.json")
    info = updater.UpdateInfo.from_api(release("v2.1.0", assets=[asset("app.exe", digest="sha256:deadbeef")]))
    with pytest.raises(updater.UpdateError, match="Checksum mismatch"):
        up._verify(target, info.assets[0], info)


def test_checksum_match_passes(tmp_path, logger):
    import hashlib

    target = tmp_path / "app.exe"
    target.write_bytes(b"payload")
    digest = hashlib.sha256(b"payload").hexdigest()
    up = updater.Updater({}, logger, state_path=tmp_path / "s.json")
    info = updater.UpdateInfo.from_api(release("v2.1.0", assets=[asset("app.exe", digest=f"sha256:{digest}")]))
    up._verify(target, info.assets[0], info)  # must not raise


def test_note_startup_confirms_an_applied_update(tmp_path, logger):
    state_path = tmp_path / "s.json"
    updater.UpdateState(pending_version="2.0.0").save(state_path)
    up = updater.Updater({}, logger, current_version="2.0.0", state_path=state_path)
    assert "Updated to version 2.0.0" in up.note_startup()
    # Reported only once.
    assert updater.Updater({}, logger, current_version="2.0.0", state_path=state_path).note_startup() is None


def test_summarise_release_trims_long_notes():
    info = updater.UpdateInfo.from_api(release("v2.1.0", body="\n".join(f"line {i}" for i in range(50))))
    summary = updater.summarise_release(info, max_lines=5)
    assert summary.count("\n") == 5
    assert summary.endswith("…")


def test_swap_script_is_generated_for_the_platform(tmp_path, monkeypatch):
    new_file = tmp_path / "new.exe"
    new_file.write_bytes(b"x")
    monkeypatch.setattr(updater.sys, "platform", "linux")
    script = updater._write_swap_script(new_file, tmp_path / "app", 4242)
    body = script.read_text()
    assert script.suffix == ".sh"
    assert "kill -0 4242" in body and str(new_file) in body


# ---------------------------------------------------------------------------
# Installing over a source checkout
# ---------------------------------------------------------------------------

def _fake_zipball(path, tag="v2.1.0", package_files=None, extra=()):
    """Build an archive shaped like GitHub's source zipball."""
    import zipfile

    root = f"nishizumi-maho-Nishizumi-Sync-{tag}"
    files = package_files or {"__init__.py": '__version__ = "2.1.0"\n', "sync.py": "# new\n"}
    with zipfile.ZipFile(path, "w") as bundle:
        for name, content in files.items():
            bundle.writestr(f"{root}/nishizumi_sync/{name}", content)
        for name, content in extra:
            bundle.writestr(f"{root}/{name}", content)
    return path


def test_source_install_replaces_the_package(monkeypatch, tmp_path, logger):
    install = tmp_path / "app"
    (install / "nishizumi_sync").mkdir(parents=True)
    (install / "nishizumi_sync" / "__init__.py").write_text('__version__ = "2.0.0"\n', encoding="utf-8")
    (install / "nishizumi_sync" / "stale.py").write_text("# removed upstream\n", encoding="utf-8")
    (install / "nishizumi_setups_sync.py").write_text("# old launcher\n", encoding="utf-8")

    archive = _fake_zipball(tmp_path / "src.zip", extra=[("nishizumi_setups_sync.py", "# new launcher\n")])
    monkeypatch.setattr(updater, "install_dir", lambda: install)

    up = updater.Updater({}, logger, state_path=tmp_path / "s.json")
    info = updater.UpdateInfo.from_api(release("v2.1.0"))
    result = up._install_source(archive, info)

    assert result.ok is True and result.restart_required is True
    assert '__version__ = "2.1.0"' in (install / "nishizumi_sync" / "__init__.py").read_text()
    assert (install / "nishizumi_setups_sync.py").read_text() == "# new launcher\n"
    # Files dropped upstream do not linger, and no backup is left behind.
    assert not (install / "nishizumi_sync" / "stale.py").exists()
    assert not (install / "nishizumi_sync.backup").exists()


def test_source_install_rejects_an_archive_without_the_package(monkeypatch, tmp_path, logger):
    import zipfile

    install = tmp_path / "app"
    (install / "nishizumi_sync").mkdir(parents=True)
    (install / "nishizumi_sync" / "__init__.py").write_text("keep me", encoding="utf-8")

    archive = tmp_path / "wrong.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("something-else/readme.md", "not the app")
    monkeypatch.setattr(updater, "install_dir", lambda: install)

    up = updater.Updater({}, logger, state_path=tmp_path / "s.json")
    with pytest.raises(updater.UpdateError):
        up._install_source(archive, updater.UpdateInfo.from_api(release("v2.1.0")))
    # The existing installation is untouched.
    assert (install / "nishizumi_sync" / "__init__.py").read_text() == "keep me"


def test_source_install_refuses_a_zip_slip_archive(monkeypatch, tmp_path, logger):
    import zipfile

    install = tmp_path / "app"
    install.mkdir()
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("../escaped.py", "pwned")
    monkeypatch.setattr(updater, "install_dir", lambda: install)

    up = updater.Updater({}, logger, state_path=tmp_path / "s.json")
    with pytest.raises(updater.UpdateError, match="escapes"):
        up._install_source(archive, updater.UpdateInfo.from_api(release("v2.1.0")))
    assert not (tmp_path / "escaped.py").exists()


def test_install_records_the_pending_version(monkeypatch, tmp_path, logger):
    install = tmp_path / "app"
    (install / "nishizumi_sync").mkdir(parents=True)
    archive = _fake_zipball(tmp_path / "src.zip")

    monkeypatch.setattr(updater, "install_dir", lambda: install)
    monkeypatch.setattr(updater, "detect_install_mode", lambda: updater.MODE_SOURCE)
    monkeypatch.setattr(updater, "download", lambda url, dest, **k: (dest.write_bytes(archive.read_bytes()), dest)[1])

    state_path = tmp_path / "s.json"
    up = updater.Updater({}, logger, state_path=state_path)
    result = up.install(updater.UpdateInfo.from_api(release("v2.1.0")))

    assert result.ok is True, result.message
    assert updater.UpdateState.load(state_path).pending_version == "v2.1.0"
