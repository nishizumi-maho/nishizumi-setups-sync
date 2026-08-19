"""The synchronisation engine.

Layout handled by this module, for every car folder inside the iRacing setups
directory::

    <car>/<sync_source>/...              setups you edit
    <car>/<sync_destination>/...         setups shared with the team
    <car>/<sync_source>/Common Setups/   shared baseline when driver mode is on
    <car>/<sync_source>/Drivers/<name>/  per-driver folders

The public entry point is :func:`perform_sync`; :func:`run_sync` wraps it with
backups, importing and plugin hooks.
"""

from __future__ import annotations

import logging
import os
from typing import Iterable, Sequence

from .cars import CAR_GROUPS, clean_name
from .fileops import FileOps, SyncStats, iter_dirs, newest, safe_listdir

COMMON_FOLDER = "Common Setups"
DRIVERS_ROOT = "Drivers"
DATA_PACKS = "Data packs"
GARAGE61_FOLDER = "Garage 61"


class SyncError(Exception):
    """Raised when a sync cannot start (bad configuration, missing folders)."""


# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------

def copy_missing_files(ops: FileOps, src: str, dst: str) -> None:
    """Copy files from ``src`` that do not exist in ``dst``; never overwrite."""
    if not os.path.isdir(src):
        return
    for name in safe_listdir(src):
        source = os.path.join(src, name)
        target = os.path.join(dst, name)
        if os.path.islink(source):
            continue
        if os.path.isdir(source):
            copy_missing_files(ops, source, target)
        elif ops.wants_file(name) and not os.path.exists(target):
            ops.copy_file(source, target)


def sync_folders(
    ops: FileOps,
    src: str,
    dst: str,
    *,
    delete_extras: bool = True,
    prune_foreign: bool = False,
    ignore_dirs: Iterable[str] | None = None,
) -> None:
    """Mirror ``src`` onto ``dst``.

    ``delete_extras`` removes destination entries that no longer exist in the
    source.  Files the sync does not manage (non-``.sto`` files while
    ``copy_all`` is off) are left alone unless ``prune_foreign`` is set — the
    old implementation deleted them unconditionally, which threw away notes,
    telemetry and data packs that lived next to the setups.
    """
    if not os.path.isdir(src):
        ops.logger.debug("Source folder '%s' does not exist, nothing to sync", src)
        return

    ignored = {name.lower() for name in (ignore_dirs or ())}
    ops.ensure_dir(dst)

    for name in safe_listdir(src):
        source = os.path.join(src, name)
        target = os.path.join(dst, name)
        if os.path.islink(source):
            ops.logger.debug("Skipping symlink '%s'", source)
            continue
        if os.path.isdir(source):
            if name.lower() in ignored:
                ops.logger.debug("Ignoring folder '%s'", source)
                continue
            sync_folders(
                ops,
                source,
                target,
                delete_extras=delete_extras,
                prune_foreign=prune_foreign,
                ignore_dirs=ignore_dirs,
            )
            continue
        if not ops.wants_file(name):
            continue
        ops.copy_if_different(source, target)

    if not delete_extras or not os.path.isdir(dst):
        return

    for name in safe_listdir(dst):
        if name.lower() in ignored:
            continue
        target = os.path.join(dst, name)
        source = os.path.join(src, name)
        if os.path.exists(source):
            continue
        if os.path.isdir(target):
            ops.remove_tree(target)
        elif ops.wants_file(name) or prune_foreign:
            ops.remove_file(target)
        else:
            ops.logger.debug("Leaving unmanaged file '%s'", target)


def sync_bidirectional(ops: FileOps, dir_a: str, dir_b: str) -> None:
    """Merge two folders in both directions, newest file wins on conflicts."""
    if not os.path.isdir(dir_a) and not os.path.isdir(dir_b):
        return
    ops.ensure_dir(dir_a)
    ops.ensure_dir(dir_b)

    items_a = set(safe_listdir(dir_a))
    items_b = set(safe_listdir(dir_b))

    for name in sorted(items_a - items_b):
        _copy_either(ops, os.path.join(dir_a, name), os.path.join(dir_b, name))
    for name in sorted(items_b - items_a):
        _copy_either(ops, os.path.join(dir_b, name), os.path.join(dir_a, name))

    for name in sorted(items_a & items_b):
        path_a = os.path.join(dir_a, name)
        path_b = os.path.join(dir_b, name)
        if os.path.islink(path_a) or os.path.islink(path_b):
            continue
        if os.path.isdir(path_a) and os.path.isdir(path_b):
            sync_bidirectional(ops, path_a, path_b)
        elif os.path.isfile(path_a) and os.path.isfile(path_b):
            if not ops.wants_file(name) or not ops.files_differ(path_a, path_b):
                continue
            if newest(path_a, path_b) == path_a:
                ops.copy_file(path_a, path_b)
            else:
                ops.copy_file(path_b, path_a)


