"""Importing supplier setups from archives and folders.

Archives are extracted into a temporary directory (the old code extracted next
to the archive, happily overwriting an existing folder of the same name) and
every member is validated first so a crafted archive cannot write outside the
extraction directory.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Callable, Optional, Sequence

from .cars import clean_name, identify_setup, load_custom_mapping, normalise, save_custom_mapping
from .fileops import FileOps, iter_dirs
from .sync import COMMON_FOLDER, DRIVERS_ROOT, copy_missing_files, sync_folders

try:  # optional dependency
    import rarfile
except Exception:  # pragma: no cover
    rarfile = None  # type: ignore[assignment]

ZIP_SUFFIXES = (".zip",)
RAR_SUFFIXES = (".rar",)

#: Callback invoked for folders that cannot be mapped to a car.
#: Written with Optional because this alias is evaluated at import time and the
#: project supports Python 3.9, where PEP 604 unions do not exist yet.
UnknownFolderHandler = Callable[[str], Optional[str]]


class ImportError_(Exception):
    """Raised when an archive or folder cannot be imported."""


def is_archive(path: str | os.PathLike[str]) -> bool:
    suffix = Path(path).suffix.lower()
    return suffix in ZIP_SUFFIXES + RAR_SUFFIXES


def _is_within(root: Path, target: Path) -> bool:
    try:
        target.relative_to(root)
    except ValueError:
        return False
    return True


def safe_extract_zip(archive: Path, destination: Path) -> None:
    """Extract a ZIP file, refusing absolute paths, ``..`` and symlinks."""
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            name = member.filename
            if not name or name.endswith("/"):
                continue
            target = (root / name).resolve()
            if not _is_within(root, target):
                raise ImportError_(f"Archive entry '{name}' would be written outside the extraction folder")
            # Reject symlinks (high bits of external_attr carry the unix mode).
            mode = member.external_attr >> 16
            if mode and (mode & 0o170000) == 0o120000:
                raise ImportError_(f"Archive entry '{name}' is a symlink, which is not supported")
        bundle.extractall(root)


def safe_extract_rar(archive: Path, destination: Path) -> None:
    if rarfile is None:
        raise ImportError_(
            "RAR archives need the 'rarfile' package (pip install rarfile) and an unrar binary"
        )
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with rarfile.RarFile(str(archive)) as bundle:
        for name in bundle.namelist():
            target = (root / name).resolve()
            if not _is_within(root, target):
                raise ImportError_(f"Archive entry '{name}' would be written outside the extraction folder")
        bundle.extractall(str(root))


def extract_archive(archive: Path, destination: Path) -> Path:
    """Extract ``archive`` into ``destination`` and return it."""
    suffix = archive.suffix.lower()
    if suffix in RAR_SUFFIXES:
        safe_extract_rar(archive, destination)
    elif suffix in ZIP_SUFFIXES:
        safe_extract_zip(archive, destination)
    else:
        raise ImportError_(f"Unsupported archive type '{archive.suffix}'")
    return destination


def locate_setup_root(source: str, custom_map: dict[str, str] | None = None, depth: int = 3) -> str:
    """Descend through wrapper folders until car folders are visible.

    Suppliers often ship ``Pack 2025S2/<car folders>``; without this the import
    silently did nothing.
    """
    current = source
    for _ in range(depth):
        subdirs = list(iter_dirs(current))
        if not subdirs:
            return current
        if any(identify_setup(name, custom_map) for name in subdirs):
            return current
        if len(subdirs) == 1:
            child = os.path.join(current, subdirs[0])
            # A single sub-folder with nothing but files inside *is* the car
            # folder; descending past it would hide it from the import.
            if not list(iter_dirs(child)):
                return current
            current = child
            continue
        return current
    return current


def _import_into_car(
    ops: FileOps,
    source_path: str,
    car_root: str,
    bases: Sequence[str],
    *,
    supplier: str,
    season: str,
    drivers: Sequence[str] | None,
    label: str,
) -> None:
    """Copy one supplier folder into a single car's personal/team folders."""
    for base_name in bases:
        base = os.path.join(car_root, base_name)
        if drivers is None:
            target = os.path.join(base, supplier, season)
            ops.logger.info("%sImporting '%s' -> '%s'", ops.prefix, label, target)
            sync_folders(ops, source_path, target, delete_extras=False)
            continue
        common = os.path.join(base, COMMON_FOLDER, supplier, season)
        ops.logger.info("%sImporting '%s' -> '%s'", ops.prefix, label, common)
        sync_folders(ops, source_path, common, delete_extras=False)
        for driver in drivers:
            copy_missing_files(ops, source_path, os.path.join(base, DRIVERS_ROOT, driver, supplier, season))


