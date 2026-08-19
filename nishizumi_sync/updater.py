"""Update checking and installation.

The previous updater downloaded ``nishizumi_setups_sync.py`` from the default
branch, compared the version string with ``!=`` (so any change looked like an
update, including downgrades) and overwrote ``sys.argv[0]``.  That could not
work for the packaged executable most people actually run, and it happily
installed whatever was on ``main`` at the time.

This implementation talks to the GitHub Releases API, compares versions
properly, verifies checksums when the release publishes them, and knows how to
replace both a frozen executable and a source checkout.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from . import GITHUB_REPOSITORY, __version__
from .http import HttpError, download, get
from .paths import install_dir, is_frozen, update_state_file

GITHUB_API = "https://api.github.com"
RELEASES_PATH = "/repos/{repo}/releases"
CHECKSUM_ASSET_NAMES = ("SHA256SUMS", "sha256sums.txt", "checksums.txt")

ProgressCallback = Callable[[int, int], None]


class UpdateError(Exception):
    """Raised when an update cannot be checked, downloaded or installed."""


# ---------------------------------------------------------------------------
# Version handling
# ---------------------------------------------------------------------------

_RELEASE_RE = re.compile(r"^\s*[vV]?(\d+(?:\.\d+)*)(?:[-+]([0-9A-Za-z.\-]+))?\s*$")


@dataclass(frozen=True)
class Version:
    """A comparable, semver-flavoured version.

    Accepts ``2.0.0``, ``v2.0.0``, ``2.0.0-beta.1`` and legacy strings such as
    ``1.1.0-fullgui``.  A version with a pre-release suffix sorts *before* the
    same version without one, as semver requires.
    """

    release: tuple[int, ...]
    prerelease: tuple[str, ...] = ()
    raw: str = ""

    @classmethod
    def parse(cls, text: str | None) -> "Version | None":
        if not text:
            return None
        match = _RELEASE_RE.match(str(text))
        if not match:
            return None
        numbers = tuple(int(part) for part in match.group(1).split("."))
        suffix = match.group(2) or ""
        prerelease = tuple(part for part in re.split(r"[.\-+]", suffix) if part)
        return cls(numbers, prerelease, str(text).strip())

    @property
    def is_prerelease(self) -> bool:
        return bool(self.prerelease)

    def _release_key(self, width: int) -> tuple[int, ...]:
        padded = self.release + (0,) * (width - len(self.release))
        return padded[:width]

    def _prerelease_key(self) -> tuple[tuple[int, int, str], ...]:
        key: list[tuple[int, int, str]] = []
        for part in self.prerelease:
            if part.isdigit():
                key.append((0, int(part), ""))
            else:
                key.append((1, 0, part.lower()))
        return tuple(key)

    def _compare(self, other: "Version") -> int:
        width = max(len(self.release), len(other.release), 3)
        mine, theirs = self._release_key(width), other._release_key(width)
        if mine != theirs:
            return -1 if mine < theirs else 1
        if self.is_prerelease != other.is_prerelease:
            # No pre-release wins.
            return 1 if other.is_prerelease else -1
        mine_pre, theirs_pre = self._prerelease_key(), other._prerelease_key()
        if mine_pre == theirs_pre:
            return 0
        return -1 if mine_pre < theirs_pre else 1

    def __lt__(self, other: "Version") -> bool:
        return self._compare(other) < 0

    def __le__(self, other: "Version") -> bool:
        return self._compare(other) <= 0

    def __gt__(self, other: "Version") -> bool:
        return self._compare(other) > 0

    def __ge__(self, other: "Version") -> bool:
        return self._compare(other) >= 0

    def __str__(self) -> str:
        return self.raw or ".".join(str(n) for n in self.release)


def is_newer(candidate: str | None, current: str | None = None) -> bool:
    """True when ``candidate`` is a strictly newer version than ``current``."""
    new = Version.parse(candidate)
    old = Version.parse(current if current is not None else __version__)
    if new is None:
        return False
    if old is None:
        return True
    return new > old


# ---------------------------------------------------------------------------
# Release metadata
# ---------------------------------------------------------------------------

@dataclass
class ReleaseAsset:
    name: str
    url: str
    size: int = 0
    digest: str = ""

    @classmethod
    def from_api(cls, payload: dict) -> "ReleaseAsset":
        digest = str(payload.get("digest") or "")
        if digest.startswith("sha256:"):
            digest = digest.split(":", 1)[1]
        elif digest:
            digest = ""
        return cls(
            name=str(payload.get("name") or ""),
            url=str(payload.get("browser_download_url") or ""),
            size=int(payload.get("size") or 0),
            digest=digest,
        )


@dataclass
class UpdateInfo:
    version: Version
    tag: str
    name: str
    notes: str
    html_url: str
    prerelease: bool = False
    published_at: str = ""
    source_url: str = ""
    assets: list[ReleaseAsset] = field(default_factory=list)

    @classmethod
    def from_api(cls, payload: dict) -> "UpdateInfo | None":
        tag = str(payload.get("tag_name") or "")
        version = Version.parse(tag) or Version.parse(payload.get("name"))
        if version is None:
            return None
        return cls(
            version=version,
            tag=tag,
            name=str(payload.get("name") or tag),
            notes=str(payload.get("body") or ""),
            html_url=str(payload.get("html_url") or ""),
            prerelease=bool(payload.get("prerelease")),
            published_at=str(payload.get("published_at") or ""),
            source_url=str(payload.get("zipball_url") or ""),
            assets=[ReleaseAsset.from_api(item) for item in payload.get("assets") or []],
        )

    def asset_for_platform(self) -> ReleaseAsset | None:
        """The binary asset matching the running platform, if any."""
        if sys.platform.startswith("win"):
            wanted, suffix = ("win", "windows"), ".exe"
        elif sys.platform == "darwin":
            wanted, suffix = ("mac", "darwin", "osx"), ""
        else:
            wanted, suffix = ("linux",), ""

        candidates = [a for a in self.assets if a.name and not _is_checksum_asset(a.name)]
        if suffix:
            exact = [a for a in candidates if a.name.lower().endswith(suffix)]
            if exact:
                named = [a for a in exact if any(w in a.name.lower() for w in wanted)]
                return (named or exact)[0]
        named = [a for a in candidates if any(w in a.name.lower() for w in wanted)]
        if named:
            return named[0]
        return None

    def checksum_asset(self) -> ReleaseAsset | None:
        for asset in self.assets:
            if _is_checksum_asset(asset.name):
                return asset
        return None


def _is_checksum_asset(name: str) -> bool:
    lowered = name.lower()
    return any(lowered == candidate.lower() for candidate in CHECKSUM_ASSET_NAMES) or lowered.endswith(".sha256")


# ---------------------------------------------------------------------------
# Persistent state
# ---------------------------------------------------------------------------

@dataclass
class UpdateState:
    last_check: str = ""
    last_seen_version: str = ""
    skipped_version: str = ""
    installed_version: str = ""
    pending_version: str = ""

    @classmethod
    def load(cls, path: Path | None = None) -> "UpdateState":
        target = path or update_state_file()
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        if not isinstance(data, dict):
            return cls()
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: str(v or "") for k, v in data.items() if k in known})

    def save(self, path: Path | None = None) -> None:
        target = path or update_state_file()
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(self.__dict__, indent=2) + "\n", encoding="utf-8")
        except OSError:
            pass  # state is a convenience, never fail a run because of it

    @property
    def last_check_at(self) -> datetime | None:
        if not self.last_check:
            return None
        try:
            return datetime.fromisoformat(self.last_check)
        except ValueError:
            return None


# ---------------------------------------------------------------------------
# Install modes
# ---------------------------------------------------------------------------

MODE_FROZEN = "frozen"
MODE_GIT = "git"
MODE_PIP = "pip"
MODE_SOURCE = "source"


def detect_install_mode() -> str:
    """How this copy of the application was installed."""
    if is_frozen():
        return MODE_FROZEN
    root = install_dir()
    if (root / ".git").exists():
        return MODE_GIT
    package = Path(__file__).resolve().parent
    if "site-packages" in package.parts or "dist-packages" in package.parts:
        return MODE_PIP
    return MODE_SOURCE


@dataclass
class InstallResult:
    ok: bool
    message: str
    restart_required: bool = False
    restart_scheduled: bool = False


# ---------------------------------------------------------------------------
# Updater
# ---------------------------------------------------------------------------

class Updater:
    """Checks GitHub Releases and installs what it finds."""

    def __init__(
        self,
        cfg: dict | None = None,
        logger: logging.Logger | None = None,
        *,
        current_version: str = __version__,
        state_path: Path | None = None,
    ) -> None:
        self.cfg = cfg or {}
        self.logger = logger or logging.getLogger("nishizumi_sync")
        self.current_version = current_version
        self.state_path = state_path
        self.state = UpdateState.load(state_path)

    # -- settings --------------------------------------------------------
    @property
    def settings(self) -> dict[str, Any]:
        from .config import DEFAULT_UPDATE_SETTINGS

        settings = dict(DEFAULT_UPDATE_SETTINGS)
        raw = self.cfg.get("updates")
        if isinstance(raw, dict):
            settings.update(raw)
        return settings

    @property
    def repository(self) -> str:
        return str(self.settings.get("repository") or GITHUB_REPOSITORY)

    @property
    def include_prereleases(self) -> bool:
        return str(self.settings.get("channel", "stable")).lower() == "prerelease"

    def due_for_check(self, now: datetime | None = None) -> bool:
        """True when enough time has passed since the last check."""
        if not self.settings.get("check_on_startup", True):
            return False
        interval = int(self.settings.get("check_interval_hours", 24) or 0)
        if interval <= 0:
            return True
        last = self.state.last_check_at
        if last is None:
            return True
        reference = now or datetime.now(timezone.utc)
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        return reference - last >= timedelta(hours=interval)

    # -- checking --------------------------------------------------------
    def _api(self, path: str, timeout: int = 15) -> Any:
        url = f"{GITHUB_API}{path}"
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return get(url, headers=headers, timeout=timeout).json()

    def fetch_releases(self, limit: int = 20) -> list[UpdateInfo]:
        """Published releases, newest version first."""
        path = RELEASES_PATH.format(repo=self.repository) + f"?per_page={max(1, min(limit, 100))}"
        payload = self._api(path)
        if not isinstance(payload, list):
            raise UpdateError("Unexpected response from the GitHub releases API")
        releases: list[UpdateInfo] = []
        for item in payload:
            if not isinstance(item, dict) or item.get("draft"):
                continue
            info = UpdateInfo.from_api(item)
            if info is None:
                continue
            if info.prerelease and not self.include_prereleases:
                continue
            releases.append(info)
        releases.sort(key=lambda info: info.version, reverse=True)
        return releases

    def check(self, *, force: bool = False, record: bool = True) -> UpdateInfo | None:
        """Return the newest release when it is newer than the running version.

        ``force`` ignores both the check interval and a previously skipped
        version.
        """
        if not force and not self.due_for_check():
            self.logger.debug("Update check skipped (checked recently)")
            return None

        try:
            releases = self.fetch_releases()
        except (HttpError, UpdateError) as exc:
            self.logger.warning("Update check failed: %s", exc)
            return None

        if record:
            self.state.last_check = datetime.now(timezone.utc).isoformat(timespec="seconds")
            self.state.save(self.state_path)

        if not releases:
            self.logger.info("No published releases found for %s", self.repository)
            return None

        newest = releases[0]
        if record:
            self.state.last_seen_version = str(newest.version)
            self.state.save(self.state_path)

        if not is_newer(str(newest.version), self.current_version):
            self.logger.info("Already running the latest version (%s)", self.current_version)
            return None
        if not force and self.state.skipped_version and self.state.skipped_version == str(newest.version):
            self.logger.info("Version %s was skipped by the user", newest.version)
            return None

        self.logger.info("Update available: %s (current %s)", newest.version, self.current_version)
        return newest

    def skip(self, info: UpdateInfo) -> None:
        """Remember that the user does not want this version."""
        self.state.skipped_version = str(info.version)
        self.state.save(self.state_path)

    # -- downloading -----------------------------------------------------
    def download_asset(
        self,
        info: UpdateInfo,
        destination_dir: Path,
        *,
        progress: ProgressCallback | None = None,
    ) -> tuple[Path, ReleaseAsset | None]:
        """Fetch the right artefact for this installation."""
        destination_dir.mkdir(parents=True, exist_ok=True)
        mode = detect_install_mode()

        if mode == MODE_FROZEN:
            asset = info.asset_for_platform()
            if asset is None:
                raise UpdateError(
                    f"Release {info.version} has no download for this platform "
                    f"({sys.platform}); download it manually from {info.html_url}"
                )
            target = destination_dir / asset.name
            self.logger.info("Downloading %s (%s)", asset.name, _format_size(asset.size))
            download(asset.url, target, progress=progress, timeout=300)
            self._verify(target, asset, info)
            return target, asset

        if not info.source_url:
            raise UpdateError(f"Release {info.version} does not expose a source archive")
        target = destination_dir / f"source-{info.tag or info.version}.zip"
        self.logger.info("Downloading source archive for %s", info.version)
        download(info.source_url, target, progress=progress, timeout=300)
        return target, None

    def _verify(self, path: Path, asset: ReleaseAsset, info: UpdateInfo) -> None:
        """Check the download against whatever checksum the release provides."""
        expected = asset.digest
        if not expected:
            checksum_asset = info.checksum_asset()
            if checksum_asset:
                try:
                    body = get(checksum_asset.url, timeout=30).text
                    expected = _find_checksum(body, asset.name)
                except HttpError as exc:
                    self.logger.warning("Could not download %s: %s", checksum_asset.name, exc)
        if not expected:
            self.logger.warning("Release %s publishes no checksum for %s", info.version, asset.name)
            return
        actual = _sha256(path)
        if actual.lower() != expected.lower():
            path.unlink(missing_ok=True)
            raise UpdateError(
                f"Checksum mismatch for {asset.name}: expected {expected[:12]}…, got {actual[:12]}…"
            )
        self.logger.info("Checksum verified for %s", asset.name)

    # -- installing ------------------------------------------------------
    def install(self, info: UpdateInfo, *, progress: ProgressCallback | None = None) -> InstallResult:
        """Download and install ``info``, choosing a strategy per install mode."""
        mode = detect_install_mode()
        if mode == MODE_GIT:
            return InstallResult(
                False,
                "This copy is a git checkout — run 'git pull' to update it.",
            )
        if mode == MODE_PIP:
            return InstallResult(
                False,
                "This copy was installed with pip — run "
                "'pip install --upgrade nishizumi-sync' to update it.",
            )

        workdir = Path(tempfile.mkdtemp(prefix="nishizumi-update-"))
        try:
            artefact, _asset = self.download_asset(info, workdir, progress=progress)
            if mode == MODE_FROZEN:
                result = self._install_frozen(artefact, info)
            else:
                result = self._install_source(artefact, info)
        except (HttpError, UpdateError) as exc:
            shutil.rmtree(workdir, ignore_errors=True)
            return InstallResult(False, str(exc))
        except OSError as exc:
            shutil.rmtree(workdir, ignore_errors=True)
            return InstallResult(False, f"Update failed: {exc}")

        if result.ok:
            self.state.pending_version = str(info.version)
            self.state.save(self.state_path)
        if not result.restart_scheduled:
            shutil.rmtree(workdir, ignore_errors=True)
        return result

    def _install_frozen(self, artefact: Path, info: UpdateInfo) -> InstallResult:
        """Swap the running executable once this process exits."""
        target = Path(sys.executable).resolve()
        script = _write_swap_script(artefact, target, os.getpid())
        try:
            _spawn_detached(script)
        except OSError as exc:
            return InstallResult(False, f"Could not start the updater helper: {exc}")
        return InstallResult(
            True,
            f"Version {info.version} downloaded. The application will restart to finish the update.",
            restart_required=True,
            restart_scheduled=True,
        )

    def _install_source(self, archive: Path, info: UpdateInfo) -> InstallResult:
        """Replace the package directory of a plain source installation."""
        root = install_dir()
        staging = archive.parent / "extracted"
        with zipfile.ZipFile(archive) as bundle:
            _safe_extract(bundle, staging)

        source_root = _single_child(staging)
        package_src = source_root / "nishizumi_sync"
        if not package_src.is_dir():
            raise UpdateError("The downloaded archive does not contain the application package")

        backup = root / "nishizumi_sync.backup"
        package_dst = root / "nishizumi_sync"
        if backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
        try:
            if package_dst.exists():
                package_dst.rename(backup)
            shutil.copytree(package_src, package_dst)
            for extra in ("nishizumi_setups_sync.py", "requirements.txt"):
                candidate = source_root / extra
                if candidate.is_file():
                    shutil.copy2(candidate, root / extra)
        except OSError as exc:
            if backup.exists() and not package_dst.exists():
                backup.rename(package_dst)
            raise UpdateError(f"Could not replace the application files: {exc}") from exc
        shutil.rmtree(backup, ignore_errors=True)
        return InstallResult(
            True,
            f"Version {info.version} installed. Restart the application to use it.",
            restart_required=True,
        )

    # -- bookkeeping -----------------------------------------------------
    def note_startup(self) -> str | None:
        """Report (once) that a pending update has been applied."""
        pending = self.state.pending_version
        if not pending:
            return None
        self.state.pending_version = ""
        if not is_newer(pending, self.current_version):
            self.state.installed_version = self.current_version
            self.state.save(self.state_path)
            return f"Updated to version {self.current_version}."
        self.state.save(self.state_path)
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _find_checksum(body: str, filename: str) -> str:
    """Pull ``filename``'s hash out of a ``sha256sum`` style listing."""
    for line in body.splitlines():
        parts = line.split()
        if len(parts) >= 2 and Path(parts[-1].lstrip("*")).name == filename:
            return parts[0]
    return ""