def _copy_either(ops: FileOps, source: str, target: str) -> None:
    if os.path.islink(source):
        return
    if os.path.isdir(source):
        ops.copy_tree(source, target)
    elif os.path.isfile(source) and ops.wants_file(os.path.basename(source)):
        ops.copy_file(source, target)


def backup_folder(ops: FileOps, source: str, destination: str) -> None:
    """Copy anything missing from ``source`` into ``destination``."""
    if not source or not os.path.isdir(source):
        ops.logger.debug("Nothing to back up from '%s'", source)
        return
    if not destination:
        ops.logger.debug("No backup folder configured")
        return
    ops.logger.info("%sBacking up '%s' -> '%s'", ops.prefix, source, destination)
    copy_missing_files(ops, source, destination)


# ---------------------------------------------------------------------------
# Team / driver structure
# ---------------------------------------------------------------------------

def car_folders(iracing_folder: str) -> list[str]:
    """Every car directory inside the iRacing setups folder."""
    return list(iter_dirs(iracing_folder))


def remove_unknown_driver_folders(
    ops: FileOps, iracing_folder: str, dest_name: str, drivers: Sequence[str] | None
) -> None:
    """Delete driver folders that are no longer in the configured driver list."""
    if drivers is None:
        return
    known = {clean_name(name).lower() for name in drivers}
    for car in car_folders(iracing_folder):
        root = os.path.join(iracing_folder, car, dest_name, DRIVERS_ROOT)
        if not os.path.isdir(root):
            continue
        for folder in safe_listdir(root):
            if folder.lower() in known:
                continue
            ops.remove_tree(os.path.join(root, folder))


def merge_external_into_source(
    ops: FileOps,
    iracing_folder: str,
    extra_folders: Sequence[dict],
    src_name: str,
    dest_name: str,
    drivers: Sequence[str] | None,
) -> None:
    """Fold folders produced by third-party tools into the sync source."""
    for car in car_folders(iracing_folder):
        car_dir = os.path.join(iracing_folder, car)
        for definition in extra_folders:
            folder_name = str(definition.get("name") or "").strip()
            if not folder_name:
                continue
            location = definition.get("location", "car")
            if location == "dest":
                external = os.path.join(car_dir, dest_name, folder_name)
                remove_after = True
            else:
                external = os.path.join(car_dir, folder_name)
                remove_after = False
            if not os.path.isdir(external):
                continue

            if drivers is not None:
                common = os.path.join(car_dir, src_name, COMMON_FOLDER, folder_name)
                sync_folders(ops, external, common, delete_extras=False)
                for driver in drivers:
                    target = os.path.join(car_dir, src_name, DRIVERS_ROOT, driver, folder_name)
                    copy_missing_files(ops, external, target)
            else:
                target = os.path.join(car_dir, src_name, folder_name)
                sync_folders(ops, external, target, delete_extras=False)

            if remove_after:
                ops.remove_tree(external)


def sync_team_folders(
    ops: FileOps,
    iracing_folder: str,
    src_name: str,
    dest_name: str,
    drivers: Sequence[str] | None,
    *,
    delete_extras: bool = True,
) -> None:
    """Publish each car's source folder into the destination folder."""
    driver_mode = drivers is not None
    for car in car_folders(iracing_folder):
        car_dir = os.path.join(iracing_folder, car)
        src = os.path.join(car_dir, src_name)
        dest_root = os.path.join(car_dir, dest_name)
        if not os.path.isdir(src) or not safe_listdir(src):
            continue

        if not driver_mode:
            sync_folders(ops, src, dest_root, delete_extras=delete_extras)
            continue

        src_common = os.path.join(src, COMMON_FOLDER)
        src_packs = os.path.join(src, DATA_PACKS)

        if os.path.isdir(src_common):
            sync_folders(ops, src_common, os.path.join(dest_root, COMMON_FOLDER), delete_extras=delete_extras)

        for driver in drivers:
            source_driver = os.path.join(src, DRIVERS_ROOT, driver)
            target = os.path.join(dest_root, DRIVERS_ROOT, driver)
            if os.path.isdir(source_driver):
                copy_missing_files(ops, source_driver, target)
            elif os.path.isdir(src_common):
                copy_missing_files(ops, src_common, target)

        if os.path.isdir(src_packs):
            sync_folders(
                ops,
                src_packs,
                os.path.join(dest_root, COMMON_FOLDER, DATA_PACKS),
                delete_extras=False,
            )
            for driver in drivers:
                copy_missing_files(ops, src_packs, os.path.join(dest_root, DRIVERS_ROOT, driver, DATA_PACKS))

        # Everything that is not part of the driver structure is mirrored as-is.
        sync_folders(
            ops,
            src,
            dest_root,
            delete_extras=delete_extras,
            ignore_dirs={DATA_PACKS, COMMON_FOLDER, DRIVERS_ROOT},
        )
        # Data packs belong inside Common Setups / each driver, not at the root.
        ops.remove_tree(os.path.join(dest_root, DATA_PACKS))