def import_from_folder(
    ops: FileOps,
    source: str,
    iracing_folder: str,
    cfg: dict,
    *,
    on_unknown_folder: UnknownFolderHandler | None = None,
    mapping_path: str | os.PathLike[str] | None = None,
) -> list[str]:
    """Copy each recognised car folder of ``source`` into the iRacing tree.

    Returns the list of folders that could not be identified.
    """
    custom_map = load_custom_mapping(mapping_path, logger=ops.logger)
    root = locate_setup_root(source, custom_map)
    if root != source:
        ops.logger.info("Using '%s' as the setup root", root)

    supplier = clean_name(cfg.get("supplier_folder") or cfg.get("driver_folder") or "")
    season = clean_name(cfg.get("season_folder"))
    team_folder = clean_name(cfg.get("team_folder"))
    personal_folder = clean_name(cfg.get("personal_folder"))
    if not supplier or not season:
        raise ImportError_("Supplier and season folder names must be configured before importing")
    if not team_folder and not personal_folder:
        raise ImportError_("At least one of the team or personal folder names must be configured")

    drivers: Sequence[str] | None = None
    if cfg.get("use_driver_folders"):
        drivers = [name for name in (clean_name(n) for n in cfg.get("drivers", [])) if name]

    unresolved: list[str] = []
    mapping_changed = False

    for folder in iter_dirs(root):
        targets = identify_setup(folder, custom_map)
        if not targets and on_unknown_folder is not None:
            answer = on_unknown_folder(folder)
            if answer:
                custom_map[normalise(folder)] = answer.strip()
                mapping_changed = True
                targets = identify_setup(folder, custom_map)
        if not targets:
            ops.logger.warning("Folder '%s' could not be matched to a car; skipping", folder)
            unresolved.append(folder)
            continue

        # A group folder (NASCAR, Super Formula) resolves to several cars. Only
        # import into the ones you actually own, so the tool does not litter the
        # setups folder with cars that are not in your account. If none of them
        # exist yet, fall back to all of them rather than importing nothing.
        if len(targets) > 1:
            owned = [car for car in targets if os.path.isdir(os.path.join(iracing_folder, car))]
            targets = owned or targets

        source_path = os.path.join(root, folder)
        bases = [base for base in (personal_folder, team_folder) if base]
        for car in targets:
            _import_into_car(
                ops,
                source_path,
                os.path.join(iracing_folder, car),
                bases,
                supplier=supplier,
                season=season,
                drivers=drivers,
                label=folder,
            )

    if mapping_changed and not ops.dry_run:
        try:
            save_custom_mapping(custom_map, mapping_path, logger=ops.logger)
        except OSError:
            pass
    return unresolved


