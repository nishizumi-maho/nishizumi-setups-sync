from __future__ import annotations

import hashlib
import os

import pytest

from nishizumi_sync import fileops


def test_dry_run_never_touches_the_filesystem(tmp_path, ops, make_file):
    source = tmp_path / "src"
    make_file(source / "a.sto")
    target = tmp_path / "dst"

    operations = ops(dry_run=True)
    operations.copy_tree(source, target)

    assert not target.exists()
    assert operations.stats.files_copied == 1
    assert operations.stats.dirs_created == 1


def test_copy_tree_filters_non_setup_files(tmp_path, ops, make_file):
    source = tmp_path / "src"
    make_file(source / "a.sto")
    make_file(source / "notes.txt")

    target = tmp_path / "dst"
    ops().copy_tree(source, target)
    assert sorted(p.name for p in target.iterdir()) == ["a.sto"]

    other = tmp_path / "all"
    ops(copy_all=True).copy_tree(source, other)
    assert sorted(p.name for p in other.iterdir()) == ["a.sto", "notes.txt"]


def test_copy_if_different_skips_identical_files(tmp_path, ops, make_file):
    source = make_file(tmp_path / "a.sto", "same")
    target = make_file(tmp_path / "b.sto", "same")
    operations = ops()
    assert operations.copy_if_different(source, target) is False
    assert operations.stats.files_copied == 0

    make_file(tmp_path / "b.sto", "different")
    assert operations.copy_if_different(source, target) is True


def test_files_differ_uses_size_shortcut(tmp_path, ops, make_file):
    a = make_file(tmp_path / "a", "short")
    b = make_file(tmp_path / "b", "much longer content")
    assert ops().files_differ(a, b) is True


@pytest.mark.parametrize("algorithm", ["md5", "sha256"])
def test_file_digest_matches_hashlib(tmp_path, make_file, algorithm):
    path = make_file(tmp_path / "f", "content")
    assert fileops.file_digest(path, algorithm) == hashlib.new(algorithm, b"content").hexdigest()


def test_file_digest_returns_none_for_missing_file(tmp_path):
    assert fileops.file_digest(tmp_path / "nope") is None


def test_unknown_algorithm_falls_back_to_md5(tmp_path, make_file):
    path = make_file(tmp_path / "f", "content")
    assert fileops.file_digest(path, "not-a-hash") == hashlib.md5(b"content").hexdigest()


def test_errors_are_counted_not_swallowed(tmp_path, ops):
    operations = ops()
    assert operations.copy_file(tmp_path / "missing.sto", tmp_path / "out.sto") is False
    assert operations.stats.errors == 1


def test_symlinked_directories_are_skipped(tmp_path, ops, make_file):
    source = tmp_path / "src"
    make_file(source / "real" / "a.sto")
    os.symlink(source / "real", source / "loop", target_is_directory=True)
    ops().copy_tree(source, tmp_path / "dst")
    assert (tmp_path / "dst" / "real" / "a.sto").exists()
    assert not (tmp_path / "dst" / "loop").exists()


def test_resolve_within_blocks_escapes(tmp_path):
    assert fileops.resolve_within(tmp_path, "a", "b").name == "b"
    with pytest.raises(ValueError):
        fileops.resolve_within(tmp_path, "..", "etc")


def test_stats_merge_and_summary():
    first = fileops.SyncStats(files_copied=2)
    first.merge(fileops.SyncStats(files_removed=1, errors=1))
    assert (first.files_copied, first.files_removed, first.errors) == (2, 1, 1)
    assert "2 file(s) copied" in first.summary()
    assert first.changed == 3


def test_human_size():
    assert fileops.human_size(512) == "512 B"
    assert fileops.human_size(2048) == "2.0 KB"
    assert fileops.human_size(5 * 1024 * 1024) == "5.0 MB"


def test_iter_dirs_only_returns_directories(tmp_path, make_file):
    root = tmp_path / "root"
    make_file(root / "dir" / "f.sto")
    make_file(root / "file.sto")
    assert list(fileops.iter_dirs(root)) == ["dir"]


def test_safe_listdir_on_missing_path(tmp_path):
    assert fileops.safe_listdir(tmp_path / "nope") == []
