from __future__ import annotations

import logging
from pathlib import Path

import pytest

from nishizumi_sync import paths
from nishizumi_sync.fileops import FileOps


@pytest.fixture(autouse=True)
def data_home(tmp_path, monkeypatch):
    """Keep every test's configuration, mapping and state files isolated."""
    home = tmp_path / "appdata"
    home.mkdir()
    monkeypatch.setenv(paths.HOME_ENV_VAR, str(home))
    return home


@pytest.fixture
def logger():
    log = logging.getLogger("nishizumi_sync.test")
    log.setLevel(logging.DEBUG)
    log.handlers = [logging.NullHandler()]
    log.propagate = False
    return log


@pytest.fixture
def ops(logger):
    def factory(**kwargs):
        return FileOps(logger, **kwargs)

    return factory


def write(path: Path, content: str = "setup") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


@pytest.fixture
def make_file():
    return write


@pytest.fixture
def iracing(tmp_path):
    """An iRacing setups folder with two car directories."""
    root = tmp_path / "setups"
    for car in ("ferrari296gt3", "bmwm4gt3"):
        (root / car).mkdir(parents=True)
    return root


@pytest.fixture
def base_config(iracing):
    return {
        "iracing_folder": str(iracing),
        "source_type": "none",
        "team_folder": "Team",
        "personal_folder": "Personal",
        "supplier_folder": "Supplier",
        "season_folder": "2025S2",
        "sync_source": "Private",
        "sync_destination": "Shared",
        "hash_algorithm": "md5",
        "copy_all": False,
        "delete_extras": True,
        "use_driver_folders": False,
        "drivers": [],
        "updates": {"check_on_startup": False},
    }
