"""Low level file operations shared by every sync routine.

Everything that touches the filesystem goes through :class:`FileOps` so that
dry-run mode is honoured in a single place.  The previous code passed
``dry_run`` down through a dozen functions and still created directories and
extracted archives while "simulating".
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

CHUNK_SIZE = 1024 * 1024
SETUP_SUFFIX = ".sto"


@dataclass
class SyncStats:
    """Counters describing what a run did (or would do)."""

    files_copied: int = 0
    files_removed: int = 0
    dirs_created: int = 0
    dirs_removed: int = 0
    errors: int = 0
    messages: list[str] = field(default_factory=list)

    def merge(self, other: "SyncStats") -> "SyncStats":
        self.files_copied += other.files_copied
        self.files_removed += other.files_removed
        self.dirs_created += other.dirs_created
        self.dirs_removed += other.dirs_removed
        self.errors += other.errors
        self.messages.extend(other.messages)
        return self

    @property
    def changed(self) -> int:
        return self.files_copied + self.files_removed + self.dirs_created + self.dirs_removed

    def summary(self) -> str:
        return (
            f"{self.files_copied} file(s) copied, {self.files_removed} removed, "
            f"{self.dirs_created} folder(s) created, {self.dirs_removed} removed, "
            f"{self.errors} error(s)"
        )


def file_digest(path: str | os.PathLike[str], algorithm: str = "md5") -> str | None:
    """Hash a file, returning ``None`` when it cannot be read."""
    try:
        digest = hashlib.new(algorithm if algorithm in hashlib.algorithms_available else "md5")
    except ValueError:
        digest = hashlib.md5()
    try:
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def is_setup_file(name: str) -> bool:
    return name.lower().endswith(SETUP_SUFFIX)


def safe_listdir(path: str | os.PathLike[str]) -> list[str]:
    """``os.listdir`` that returns an empty list instead of raising."""
    try:
        return sorted(os.listdir(path))
    except OSError:
        return []


def iter_dirs(path: str | os.PathLike[str]) -> Iterator[str]:
    """Yield the names of the sub-directories of ``path``."""
    try:
        with os.scandir(path) as entries:
            for entry in sorted(entries, key=lambda e: e.name):
                if entry.is_dir(follow_symlinks=False):
                    yield entry.name
    except OSError:
        return


class FileOps:
    """Dry-run aware file operations with logging and statistics."""

    def __init__(
        self,
        logger: logging.Logger,
        *,
        dry_run: bool = False,
        algorithm: str = "md5",
        copy_all: bool = False,
        stats: SyncStats | None = None,
    ) -> None:
        self.logger = logger
        self.dry_run = bool(dry_run)
        self.algorithm = algorithm if algorithm in {"md5", "sha256"} else "md5"
        self.copy_all = bool(copy_all)
        self.stats = stats if stats is not None else SyncStats()

    # -- helpers ---------------------------------------------------------
    @property
    def prefix(self) -> str:
        return "[DRY-RUN] Would " if self.dry_run else ""

    def wants_file(self, name: str) -> bool:
        """True when a file should take part in the sync."""
        return self.copy_all or is_setup_file(name)

    def _fail(self, message: str, exc: BaseException) -> None:
        self.stats.errors += 1
        self.logger.error("%s: %s", message, exc)

    # -- directories -----------------------------------------------------
    def ensure_dir(self, path: str | os.PathLike[str]) -> bool:
        """Create ``path`` (and parents).  Never writes in dry-run mode."""
        if os.path.isdir(path):
            return True
        if self.dry_run:
            self.logger.info("[DRY-RUN] Would create folder '%s'", path)
            self.stats.dirs_created += 1
            return True
        try:
            os.makedirs(path, exist_ok=True)
        except OSError as exc:
            self._fail(f"Could not create folder '{path}'", exc)
            return False
        self.stats.dirs_created += 1
        self.logger.debug("Created folder '%s'", path)
        return True

    def remove_tree(self, path: str | os.PathLike[str]) -> bool:
        if not os.path.isdir(path):
            return False
        if self.dry_run:
            self.logger.info("[DRY-RUN] Would remove folder '%s'", path)
            self.stats.dirs_removed += 1
            return True
        try:
            shutil.rmtree(path)
        except OSError as exc:
            self._fail(f"Could not remove folder '{path}'", exc)
            return False
        self.stats.dirs_removed += 1
        self.logger.info("Removed folder '%s'", path)
        return True

    def remove_file(self, path: str | os.PathLike[str]) -> bool:
        if not os.path.isfile(path):
            return False
        if self.dry_run:
            self.logger.info("[DRY-RUN] Would remove file '%s'", path)
            self.stats.files_removed += 1
            return True
        try:
            os.remove(path)
        except OSError as exc:
            self._fail(f"Could not remove file '{path}'", exc)
            return False
        self.stats.files_removed += 1
        self.logger.info("Removed file '%s'", path)
        return True

    # -- files -----------------------------------------------------------
    def files_differ(self, src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> bool:
        """Compare two files, cheaply when possible."""
        try:
            src_stat = os.stat(src)
            dst_stat = os.stat(dst)
        except OSError:
            return True
        if src_stat.st_size != dst_stat.st_size:
            return True
        src_hash = file_digest(src, self.algorithm)
        dst_hash = file_digest(dst, self.algorithm)
        if src_hash is None or dst_hash is None:
            return True
        return src_hash != dst_hash

    def copy_file(self, src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> bool:
        """Copy a single file, creating the parent directory as needed."""
        if self.dry_run:
            self.logger.info("[DRY-RUN] Would copy '%s' -> '%s'", src, dst)
            self.stats.files_copied += 1
            return True
        parent = os.path.dirname(str(dst))
        if parent and not os.path.isdir(parent):
            try:
                os.makedirs(parent, exist_ok=True)
            except OSError as exc:
                self._fail(f"Could not create folder '{parent}'", exc)
                return False
        try:
            shutil.copy2(src, dst)
        except (OSError, shutil.Error) as exc:
            self._fail(f"Could not copy '{src}' -> '{dst}'", exc)
            return False
        self.stats.files_copied += 1
        self.logger.info("Copied '%s' -> '%s'", src, dst)
        return True

    def copy_tree(self, src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> bool:
        """Recursively copy a directory, honouring the ``.sto`` filter."""
        if not os.path.isdir(src):
            return False
        if not self.ensure_dir(dst):
            return False
        ok = True
        for name in safe_listdir(src):
            source = os.path.join(str(src), name)
            target = os.path.join(str(dst), name)
            if os.path.islink(source):
                self.logger.debug("Skipping symlink '%s'", source)
                continue
            if os.path.isdir(source):
                ok = self.copy_tree(source, target) and ok
            elif self.wants_file(name):
                ok = self.copy_file(source, target) and ok
            else:
                self.logger.debug("Skipping '%s' (not a %s file)", source, SETUP_SUFFIX)
        return ok

    def copy_if_different(self, src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> bool:
        """Copy only when the destination is missing or differs."""
        if os.path.exists(dst) and not self.files_differ(src, dst):
            self.logger.debug("Unchanged '%s'", dst)
            return False
        return self.copy_file(src, dst)


def newest(path_a: str | os.PathLike[str], path_b: str | os.PathLike[str]) -> str:
    """Return whichever path has the newer modification time (``a`` on ties)."""
    try:
        return str(path_a) if os.path.getmtime(path_a) >= os.path.getmtime(path_b) else str(path_b)
    except OSError:
        return str(path_a)


def human_size(num_bytes: float) -> str:
    """Format a byte count for the UI."""
    for unit in ("B", "KB", "MB", "GB"):
        if abs(num_bytes) < 1024.0 or unit == "GB":
            return f"{num_bytes:.0f} {unit}" if unit == "B" else f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.1f} GB"


def resolve_within(root: str | os.PathLike[str], *parts: str) -> Path:
    """Join ``parts`` under ``root`` and ensure the result stays inside it.

    Used when a name comes from user input or an archive.
    """
    root_path = Path(root).resolve()
    candidate = (root_path / Path(*parts)).resolve()
    if candidate != root_path and root_path not in candidate.parents:
        raise ValueError(f"Path '{candidate}' escapes '{root_path}'")
    return candidate
