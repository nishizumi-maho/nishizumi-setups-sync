"""Garage61, plugins and the HTTP fallback layer."""

from __future__ import annotations

import pytest

from nishizumi_sync import garage61, http, plugins


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


@pytest.mark.parametrize(
    "payload",
    [
        {"drivers": [{"name": "Ann Lee"}, {"name": "Bob"}]},
        {"members": [{"displayName": "Ann Lee"}, {"displayName": "Bob"}]},
        [{"firstName": "Ann", "lastName": "Lee"}, {"name": "Bob"}],
        {"items": ["Ann Lee", "Bob"]},
    ],
)
def test_driver_payload_shapes_are_all_accepted(monkeypatch, logger, payload):
    monkeypatch.setattr(garage61, "get", lambda *a, **k: FakeResponse(payload))
    assert garage61.fetch_drivers("42", None, logger) == ["Ann Lee", "Bob"]


def test_duplicate_and_empty_names_are_dropped(monkeypatch, logger):
    payload = {"drivers": [{"name": "Ann"}, {"name": "ann"}, {"name": " "}, {"name": "Bob"}]}
    monkeypatch.setattr(garage61, "get", lambda *a, **k: FakeResponse(payload))
    assert garage61.fetch_drivers("42", "key", logger) == ["Ann", "Bob"]


def test_empty_team_id_is_rejected_without_a_request(monkeypatch, logger):
    def boom(*args, **kwargs):
        raise AssertionError("no request expected")

    monkeypatch.setattr(garage61, "get", boom)
    assert garage61.fetch_drivers("", None, logger) is None


@pytest.mark.parametrize("status", [401, 403, 404, 500])
def test_http_errors_return_none(monkeypatch, logger, status):
    def boom(*args, **kwargs):
        raise http.HttpError("failed", status)

    monkeypatch.setattr(garage61, "get", boom)
    assert garage61.fetch_drivers("42", None, logger) is None


def test_an_empty_driver_list_is_treated_as_a_failure(monkeypatch, logger):
    monkeypatch.setattr(garage61, "get", lambda *a, **k: FakeResponse({"drivers": []}))
    assert garage61.fetch_drivers("42", None, logger) is None


def test_plugins_are_opt_in(tmp_path, logger):
    hook = tmp_path / "before_sync.py"
    hook.write_text("def execute(config, logger):\n    config['ran'] = True\n", encoding="utf-8")
    cfg = {}
    assert plugins.run_hook("before_sync", cfg, logger, enabled=False, directory=tmp_path) is False
    assert cfg == {}
    assert plugins.run_hook("before_sync", cfg, logger, enabled=True, directory=tmp_path) is True
    assert cfg["ran"] is True


def test_missing_hook_is_not_an_error(tmp_path, logger):
    assert plugins.run_hook("after_sync", {}, logger, enabled=True, directory=tmp_path) is False


def test_broken_hook_is_reported_but_does_not_raise(tmp_path, logger):
    (tmp_path / "after_sync.py").write_text("raise RuntimeError('boom')\n", encoding="utf-8")
    assert plugins.run_hook("after_sync", {}, logger, enabled=True, directory=tmp_path) is False


def test_hook_without_execute_is_reported(tmp_path, logger):
    (tmp_path / "after_sync.py").write_text("VALUE = 1\n", encoding="utf-8")
    assert plugins.run_hook("after_sync", {}, logger, enabled=True, directory=tmp_path) is False


def test_unknown_hook_name_is_rejected(logger):
    with pytest.raises(ValueError):
        plugins.run_hook("during_sync", {}, logger, enabled=True)


def test_available_hooks(tmp_path):
    (tmp_path / "before_sync.py").write_text("", encoding="utf-8")
    assert plugins.available_hooks(tmp_path) == ["before_sync"]


def test_urllib_fallback_is_used_when_requests_is_missing(monkeypatch, tmp_path):
    """The updater and Garage61 must still work without the requests package."""
    monkeypatch.setattr(http, "requests", None)
    calls = {}

    class FakeHandle:
        status = 200
        headers = {"Content-Length": "5"}

        def read(self, *args):
            if calls.get("read"):
                return b""
            calls["read"] = True
            return b"hello"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(http.urllib.request, "urlopen", lambda *a, **k: FakeHandle())
    assert http.get("https://example.invalid").text == "hello"

    calls.clear()
    target = tmp_path / "out.bin"
    http.download("https://example.invalid", target)
    assert target.read_bytes() == b"hello"
