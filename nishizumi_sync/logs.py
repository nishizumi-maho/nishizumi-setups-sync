"""Logging helpers built on top of the standard library ``logging`` module.

The previous implementation printed to stdout and appended to a file by hand,
re-opening the file for every single line and silently discarding write errors.
Using ``logging`` gives us levels, rotation and — importantly for the GUI — the
ability to attach extra sinks without touching the call sites.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
from pathlib import Path
from typing import Callable, Iterable

LOGGER_NAME = "nishizumi_sync"

LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARN": logging.WARNING,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}

_FORMAT = "[%(asctime)s] [%(levelname)s] %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

#: Marker prefix used for messages emitted while running in dry-run mode.
DRY_RUN_PREFIX = "[DRY-RUN]"


def parse_level(level: str | int | None, default: int = logging.INFO) -> int:
    """Translate a level name (``"info"``, ``"WARN"``, …) into a numeric level."""
    if level is None:
        return default
    if isinstance(level, int):
        return level
    return LEVELS.get(str(level).strip().upper(), default)


class CallbackHandler(logging.Handler):
    """Forward formatted records to an arbitrary callable (used by the GUI)."""

    def __init__(self, callback: Callable[[str, int], None], level: int = logging.NOTSET):
        super().__init__(level)
        self._callback = callback

    def emit(self, record: logging.LogRecord) -> None:  # pragma: no cover - GUI glue
        try:
            self._callback(self.format(record), record.levelno)
        except Exception:
            self.handleError(record)


def get_logger(name: str = LOGGER_NAME) -> logging.Logger:
    """Return the application logger, creating a console handler on first use."""
    logger = logging.getLogger(name)
    if not getattr(logger, "_nishizumi_configured", False):
        logger.setLevel(logging.INFO)
        logger.propagate = False
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(_FORMAT, _DATE_FORMAT))
        handler.set_name("console")
        logger.addHandler(handler)
        logger._nishizumi_configured = True  # type: ignore[attr-defined]
    return logger


def _remove_handlers(logger: logging.Logger, names: Iterable[str]) -> None:
    wanted = set(names)
    for handler in list(logger.handlers):
        if handler.get_name() in wanted:
            logger.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass


def configure(
    level: str | int | None = None,
    *,
    log_file: str | os.PathLike[str] | None = None,
    enable_file: bool = False,
    max_bytes: int = 2 * 1024 * 1024,
    backup_count: int = 3,
    logger: logging.Logger | None = None,
) -> logging.Logger:
    """Configure the application logger.

    Returns the logger so callers can keep the usual ``logger.info(...)`` style.
    File logging failures are reported once instead of being swallowed, which is
    how the old code hid misconfigured log paths.
    """
    log = logger or get_logger()
    log.setLevel(parse_level(level, log.level or logging.INFO))
    _remove_handlers(log, {"file"})

    if enable_file and log_file:
        path = Path(log_file).expanduser()
        try:
            if path.parent and not path.parent.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
            handler = logging.handlers.RotatingFileHandler(
                path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
            )
            handler.setFormatter(logging.Formatter(_FORMAT, _DATE_FORMAT))
            handler.set_name("file")
            log.addHandler(handler)
        except OSError as exc:
            log.error("Could not open log file '%s': %s", path, exc)
    return log


def configure_from_config(cfg: dict, logger: logging.Logger | None = None) -> logging.Logger:
    """Configure logging from a configuration mapping."""
    from .paths import default_log_file

    return configure(
        cfg.get("log_level", "INFO"),
        log_file=cfg.get("log_file") or default_log_file(),
        enable_file=bool(cfg.get("enable_logging")),
        logger=logger,
    )


def add_callback_handler(
    callback: Callable[[str, int], None],
    *,
    level: int = logging.DEBUG,
    logger: logging.Logger | None = None,
) -> CallbackHandler:
    """Attach a :class:`CallbackHandler` and return it so it can be removed."""
    log = logger or get_logger()
    handler = CallbackHandler(callback, level)
    handler.setFormatter(logging.Formatter(_FORMAT, _DATE_FORMAT))
    handler.set_name("callback")
    log.addHandler(handler)
    return handler


def remove_handler(handler: logging.Handler, logger: logging.Logger | None = None) -> None:
    log = logger or get_logger()
    log.removeHandler(handler)
    try:
        handler.close()
    except Exception:
        pass
