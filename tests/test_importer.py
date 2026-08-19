from __future__ import annotations

import zipfile

import pytest

from nishizumi_sync import importer


def make_zip(path, entries: dict[str, str]) -> None:
    with zipfile.ZipFile(path, "w") as bundle:
        for name, content in entries.items():
            bundle.writestr(name, content)


def test_zip_slip_is_rejected(tmp_path):
    archive = tmp_path / "evil.zip"
    make_zip(archive, {"../escaped.sto": "x"})
    with pytest.raises(importer.ImportError_):
        importer.safe_extract_zip(archive, tmp_path / "out")
    assert not (tmp_path / "escaped.sto").exists()


def test_absolute_paths_in_archives_are_rejected(tmp_path):
    archive = tmp_path / "evil.zip"
    make_zip(archive, {"/etc/whatever.sto": "x"})
    with pytest.raises(importer.ImportError_):
        importer.safe_extract_zip(archive, tmp_path / "out")


def test_symlink_entries_are_rejected(tmp_path):
    archive = tmp_path / "link.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        info = zipfile.ZipInfo("link")
        info.external_attr = (0o120777 << 16)
        bundle.writestr(info, "/etc/passwd")
    with pytest.raises(importer.ImportError_):
        importer.safe_extract_zip(archive, tmp_path / "out")


def test_import_from_archive(tmp_path, iracing, base_config, ops):
    archive = tmp_path / "pack.zip"
    make_zip(archive, {"03 - Ferrari GT3/fast.sto": "data", "04 - BMW GT3/quick.sto": "data"})

    unresolved = importer.import_archive(ops(), str(archive), str(iracing), base_config)
    assert unresolved == []
    assert (iracing / "ferrari296gt3" / "Team" / "Supplier" / "2025S2" / "fast.sto").exists()
    assert (iracing / "ferrari296gt3" / "Personal" / "Supplier" / "2025S2" / "fast.sto").exists()
    assert (iracing / "bmwm4gt3" / "Team" / "Supplier" / "2025S2" / "quick.sto").exists()


def test_import_descends_through_a_wrapper_folder(tmp_path, iracing, base_config, ops):
    archive = tmp_path / "pack.zip"
    make_zip(archive, {"Pack 2025S2/03 - Ferrari GT3/fast.sto": "data"})

    importer.import_archive(ops(), str(archive), str(iracing), base_config)
    assert (iracing / "ferrari296gt3" / "Team" / "Supplier" / "2025S2" / "fast.sto").exists()


def test_group_folders_import_into_every_variant(tmp_path, base_config, ops):
    from nishizumi_sync.cars import CAR_GROUPS

    root = tmp_path / "nascar-setups"
    root.mkdir()
    source = tmp_path / "src" / "NASCAR Trucks"
    source.mkdir(parents=True)
    (source / "truck.sto").write_text("data", encoding="utf-8")

    importer.import_from_folder(ops(), str(tmp_path / "src"), str(root), base_config)
    for car in CAR_GROUPS["nascar trucks"]:
        assert (root / car / "Team" / "Supplier" / "2025S2" / "truck.sto").exists(), car


def test_unknown_folders_are_reported_and_can_be_mapped(tmp_path, iracing, base_config, ops):
    source = tmp_path / "src"
    (source / "Mystery Machine").mkdir(parents=True)
    (source / "Mystery Machine" / "a.sto").write_text("data", encoding="utf-8")

    unresolved = importer.import_from_folder(ops(), str(source), str(iracing), base_config)
    assert unresolved == ["Mystery Machine"]

    mapping_path = tmp_path / "map.json"
    unresolved = importer.import_from_folder(
        ops(),
        str(source),
        str(iracing),
        base_config,
        on_unknown_folder=lambda folder: "ferrari296gt3",
        mapping_path=mapping_path,
    )
    assert unresolved == []
    assert (iracing / "ferrari296gt3" / "Team" / "Supplier" / "2025S2" / "a.sto").exists()
    # The answer is remembered for next time.
    assert "mystery machine" in mapping_path.read_text()


