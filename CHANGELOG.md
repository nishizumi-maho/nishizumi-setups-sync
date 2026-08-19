# Changelog

All notable changes to this project are documented here.
This project follows [Semantic Versioning](https://semver.org/).

## 3.0.0

A full review and rewrite of the application. The behaviour you rely on is
unchanged, but the code is now split into modules, covered by tests, and the
update system actually works.

### Added

- **Automatic updates.** The application checks GitHub Releases on startup
  (throttled, configurable) and can download and install a new version by
  itself. Frozen executables are replaced by a helper that waits for the app to
  exit and then restarts it; source installations are refreshed from the release
  archive; git checkouts and pip installs are told what to run instead.
- Update settings in the GUI and in `user_config.json`: check on startup, check
  interval, stable or pre-release channel, and automatic installation.
- `check-update` and `update` commands, plus `--version` and `--pre` flags.
- SHA-256 verification of every downloaded artefact, using the checksum GitHub
  publishes for the asset or the `SHA256SUMS` file shipped with the release.
- A release workflow that builds Windows and Linux executables, publishes
  checksums, and creates the GitHub release from the changelog.
- A test suite (163 tests, including the interface) and a CI workflow on Linux and Windows
  against Python 3.9 and 3.12.
- Profile management in the GUI (create, rename, delete, switch).
- The car mapping editor, configuration import/export and "reset to defaults",
  all of which the README documented but the shipped build did not have.
- `mapping` and `config` CLI commands for scripting.
- A `tray` command, so "stay in the tray" finally does something.
- Plugin hooks (`before_sync` / `after_sync`) are implemented and opt-in.
- `--config` for an alternative configuration file, and `--log-level`.

### Fixed

- **Dry-run mode wrote to disk.** It created folders and extracted archives while
  claiming to only simulate. Nothing touches the filesystem now.
- **The sync deleted files it did not manage.** With "copy everything" off, any
  non-`.sto` file in the destination was deleted even when the same file existed
  in the source — notes, telemetry and data packs were lost. Only entries that
  are genuinely missing from the source are removed now.
- **The updater downloaded the default branch** of a repository path that no
  longer exists, compared versions with `!=` (so an older version counted as an
  update) and overwrote `sys.argv[0]`, which could never work for the packaged
  executable.
- **Archives were extracted without validation.** A crafted ZIP could write
  outside the target folder (Zip Slip). Entries are now validated and symlinks
  are rejected. Extraction happens in a temporary directory instead of next to
  the archive, where it could overwrite an existing folder.
- Archives and folders that wrap the car folders in a single directory are now
  detected instead of importing nothing.
- NASCAR and Super Formula variants that had no source folder yet were skipped;
  every car of the group that you own now receives the shared setups.
- Configuration writes are atomic and report failures instead of silently doing
  nothing; a corrupt file is moved aside rather than overwritten.
- A failed Garage 61 lookup no longer wipes the configured driver list, and the
  configuration is only rewritten when the list really changed.
- Unrecognised car folders are resolved before a run starts (with a dialog in
  the GUI) instead of blocking a background sync on `input()`.
- Dead code removed: two definitions of `perform_sync`, lambdas that resolved
  functions through `globals()`, and a tray helper nothing ever called.
- The "Add extra folder" buttons existed but were never added to the layout, so
  extra folders could not be managed from the GUI at all.
- Tabs in names were deleted rather than turned into spaces by `clean_name`.
- Car folder matching is deterministic: exact matches win, then the longest
  alias, so `BMW GT4 Evo` no longer resolves like `BMW GT4`.

### Changed

- The single 1 400-line script (checked in without a `.py` extension) is now the
  `nishizumi_sync` package. `nishizumi_setups_sync.py` remains as the launcher
  and still exposes `load_config`, `save_config`, `run_silent` and `Logger`.
- The sync runs on a worker thread: the window stays responsive and streams the
  log live instead of freezing until the run finishes.
- Logging uses the standard library, with levels, rotation and a size cap.
- Configuration, mapping, log and state files move to the per-user data
  directory when the install folder is read-only; `NISHIZUMI_SYNC_HOME`
  overrides the location.
- File comparison checks the size before hashing.
- The Garage 61 client accepts every response shape the API has used, and
  reports authentication and not-found errors distinctly.
- `requests` is optional everywhere: update checks and Garage 61 fall back to
  the standard library.
- Removed `OLD_nishizumi_setups_sync.py` and the extension-less `v2` script.

## 2.0.0

Released from the previous single-file script (tagged `V2.0.0`). Superseded by
3.0.0, which is where the review below applies.

## 1.1.0-fullgui

- Full PySide6 interface, profile system, dry-run mode and CLI subcommands.
- Custom car mapping, driver folder management and extra folders.
- Backup, logging and tray options.