# ---------------------------------------------------------------------------
# Car groups (NASCAR, Super Formula)
# ---------------------------------------------------------------------------

def sync_group_folders(ops: FileOps, iracing_folder: str, src_rel: str, dest_rel: str) -> None:
    """Copy one group member's source folder onto every sibling's destination."""
    for cars in CAR_GROUPS.values():
        source_path = None
        for car in cars:
            candidate = os.path.join(iracing_folder, car, src_rel)
            if os.path.isdir(candidate) and safe_listdir(candidate):
                source_path = candidate
                break
        if not source_path:
            continue
        for car in cars:
            destination = os.path.join(iracing_folder, car, dest_rel)
            if os.path.abspath(destination) == os.path.abspath(source_path):
                continue
            sync_folders(ops, source_path, destination, delete_extras=False)


def sync_group_sources(
    ops: FileOps, iracing_folder: str, src_name: str, drivers: Sequence[str] | None
) -> None:
    """Keep the *source* folders of cars that share setups identical.

    Every car of the group that exists in the setups folder takes part, even if
    it has no source folder yet — otherwise a NASCAR variant you have never
    opened never receives the setups, which is what 1.x did.
    """
    for cars in CAR_GROUPS.values():
        paths: list[str] = []
        for car in cars:
            if not os.path.isdir(os.path.join(iracing_folder, car)):
                continue
            base = os.path.join(iracing_folder, car, src_name)
            paths.append(os.path.join(base, COMMON_FOLDER) if drivers is not None else base)
        if len(paths) < 2:
            continue
        # Nothing to share yet: do not create empty folders for every variant.
        if not any(os.path.isdir(path) and safe_listdir(path) for path in paths):
            continue
        for index, first in enumerate(paths):
            for second in paths[index + 1:]:
                sync_bidirectional(ops, first, second)


def sync_group_data_packs(ops: FileOps, iracing_folder: str, dest_name: str) -> None:
    """Share Garage 61 data packs between cars of the same group."""
    for cars in CAR_GROUPS.values():
        paths: list[str] = []
        for car in cars:
            for candidate in (
                os.path.join(iracing_folder, car, GARAGE61_FOLDER, DATA_PACKS),
                os.path.join(iracing_folder, car, dest_name, DATA_PACKS),
            ):
                if os.path.isdir(candidate):
                    paths.append(candidate)
        for index, first in enumerate(paths):
            for second in paths[index + 1:]:
                sync_bidirectional(ops, first, second)


def sync_data_packs(ops: FileOps, iracing_folder: str, src_name: str, dest_name: str) -> None:
    """Publish data packs from source to destination (non driver mode)."""
    src_rel = os.path.join(src_name, DATA_PACKS)
    dest_rel = os.path.join(dest_name, DATA_PACKS)
    sync_group_folders(ops, iracing_folder, src_rel, dest_rel)
    for car in car_folders(iracing_folder):
        source = os.path.join(iracing_folder, car, src_rel)
        if not os.path.isdir(source) or not safe_listdir(source):
            continue
        sync_folders(ops, source, os.path.join(iracing_folder, car, dest_rel), delete_extras=False)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def configured_drivers(cfg: dict) -> list[str] | None:
    """The cleaned driver list, or ``None`` when driver folders are disabled."""
    if not cfg.get("use_driver_folders"):
        return None
    return [name for name in (clean_name(n) for n in cfg.get("drivers", [])) if name]


def refresh_drivers_from_garage61(
    cfg: dict, logger: logging.Logger, *, config_path=None, dry_run: bool = False
) -> bool:
    """Update ``cfg["drivers"]`` from Garage61 and persist the result.

    A failed lookup leaves the configured drivers untouched instead of wiping
    them, and the configuration is only rewritten when the list really changed.
    """
    if not cfg.get("use_garage61") or not cfg.get("garage61_team_id"):
        return False

    from .config import ConfigError, save_config
    from .garage61 import fetch_drivers

    names = fetch_drivers(cfg.get("garage61_team_id"), cfg.get("garage61_api_key"), logger)
    if names is None:
        return False
    current = [clean_name(n) for n in cfg.get("drivers", [])]
    if names == current:
        logger.debug("Garage61 driver list unchanged")
        return False
    cfg["drivers"] = names
    logger.info("Driver list updated from Garage61: %s", ", ".join(names))
    if dry_run:
        return True
    try:
        save_config(cfg, config_path, logger=logger)
    except ConfigError as exc:
        logger.error("Could not save the refreshed driver list: %s", exc)
    return True