def test_driver_mode_import_layout(tmp_path, iracing, base_config, ops):
    base_config.update({"use_driver_folders": True, "drivers": ["Ann Lee"]})
    source = tmp_path / "src" / "Ferrari GT3"
    source.mkdir(parents=True)
    (source / "a.sto").write_text("data", encoding="utf-8")

    importer.import_from_folder(ops(), str(tmp_path / "src"), str(iracing), base_config)
    car = iracing / "ferrari296gt3"
    assert (car / "Team" / "Common Setups" / "Supplier" / "2025S2" / "a.sto").exists()
    assert (car / "Team" / "Drivers" / "Ann Lee" / "Supplier" / "2025S2" / "a.sto").exists()


def test_dry_run_import_writes_nothing(tmp_path, iracing, base_config, ops):
    archive = tmp_path / "pack.zip"
    make_zip(archive, {"Ferrari GT3/fast.sto": "data"})

    operations = ops(dry_run=True)
    importer.import_archive(operations, str(archive), str(iracing), base_config)
    assert list((iracing / "ferrari296gt3").iterdir()) == []
    assert operations.stats.files_copied >= 1


def test_import_requires_configured_folder_names(tmp_path, iracing, base_config, ops):
    base_config["season_folder"] = ""
    with pytest.raises(importer.ImportError_):
        importer.import_from_folder(ops(), str(tmp_path), str(iracing), base_config)


def test_missing_path_raises(tmp_path, iracing, base_config, ops):
    with pytest.raises(importer.ImportError_):
        importer.import_path(ops(), str(tmp_path / "nope"), str(iracing), base_config)


def test_unsupported_archive_type(tmp_path, iracing, base_config, ops):
    archive = tmp_path / "pack.7z"
    archive.write_bytes(b"not an archive")
    with pytest.raises(importer.ImportError_):
        importer.import_archive(ops(), str(archive), str(iracing), base_config)


def test_archive_setup_folders_lists_car_folders(tmp_path):
    archive = tmp_path / "pack.zip"
    make_zip(archive, {"Pack/01 - Ferrari GT3/a.sto": "x", "Pack/02 - Nope/a.sto": "x"})
    assert importer.archive_setup_folders(archive) == ["01 - Ferrari GT3", "02 - Nope"]


def test_configured_setup_folders_for_each_mode(tmp_path):
    archive = tmp_path / "pack.zip"
    make_zip(archive, {"Ferrari GT3/a.sto": "x"})
    assert importer.configured_setup_folders({"source_type": "zip", "zip_file": str(archive)}) == ["Ferrari GT3"]

    folder = tmp_path / "src"
    (folder / "BMW GT3").mkdir(parents=True)
    assert importer.configured_setup_folders({"source_type": "folder", "source_folder": str(folder)}) == ["BMW GT3"]
    assert importer.configured_setup_folders({"source_type": "none"}) == []


def test_group_import_only_touches_cars_you_own(tmp_path, base_config, ops):
    from nishizumi_sync.cars import CAR_GROUPS

    trucks = CAR_GROUPS["nascar trucks"]
    root = tmp_path / "owned-setups"
    (root / trucks[0]).mkdir(parents=True)
    (root / trucks[1]).mkdir(parents=True)
    source = tmp_path / "src" / "NASCAR Trucks"
    source.mkdir(parents=True)
    (source / "oval.sto").write_text("data", encoding="utf-8")

    importer.import_from_folder(ops(), str(tmp_path / "src"), str(root), base_config)
    assert (root / trucks[0] / "Team" / "Supplier" / "2025S2" / "oval.sto").exists()
    assert (root / trucks[1] / "Team" / "Supplier" / "2025S2" / "oval.sto").exists()
    assert not (root / trucks[2]).exists()
