# Nishizumi Setups Sync

[**Download the latest release here**](https://github.com/nishizumi-maho/nishizumi-setups-sync/releases/latest)

## Setup Manager

This repository provides Python script `nishizumi_setups_sync.py` to copy iRacing setup files.

## Features

- Import setups from a ZIP or RAR archive or from another folder, or skip importing entirely.
- Customisable team, personal, setup supplier folder and season folder names.
- Remembers the last configuration in `user_config.json`.
- Works with any Setup supplier: select the folder or ZIP/RAR they provide.
- Can run silently when executed with the `--silent` argument or, when
  `Run silently on startup` is enabled, if the script is launched without a
  console (for example via `pythonw`).
  - Automatically synchronises a source folder to a destination folder for
    every car. NASCAR Cup, Xfinity and Trucks cars first share files between
    their source folders so that each variant receives new files from the
    others. When these folders contain a "Data packs" subfolder, that
    subfolder is copied as well.
  - Data packs across all NASCAR variants remain synchronised.
  - If enabled, one or more additional folders (e.g. from third-party sync
    tools) are copied as subfolders inside the source folder before the normal
    synchronisation copies everything to the team folder. Each extra folder can
    be taken either from the car directory or from inside the sync destination
    folder. When gathered from the destination folder the original is removed
    after copying to keep things tidy.
- By default only `.sto` files are copied; a checkbox allows copying every file
  type instead.
- Optionally create driver-specific folders in the destination. When enabled,
  setups are synced to a `Common Setups` folder and to each named driver folder
  without overwriting the drivers' custom versions.
- When driver folders are active, imported setups and any extra sync folders are
  automatically copied into each driver directory and the `Common Setups`
  folder.
- When driver folders are active, any `Data packs` subfolder found inside the
  sync source is merged into each driver directory and the `Common Setups`
  folder instead of being copied separately.
- Optional Garage61 integration can fetch the list of drivers from the
  Garage61 API so driver folders are created and removed automatically.
- When an unknown car folder is detected while running the GUI,
  the script asks which iRacing folder to use and remembers the choice.
- Optional backup: the iRacing setups folder can be copied to a selected folder
  before syncing and again to another folder afterwards. If the same locations
  are reused on later runs the previous contents are overwritten.
- Optional logging: when enabled, actions are appended to a log file for later
  review.
- Built-in update function to download the latest script version.

## Installation

Install Python 3 and the required dependencies:

```bash
pip install -r requirements.txt
```

## Usage

Run the script and choose the iRacing setups folder. Depending on the selected
import mode you may also pick a ZIP or RAR file or a source folder. Configure folder
names as needed and press **Run**. The selected options are saved for next
time. If backup is enabled, the iRacing folder is copied to the first backup
location before any syncing occurs and again to the second location after
synchronisation. Reusing the same folders will overwrite the previous backup
each time.

To automate the process on start-up, enable `Run silently on startup`. Place a
shortcut that launches the script without a console (for example using
`pythonw` or passing `--silent`) in your operating system's start-up folder.

```bash
python nishizumi_setups_sync.py     # open the graphical interface
python nishizumi_setups_sync.py --silent  # run with saved options without showing UI
python nishizumi_setups_sync.py --gui     # force the graphical interface even if "Run silently on startup" is set
python nishizumi_setups_sync.py --update  # download the latest version
```

If the configured iRacing folder, ZIP/RAR file or source folder is missing when
running silently, the script prints a message and exits without copying.

If the GUI cannot start because no display is available (for example when
running on a server), the script also falls back to silent mode.
If Tkinter is missing entirely, any prompts for unknown car folders are shown
in the console instead of a popup dialog.

## How to Use

1. Run `python nishizumi_setups_sync.py` to open the interface.
2. Select your **iRacing Setups Folder path** using the folder browser and choose
   whether to import from a ZIP/RAR file or from another folder.
3. Fill in the team, personal, setup supplier folder and season folder names
   with *folder names only*. These folders must exist inside your iRacing setups
   folder and will be created automatically if they do not. The script uses
   these folders inside every car directory. When importing from a ZIP/RAR or
   folder, the personal folder also includes the setup supplier and season
   subfolders so the structure matches the team folder.
4. Optionally enable backup or logging and browse to the **Backup Folder (before)**,
   **Backup Folder (after)** and log file locations.
5. Click **Save Config** to store your settings without running.
6. Press **Run** to perform the import and sync. The settings are saved for the
   next time you open the tool.
7. Click **Check for Updates** to fetch the latest version when needed.

### Example Configuration

```
iRacing Setups Folder:   C:\iRacing\setups
Backup Folder (before):  D:\SetupsBackupBefore
Backup Folder (after):   D:\SetupsBackupAfter
Team Folder Name:        MyTeam
Personal Folder Name:    DriverOne
Season Folder Name:      2025S1
```

## Interface Guide

When running without `--silent`, the script shows a window where you can
configure how setups are imported. The window resizes automatically to fit all
options. Each setting is saved in `user_config.json` for the next run. When
packaged as an executable, this file sits alongside the `.exe` so your
preferences persist between launches.

Only the **iRacing Setups Folder** and the backup folder fields expect full
paths. Every other folder input should contain just a folder name that will be
created or monitored inside each car directory. These folders must be located
inside your iRacing setups folder and will be created automatically if they do
not exist.

* **iRacing Setups Folder** – root folder that stores all car setup folders.
  Select the full path using the folder browser.
* **Enable backup** – when checked, two backups of the iRacing folder are taken:
  one before syncing starts and one after it finishes.
* **Backup Folder (before)** – directory for the pre-run backup.
* **Backup Folder (after)** – directory for the post-run backup.
* **Enable logging** – write operations to a log file during execution.
* **Log File** – path of the file used when logging is enabled.
* **Import Mode** – choose **Zip Import** to unpack a ZIP or RAR file,
  **Folder Import** to copy from an existing folder, or **No Import** to skip
  importing and only synchronise existing files. When **No Import** is
  selected, the path fields for the archive file and source folder are hidden,
  along with the team and personal folder fields.
* **Archive File to Import** – path when using Zip Import (ZIP or RAR).
* **Folder to Import** – folder to copy from when using Folder Import.
* **Team Folder Name (destination)** – team directory to place files in
  (default `Example Team`). Use only the folder name; do not include a path.
* **Personal Folder Name (source)** – your personal folder that provides
  files (default `My Personal Folder`). Name only, no path. When importing
  from a ZIP/RAR or folder this folder also contains the setup supplier and
  season subfolders, mirroring the team folder layout.
* **Setup Supplier Folder Name (inside team folder)** – subfolder for your
  setup supplier name (default `Example Supplier`). Invalid characters and
  trailing spaces are automatically removed. Name only.
* **Season Folder Name (inside supplier folder)** – season subfolder
  (default `Example Season`). Name only.
* **Sync Source Folder (copy from)** – name of the source folder in each car
  directory (default `Example Source`). Name only.
* **Sync Destination Folder (copy to)** – destination folder name in each car
  directory (default `Example Destination`). The `Data packs` subfolder is
  synced automatically. When driver folders are enabled, this folder is merged
  into the driver and common directories instead of being copied separately.
  Name only.
* **Driver Folders** – sync setups to a common folder and optionally to each
  driver’s personal folder.
* **Use Garage61 API for drivers** – when enabled, the driver list is fetched
  from the Garage61 service. Provide your team ID and optional API key.
* **Garage61 Team ID** – identifier of your team on Garage61.
* **Garage61 API Key** – authentication token if required by the API. When this
  option is enabled, the manual driver list is hidden because driver folders are
  managed automatically.
* **Manually write drivers names** – copy setups to a shared `Common Setups`
  folder and to each driver name without overwriting their custom versions.
* **Number of drivers** – how many driver folders to create when manual entry is
  enabled.
* **Driver N Name** – text fields for each driver folder name. Use names only.
* **Use extra sync folders** – when enabled, the additional folders listed below
  are copied as subfolders inside the source folder for every car before the
  usual sync copies the files to the team folder.
* **Number of extra folders** – how many additional folder names to provide.
* **Extra Folder N Name** – the name of each folder created by external sync
  tools (for example `ExampleTool`). Use just the folder name. For each entry
  you can also choose whether the folder is located directly inside the car
  directory or inside the sync destination folder.
* **Hash Algorithm (file comparison)** – method used to detect changes.
* **Copy everything (not just .sto)** – when enabled, the tool copies every
  file type instead of only `.sto` files.
 * **Run silently on startup** – if enabled, the script runs silently when
   launched without a console (for example using `pythonw`) and otherwise shows
   the interface.
 * **Save Config** – stores the current options without running any action.
 * **Run** – saves the options and performs the copy operation.
 * **Check for Updates** – downloads the latest version of the script when
   available.

   ## Running Silently on Windows Startup

To run Nishizumi Setups Sync automatically and silently at system startup:

1. Open the interface (`python nishizumi_setups_sync.py`), configure everything, and enable **Run silently on startup** in the settings.
2. If you're using the Python script directly, create a shortcut pointing to:

    ```
    pythonw.exe path\to\nishizumi_setups_sync.py
    ```

3. If you're using the compiled EXE, create a shortcut pointing to:

    ```
    path\to\nishizumi_setups_sync.exe
    ```

4. Place this shortcut into your Windows **Startup** folder.

The tool will automatically run silently on boot. If any folders are missing, it will skip copying and exit cleanly.


## Building a Windows EXE

To run the tool without requiring Python installed, you can create a standalone
executable using [PyInstaller](https://pyinstaller.org/). These steps must be
performed on a Windows machine:

1. Install Python 3 if it is not already installed.
2. Install PyInstaller from a command prompt:

   ```bash
   pip install pyinstaller
   ```

3. In the repository folder, run PyInstaller to build a single-file executable:

   ```bash
   pyinstaller --onefile nishizumi_setups_sync.py
   ```

The resulting `nishizumi_setups_sync.exe` will appear in the `dist` folder. You can
share or run this file directly without needing Python. The program stores its
configuration in `user_config.json` next to the executable, so ensure the folder
containing the `.exe` allows write access.

## Future Ideas

Some potential improvements for later versions:

- Schedule synchronisation at specific times rather than only on startup.
- Display a progress bar for long operations.
- Send a notification when the sync completes or if an error occurs.