def _format_size(size: int) -> str:
    from .fileops import human_size

    return human_size(size) if size else "unknown size"


def _single_child(path: Path) -> Path:
    """GitHub source archives wrap everything in one directory."""
    children = [child for child in path.iterdir() if child.is_dir()]
    if len(children) == 1:
        return children[0]
    return path


def _safe_extract(bundle: zipfile.ZipFile, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    for member in bundle.infolist():
        target = (root / member.filename).resolve()
        if target != root and root not in target.parents:
            raise UpdateError(f"Archive entry '{member.filename}' escapes the extraction folder")
    bundle.extractall(root)


def _write_swap_script(new_file: Path, target: Path, pid: int) -> Path:
    """Create the helper that waits for us to exit, then swaps the binary."""
    directory = new_file.parent
    if sys.platform.startswith("win"):
        script = directory / "apply_update.bat"
        script.write_text(
            "@echo off\r\n"
            "setlocal\r\n"
            f"set TARGET={target}\r\n"
            f"set SOURCE={new_file}\r\n"
            ":wait\r\n"
            "ping -n 2 127.0.0.1 >nul\r\n"
            f'tasklist /FI "PID eq {pid}" 2>nul | find "{pid}" >nul\r\n'
            "if not errorlevel 1 goto wait\r\n"
            'move /Y "%SOURCE%" "%TARGET%" >nul\r\n'
            'if errorlevel 1 exit /b 1\r\n'
            'start "" "%TARGET%"\r\n'
            'del "%~f0"\r\n',
            encoding="utf-8",
        )
        return script

    script = directory / "apply_update.sh"
    script.write_text(
        "#!/bin/sh\n"
        f"while kill -0 {pid} 2>/dev/null; do sleep 0.5; done\n"
        f'mv -f "{new_file}" "{target}" || exit 1\n'
        f'chmod +x "{target}"\n'
        f'"{target}" &\n'
        'rm -- "$0"\n',
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def _spawn_detached(script: Path) -> None:
    """Start the helper so it survives this process exiting."""
    if sys.platform.startswith("win"):
        creationflags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        subprocess.Popen(  # noqa: S603 - fixed, locally generated command
            ["cmd", "/c", str(script)],
            close_fds=True,
            creationflags=creationflags,
        )
    else:
        subprocess.Popen(  # noqa: S603
            ["/bin/sh", str(script)],
            close_fds=True,
            start_new_session=True,
        )


def summarise_release(info: UpdateInfo, *, max_lines: int = 20) -> str:
    """Release notes trimmed for a message box."""
    lines = [line.rstrip() for line in (info.notes or "").splitlines()]
    while lines and not lines[0]:
        lines.pop(0)
    if len(lines) > max_lines:
        lines = lines[:max_lines] + ["…"]
    return "\n".join(lines).strip()


def check_for_update(
    cfg: dict | None = None,
    logger: logging.Logger | None = None,
    *,
    force: bool = True,
) -> UpdateInfo | None:
    """Convenience wrapper used by the CLI and the GUI."""
    return Updater(cfg, logger).check(force=force)
