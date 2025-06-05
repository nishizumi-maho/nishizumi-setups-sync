# Nishizumi Setups Sync

## Setup Manager
This repository provides a single Python script `nishizumi_setups_sync.py` to copy iRacing setup files.

## Features

- Import setups from a ZIP archive or from another folder, or skip importing entirely.
- Customisable team, personal, setup supplier folder and season folder names.
- Remembers the last configuration in `user_config.json`.
- Works with any Setup supplier: select the folder or ZIP they provide.
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
    synchronisation copies everything to the team folder.
- By default only `.sto` files are copied; a checkbox allows copying every file
  type instead.
- Optionally create driver-specific folders in the destination. When enabled,
  setups are synced to a `Common Setups` folder and to each named driver folder
  without overwriting the drivers' custom versions.
- Optional Garage61 integration can fetch the list of drivers from the
  Garage61 API so driver folders are created and removed automatically.
- When an unknown car folder is detected while running the GUI,
  the script asks which iRacing folder to use and remembers the choice.
- Optional backup: copy new files from the iRacing setups folder to a chosen
  backup folder on every run. Existing backups are preserved.
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
import mode you may also pick a ZIP file or a source folder. Configure folder
names as needed and press **Run**. The selected options are saved for next
time. If backup is enabled, new files are copied to the backup folder on each
execution.

To automate the process on start-up, enable `Run silently on startup`. Place a
shortcut that launches the script without a console (for example using
`pythonw` or passing `--silent`) in your operating system's start-up folder.

```bash
python nishizumi_setups_sync.py     # open the graphical interface
python nishizumi_setups_sync.py --silent  # run with saved options without showing UI
python nishizumi_setups_sync.py --gui     # force the graphical interface even if "Run silently on startup" is set
python nishizumi_setups_sync.py --update  # download the latest version
```

If the configured iRacing folder, zip file or source folder is missing when
running silently, the script prints a message and exits without copying.

If the GUI cannot start because no display is available (for example when
running on a server), the script also falls back to silent mode.

## How to Use

1. Run `python nishizumi_setups_sync.py` to open the interface.
2. Select your **iRacing Setups Folder path** using the folder browser and choose
   whether to import from a ZIP file or from another folder.
3. Fill in the team, personal, setup supplier folder and season folder names
   with *folder names only*. These folders must exist inside your iRacing setups
   folder and will be created automatically if they do not. The script uses
   these folders inside every car directory.
4. Optionally enable backup or logging and browse to the **Backup Folder** and
   log file locations.
5. Press **Run** to perform the import and sync. The settings are saved for the
   next time you open the tool.
6. Click **Check for Updates** to fetch the latest version when needed.

### Example Configuration

```
iRacing Setups Folder: C:\iRacing\setups
Backup Folder:        D:\SetupsBackup
Team Folder Name:     MyTeam
Personal Folder Name: DriverOne
Season Folder Name:   2025S1
```

## Interface Guide

When running without `--silent`, the script shows a window where you can
configure how setups are imported. The window resizes automatically to fit all
options. Each setting is saved in `user_config.json` for the next run. When
packaged as an executable, this file sits alongside the `.exe` so your
preferences persist between launches.

Only the **iRacing Setups Folder** and **Backup Folder** fields expect full
paths. Every other folder input should contain just a folder name that will be
created or monitored inside each car directory. These folders must be located
inside your iRacing setups folder and will be created automatically if they do
not exist.

* **iRacing Setups Folder** – root folder that stores all car setup folders.
  Select the full path using the folder browser.
* **Enable backup** – when checked, a copy of new files is stored in the
  specified backup folder each time the program runs.
* **Backup Folder** – directory where backups are placed when backup is enabled.
  Provide the full path here.
* **Enable logging** – write operations to a log file during execution.
* **Log File** – path of the file used when logging is enabled.
* **Import Mode** – choose **Zip Import** to unpack a ZIP file,
  **Folder Import** to copy from an existing folder, or **No Import** to skip
  importing and only synchronise existing files. When **No Import** is
  selected, the path fields for the ZIP file and source folder are hidden,
  along with the team and personal folder fields.
* **Zip File to Import** – archive path when using Zip Import.
* **Folder to Import** – folder to copy from when using Folder Import.
* **Team Folder Name (destination)** – team directory to place files in
  (default `Example Team`). Use only the folder name; do not include a path.
* **Personal Folder Name (source)** – your personal folder that provides
  files (default `My Personal Folder`). Name only, no path.
* **Setup Supplier Folder Name (inside team folder)** – subfolder for your
  setup supplier name (default `Example Supplier`). Invalid characters and
  trailing spaces are automatically removed. Name only.
* **Season Folder Name (inside supplier folder)** – season subfolder
  (default `Example Season`). Name only.
* **Sync Source Folder (copy from)** – name of the source folder in each car
  directory (default `Example Source`). Name only.
* **Sync Destination Folder (copy to)** – destination folder name in each car
  directory (default `Example Destination`). The `Data packs` subfolder is
  synced automatically. Name only.
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
  tools (for example `ExampleTool`). Name only.
* **Hash Algorithm (file comparison)** – method used to detect changes.
* **Copy everything (not just .sto)** – when enabled, the tool copies every
  file type instead of only `.sto` files.
* **Run silently on startup** – if enabled, the script runs silently when
  launched without a console (for example using `pythonw`) and otherwise shows
  the interface.
* **Run** – saves the options and performs the copy operation.
* **Check for Updates** – downloads the latest version of the script when
  available.

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

> **Warning**
> At the moment packaging the script as an executable does not work correctly.
> The configuration is not saved persistently when running the `.exe`, so for now
> you should run the script with Python instead:
>
> ```bash
> python nishizumi_setups_sync.py
> ```

## Future Ideas

Some potential improvements for later versions:

- Schedule synchronisation at specific times rather than only on startup.
- Display a progress bar for long operations.
- Send a notification when the sync completes or if an error occurs.
