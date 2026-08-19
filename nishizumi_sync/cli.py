"""Command line interface."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from . import APP_NAME, PROJECT_URL, __version__
from .config import (
    ConfigError,
    default_config,
    export_config,
    load_config,
    save_config,
)
from .paths import config_file, data_dir

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2

#: Flags accepted by older releases, translated to the current syntax.
_LEGACY_FLAGS = {"--silent": "run", "--gui": "gui", "--tray": "tray"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nishizumi-sync",
        description=f"{APP_NAME} — synchronise iRacing setups between personal, team and supplier folders.",
        epilog=f"Documentation: {PROJECT_URL}",
    )
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    parser.add_argument("--config", metavar="PATH", help="use an alternative configuration file")
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARN", "ERROR"],
        help="override the configured log level",
    )
    parser.add_argument(
        "--no-update-check",
        action="store_true",
        help="never contact GitHub for updates during this run",
    )

    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="import and synchronise using the saved configuration")
    run.add_argument("--dry-run", action="store_true", help="report what would happen, change nothing")
    run.add_argument(
        "--ask",
        action="store_true",
        help="prompt for unknown car folders instead of skipping them",
    )

    sub.add_parser("dry-run", help="alias for 'run --dry-run'")

    imp = sub.add_parser("import", help="import a ZIP/RAR archive or a folder")
    imp.add_argument("path", help="archive or folder to import")
    imp.add_argument("--dry-run", action="store_true", help="report what would happen, change nothing")
    imp.add_argument("--ask", action="store_true", help="prompt for unknown car folders")

    sub.add_parser("gui", help="open the graphical interface (default)")
    sub.add_parser("tray", help="run in the system tray and sync periodically")

    check = sub.add_parser("check-update", help="check whether a newer release exists")
    check.add_argument("--pre", action="store_true", help="include pre-releases")

    update = sub.add_parser("update", help="download and install the latest release")
    update.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")
    update.add_argument("--pre", action="store_true", help="include pre-releases")

    config_cmd = sub.add_parser("config", help="inspect or manage the configuration file")
    config_sub = config_cmd.add_subparsers(dest="config_command")
    config_sub.add_parser("show", help="print the active configuration")
    config_sub.add_parser("path", help="print the configuration file location")
    exp = config_sub.add_parser("export", help="write the configuration to another file")
    exp.add_argument("path")
    config_sub.add_parser("reset", help="restore the built-in defaults")

    mapping = sub.add_parser("mapping", help="manage custom car folder mappings")
    mapping_sub = mapping.add_subparsers(dest="mapping_command")
    mapping_sub.add_parser("list", help="show the custom mappings")
    set_map = mapping_sub.add_parser("set", help="map a supplier folder to a car")
    set_map.add_argument("folder")
    set_map.add_argument("car")
    rm_map = mapping_sub.add_parser("remove", help="delete a custom mapping")
    rm_map.add_argument("folder")

    return parser


def _translate_legacy(argv: list[str]) -> list[str]:
    """Accept the flags used by the 1.x releases."""
    translated: list[str] = []
    for arg in argv:
        if arg in _LEGACY_FLAGS:
            translated.append(_LEGACY_FLAGS[arg])
        else:
            translated.append(arg)
    return translated


def _should_run_silently(cfg: dict) -> bool:
    """Reproduce the historic 'launched by the startup shortcut' behaviour."""
    if not cfg.get("run_on_startup"):
        return False
    stdin = getattr(sys, "stdin", None)
    try:
        return not (stdin is not None and stdin.isatty())
    except (ValueError, AttributeError):
        return True


def _prompt_unknown_folder(folder: str) -> str | None:
    try:
        answer = input(
            f"Folder '{folder}' was not recognised. "
            "Enter the iRacing car folder to use (blank to skip): "
        ).strip()
    except (EOFError, KeyboardInterrupt):
        return None
    return answer or None


def _confirm(question: str, *, assume_yes: bool = False) -> bool:
    if assume_yes:
        return True
    try:
        return input(f"{question} [y/N] ").strip().lower() in {"y", "yes"}
    except (EOFError, KeyboardInterrupt):
        return False


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def _cmd_run(args, cfg, logger, config_path) -> int:
    from .sync import SyncError, run_sync

    dry_run = bool(getattr(args, "dry_run", False)) or args.command == "dry-run"
    handler = _prompt_unknown_folder if getattr(args, "ask", False) else None
    try:
        stats = run_sync(
            cfg, logger, dry_run=dry_run, on_unknown_folder=handler, config_path=config_path
        )
    except SyncError as exc:
        logger.error("%s", exc)
        return EXIT_ERROR
    print(stats.summary())
    return EXIT_OK if stats.errors == 0 else EXIT_ERROR


def _cmd_import(args, cfg, logger, config_path) -> int:
    from .fileops import FileOps
    from .importer import ImportError_, import_path

    target = str(cfg.get("iracing_folder") or "").strip()
    if not target or not os.path.isdir(target):
        logger.error("Configure a valid iRacing setups folder before importing")
        return EXIT_ERROR

    ops = FileOps(
        logger,
        dry_run=bool(args.dry_run),
        algorithm=cfg.get("hash_algorithm", "md5"),
        copy_all=bool(cfg.get("copy_all")),
    )
    handler = _prompt_unknown_folder if args.ask else None
    try:
        unresolved = import_path(ops, args.path, target, cfg, on_unknown_folder=handler)
    except ImportError_ as exc:
        logger.error("%s", exc)
        return EXIT_ERROR
    if unresolved:
        logger.warning("Unrecognised folders: %s", ", ".join(unresolved))
    print(ops.stats.summary())
    return EXIT_OK if ops.stats.errors == 0 else EXIT_ERROR


def _cmd_check_update(args, cfg, logger) -> int:
    from .updater import Updater, detect_install_mode

    if getattr(args, "pre", False):
        cfg.setdefault("updates", {})["channel"] = "prerelease"
    updater = Updater(cfg, logger)
    info = updater.check(force=True)
    if info is None:
        print(f"{APP_NAME} {__version__} is up to date.")
        return EXIT_OK
    print(f"Update available: {info.version} (installed: {__version__})")
    if info.html_url:
        print(f"Release page: {info.html_url}")
    print(f"Install mode: {detect_install_mode()}")
    print("Run 'nishizumi-sync update' to install it.")
    return EXIT_OK


def _cmd_update(args, cfg, logger) -> int:
    from .updater import Updater, summarise_release

    if getattr(args, "pre", False):
        cfg.setdefault("updates", {})["channel"] = "prerelease"
    updater = Updater(cfg, logger)
    info = updater.check(force=True)
    if info is None:
        print(f"{APP_NAME} {__version__} is up to date.")
        return EXIT_OK

    print(f"Update available: {info.version} (installed: {__version__})")
    notes = summarise_release(info, max_lines=12)
    if notes:
        print(f"\n{notes}\n")
    if not _confirm(f"Install version {info.version}?", assume_yes=args.yes):
        print("Update cancelled.")
        return EXIT_OK

    def progress(done: int, total: int) -> None:
        if total:
            print(f"\rDownloading… {done * 100 // total}%", end="", flush=True)

    result = updater.install(info, progress=progress)
    print()
    print(result.message)
    return EXIT_OK if result.ok else EXIT_ERROR


def _cmd_config(args, cfg, logger, config_path) -> int:
    command = getattr(args, "config_command", None) or "show"
    if command == "path":
        print(config_path)
        print(f"data directory: {data_dir()}")
        return EXIT_OK
    if command == "show":
        printable = dict(cfg)
        if printable.get("garage61_api_key"):
            printable["garage61_api_key"] = "***"
        print(json.dumps(printable, indent=2, ensure_ascii=False))
        return EXIT_OK
    if command == "export":
        try:
            path = export_config(cfg, args.path)
        except ConfigError as exc:
            logger.error("%s", exc)
            return EXIT_ERROR
        print(f"Configuration exported to {path}")
        return EXIT_OK
    if command == "reset":
        if not _confirm(f"Reset {config_path} to the defaults?"):
            print("Cancelled.")
            return EXIT_OK
        try:
            save_config(default_config(), config_path, logger=logger)
        except ConfigError as exc:
            logger.error("%s", exc)
            return EXIT_ERROR
        print("Configuration reset.")
        return EXIT_OK
    logger.error("Unknown config command '%s'", command)
    return EXIT_USAGE


def _cmd_mapping(args, logger) -> int:
    from .cars import load_custom_mapping, normalise, save_custom_mapping

    command = getattr(args, "mapping_command", None) or "list"
    mapping = load_custom_mapping(logger=logger)
    if command == "list":
        if not mapping:
            print("No custom mappings defined.")
        for folder, car in sorted(mapping.items()):
            print(f"{folder} -> {car}")
        return EXIT_OK
    if command == "set":
        mapping[normalise(args.folder)] = args.car.strip()
        save_custom_mapping(mapping, logger=logger)
        print(f"Mapped '{args.folder}' to '{args.car}'")
        return EXIT_OK
    if command == "remove":
        if mapping.pop(normalise(args.folder), None) is None:
            print(f"No mapping for '{args.folder}'")
            return EXIT_OK
        save_custom_mapping(mapping, logger=logger)
        print(f"Removed mapping for '{args.folder}'")
        return EXIT_OK
    logger.error("Unknown mapping command '%s'", command)
    return EXIT_USAGE


def _cmd_gui(cfg, logger, config_path) -> int:
    try:
        from .gui import run_gui
    except ImportError as exc:
        logger.error("The graphical interface needs PySide6: %s", exc)
        logger.info("Install it with 'pip install PySide6', or use 'nishizumi-sync run'.")
        return EXIT_ERROR
    return run_gui(cfg, logger, config_path=config_path)


def _cmd_tray(cfg, logger, config_path) -> int:
    try:
        from .gui import run_tray
    except ImportError as exc:
        logger.error("Tray mode needs PySide6: %s", exc)
        return EXIT_ERROR
    return run_tray(cfg, logger, config_path=config_path)


def _startup_update_check(cfg: dict, logger: logging.Logger) -> None:
    """Best-effort background-ish check used by the non-GUI commands."""
    from .updater import Updater

    updater = Updater(cfg, logger)
    message = updater.note_startup()
    if message:
        logger.info("%s", message)
    if not updater.due_for_check():
        return
    info = updater.check()
    if info is None:
        return
    if updater.settings.get("auto_install"):
        logger.info("Installing update %s automatically", info.version)
        result = updater.install(info)
        logger.info("%s", result.message)
    else:
        logger.info(
            "Update %s is available — run 'nishizumi-sync update' to install it.", info.version
        )


def main(argv: list[str] | None = None) -> int:
    from .logs import configure_from_config, get_logger

    raw_args = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(_translate_legacy(raw_args))

    config_path = Path(args.config).expanduser() if args.config else config_file()
    logger = get_logger()
    cfg = load_config(config_path, logger=logger)
    if args.log_level:
        cfg["log_level"] = args.log_level
    configure_from_config(cfg, logger)

    command = args.command
    if command is None:
        if _should_run_silently(cfg):
            command = "tray" if cfg.get("tray_mode") else "run"
            args.dry_run = False
            args.ask = False
        else:
            command = "gui"
        args.command = command

    if command in {"run", "dry-run", "tray"} and not args.no_update_check:
        _startup_update_check(cfg, logger)

    try:
        if command in {"run", "dry-run"}:
            return _cmd_run(args, cfg, logger, config_path)
        if command == "import":
            return _cmd_import(args, cfg, logger, config_path)
        if command == "gui":
            return _cmd_gui(cfg, logger, config_path)
        if command == "tray":
            return _cmd_tray(cfg, logger, config_path)
        if command == "check-update":
            return _cmd_check_update(args, cfg, logger)
        if command == "update":
            return _cmd_update(args, cfg, logger)
        if command == "config":
            return _cmd_config(args, cfg, logger, config_path)
        if command == "mapping":
            return _cmd_mapping(args, logger)
    except KeyboardInterrupt:
        logger.warning("Interrupted")
        return EXIT_ERROR

    parser.print_help()
    return EXIT_USAGE