def perform_sync(ops: FileOps, iracing_folder: str, cfg: dict) -> SyncStats:
    """Run the full synchronisation pipeline for one iRacing setups folder."""
    src_name = clean_name(cfg.get("sync_source"))
    dest_name = clean_name(cfg.get("sync_destination"))
    if not src_name or not dest_name:
        ops.logger.warning("Sync source or destination is not configured; skipping sync")
        return ops.stats
    if src_name.lower() == dest_name.lower():
        raise SyncError("Sync source and destination must be different folders")
    if not os.path.isdir(iracing_folder):
        raise SyncError(f"iRacing setups folder '{iracing_folder}' does not exist")

    drivers = configured_drivers(cfg)
    delete_extras = bool(cfg.get("delete_extras", True))

    if cfg.get("use_external") and cfg.get("extra_folders"):
        ops.logger.info("Merging extra folders into '%s'", src_name)
        merge_external_into_source(ops, iracing_folder, cfg["extra_folders"], src_name, dest_name, drivers)

    if drivers is not None:
        remove_unknown_driver_folders(ops, iracing_folder, dest_name, drivers)

    ops.logger.info("Synchronising shared cars")
    sync_group_sources(ops, iracing_folder, src_name, drivers)

    ops.logger.info("Publishing '%s' -> '%s'", src_name, dest_name)
    sync_team_folders(ops, iracing_folder, src_name, dest_name, drivers, delete_extras=delete_extras)

    if drivers is None:
        sync_data_packs(ops, iracing_folder, src_name, dest_name)
    sync_group_data_packs(ops, iracing_folder, dest_name)
    return ops.stats


def run_sync(
    cfg: dict,
    logger: logging.Logger,
    *,
    dry_run: bool = False,
    on_unknown_folder=None,
    stats: SyncStats | None = None,
    config_path=None,
) -> SyncStats:
    """Backup → import → sync → backup, driven entirely by ``cfg``."""
    from .importer import import_path
    from .plugins import run_hook

    iracing_folder = str(cfg.get("iracing_folder") or "").strip()
    if not iracing_folder:
        raise SyncError("No iRacing setups folder configured")
    if not os.path.isdir(iracing_folder):
        raise SyncError(f"iRacing setups folder '{iracing_folder}' does not exist")

    ops = FileOps(
        logger,
        dry_run=dry_run,
        algorithm=cfg.get("hash_algorithm", "md5"),
        copy_all=bool(cfg.get("copy_all")),
        stats=stats,
    )
    logger.info("Starting sync%s", " (dry run)" if dry_run else "")
    run_hook("before_sync", cfg, logger, enabled=bool(cfg.get("enable_plugins")))
    refresh_drivers_from_garage61(cfg, logger, config_path=config_path, dry_run=dry_run)

    if cfg.get("backup_enabled") and cfg.get("backup_before_folder"):
        backup_folder(ops, iracing_folder, cfg["backup_before_folder"])

    source_type = cfg.get("source_type", "none")
    if source_type == "zip":
        archive = str(cfg.get("zip_file") or "").strip()
        if archive and os.path.isfile(archive):
            import_path(ops, archive, iracing_folder, cfg, on_unknown_folder=on_unknown_folder)
        elif archive:
            logger.warning("Archive '%s' not found; skipping import", archive)
        else:
            logger.info("No archive configured; skipping import")
    elif source_type == "folder":
        folder = str(cfg.get("source_folder") or "").strip()
        if folder and os.path.isdir(folder):
            import_path(ops, folder, iracing_folder, cfg, on_unknown_folder=on_unknown_folder)
        elif folder:
            logger.warning("Import folder '%s' not found; skipping import", folder)
        else:
            logger.info("No import folder configured; skipping import")

    perform_sync(ops, iracing_folder, cfg)

    if cfg.get("backup_enabled") and cfg.get("backup_after_folder"):
        backup_folder(ops, iracing_folder, cfg["backup_after_folder"])

    run_hook("after_sync", cfg, logger, enabled=bool(cfg.get("enable_plugins")))
    logger.info("Sync finished: %s", ops.stats.summary())
    return ops.stats