def import_archive(
    ops: FileOps,
    archive: str,
    iracing_folder: str,
    cfg: dict,
    *,
    on_unknown_folder: UnknownFolderHandler | None = None,
    mapping_path: str | os.PathLike[str] | None = None,
) -> list[str]:
    """Extract ``archive`` to a temporary folder and import its contents."""
    archive_path = Path(archive)
    if not archive_path.is_file():
        raise ImportError_(f"Archive '{archive}' does not exist")

    if ops.dry_run:
        ops.logger.info("[DRY-RUN] Would extract '%s' and import its contents", archive_path)

    workdir = Path(tempfile.mkdtemp(prefix="nishizumi-import-"))
    try:
        ops.logger.info("Extracting '%s'", archive_path.name)
        extract_archive(archive_path, workdir)
        return import_from_folder(
            ops,
            str(workdir),
            iracing_folder,
            cfg,
            on_unknown_folder=on_unknown_folder,
            mapping_path=mapping_path,
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def import_path(
    ops: FileOps,
    path: str,
    iracing_folder: str,
    cfg: dict,
    *,
    on_unknown_folder: UnknownFolderHandler | None = None,
    mapping_path: str | os.PathLike[str] | None = None,
) -> list[str]:
    """Import an archive or a folder, whichever ``path`` points at."""
    if os.path.isdir(path):
        return import_from_folder(
            ops, path, iracing_folder, cfg,
            on_unknown_folder=on_unknown_folder, mapping_path=mapping_path,
        )
    if os.path.isfile(path):
        return import_archive(
            ops, path, iracing_folder, cfg,
            on_unknown_folder=on_unknown_folder, mapping_path=mapping_path,
        )
    raise ImportError_(f"'{path}' does not exist")


def preview_archive(archive: str) -> list[str]:
    """Top level folder names inside an archive, for the GUI."""
    path = Path(archive)
    names: set[str] = set()
    if path.suffix.lower() in ZIP_SUFFIXES:
        with zipfile.ZipFile(path) as bundle:
            entries = bundle.namelist()
    elif path.suffix.lower() in RAR_SUFFIXES and rarfile is not None:
        with rarfile.RarFile(str(path)) as bundle:
            entries = bundle.namelist()
    else:
        return []
    for entry in entries:
        head = entry.replace("\\", "/").split("/", 1)[0]
        if head:
            names.add(head)
    return sorted(names)


def _archive_entries(archive: Path) -> list[str]:
    suffix = archive.suffix.lower()
    if suffix in ZIP_SUFFIXES:
        with zipfile.ZipFile(archive) as bundle:
            return bundle.namelist()
    if suffix in RAR_SUFFIXES:
        if rarfile is None:
            raise ImportError_("RAR archives need the 'rarfile' package")
        with rarfile.RarFile(str(archive)) as bundle:
            return bundle.namelist()
    raise ImportError_(f"Unsupported archive type '{archive.suffix}'")


def archive_setup_folders(archive: str | os.PathLike[str], custom_map: dict[str, str] | None = None) -> list[str]:
    """Names of the car folders inside an archive, descending wrappers.

    Lets the GUI ask about unrecognised folders before starting a long import.
    """
    path = Path(archive)
    entries = [entry.replace("\\", "/") for entry in _archive_entries(path)]

    def names_at(prefix: str) -> list[str]:
        depth = prefix.count("/") if prefix else 0
        found: set[str] = set()
        for entry in entries:
            if prefix and not entry.startswith(prefix):
                continue
            parts = [part for part in entry.split("/") if part]
            if len(parts) <= depth:
                continue
            # Only directories: an entry deeper than this level, or a trailing /
            if len(parts) == depth + 1 and not entry.endswith("/"):
                continue
            found.add(parts[depth])
        return sorted(found)

    prefix = ""
    for _ in range(3):
        names = names_at(prefix)
        if not names:
            return []
        if any(identify_setup(name, custom_map) for name in names):
            return names
        if len(names) == 1:
            deeper = names_at(f"{prefix}{names[0]}/")
            if deeper:
                prefix = f"{prefix}{names[0]}/"
                continue
        return names
    return names_at(prefix)


def configured_setup_folders(cfg: dict, custom_map: dict[str, str] | None = None) -> list[str]:
    """Car folder names offered by the import source configured in ``cfg``."""
    source_type = cfg.get("source_type", "none")
    if source_type == "zip":
        archive = str(cfg.get("zip_file") or "").strip()
        if archive and os.path.isfile(archive):
            return archive_setup_folders(archive, custom_map)
        return []
    if source_type == "folder":
        folder = str(cfg.get("source_folder") or "").strip()
        if folder and os.path.isdir(folder):
            return list(iter_dirs(locate_setup_root(folder, custom_map)))
    return []
