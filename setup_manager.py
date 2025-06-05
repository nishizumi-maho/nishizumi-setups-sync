"""iRacing setup management script.

This script imports setup files from a ZIP archive or another folder and
synchronises them between personal and team directories. It can run silently
on startup and supports optional Garage61 integration and backup features.
"""

import os
import sys
import zipfile
import shutil
import hashlib
import json
try:
    import requests
except ModuleNotFoundError:  # pragma: no cover - handle missing dependency
    print("The 'requests' package is required. Install it with 'pip install requests'.")
    sys.exit(1)
import re
from datetime import datetime
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog
from tkinter import ttk

CONFIG_FILE = "user_config.json"
MAP_FILE = "custom_car_mapping.json"
VERSION = "1.0.0"
# Location of the latest script version for the self-update feature
UPDATE_URL = (
    "https://raw.githubusercontent.com/MahoNishizumi/"
    "nishizumi-setups-sync/main/setup_manager.py"
)

# ---------------------- Config Handling ----------------------
DEFAULT_CONFIG = {
    "iracing_folder": "",
    "source_type": "zip",  # 'zip' or 'folder'
    "zip_file": "",
    "source_folder": "",
    "team_folder": "Example Team",
    "personal_folder": "My Personal Folder",
    "driver_folder": "Example Supplier",
    "season_folder": "Example Season",
    "sync_source": "Example Source",
    "sync_destination": "Example Destination",
    "hash_algorithm": "md5",
    "run_on_startup": False,
    "use_external": False,
    "extra_folders": [],
    "backup_enabled": False,
    "backup_folder": "",
    "enable_logging": False,
    "log_file": "setup_manager.log",
    "copy_all": False,
    "use_driver_folders": False,
    "drivers": [],
    "use_garage61": False,
    "garage61_team_id": "",
    "garage61_api_key": "",
}


def load_config():
    cfg = DEFAULT_CONFIG.copy()
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    if "external_folder" in data and "extra_folders" not in data:
                        ef = data.get("external_folder")
                        data["extra_folders"] = [ef] if ef else []
                    cfg.update(data)
        except Exception:
            pass
    return cfg


def save_config(cfg):
    try:
        cfg.pop("external_folder", None)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=4)
    except Exception:
        pass


# ---------------------- Utilities ----------------------


def calc_hash(path, algorithm="md5"):
    try:
        if algorithm == "sha256":
            h = hashlib.sha256()
        else:
            h = hashlib.md5()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


def log(msg, cfg=None):
    """Print and optionally write a message to a log file."""
    print(msg)
    if cfg and cfg.get("enable_logging"):
        log_path = cfg.get("log_file") or "setup_manager.log"
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                f.write(f"[{timestamp}] {msg}\n")
        except Exception:
            pass


INVALID_CHARS = '<>:"/\\|?*'


def clean_name(name):
    """Return ``name`` stripped of whitespace and invalid path characters."""
    if not name:
        return ""
    name = name.strip()
    return "".join(c for c in name if c not in INVALID_CHARS)


def copy_entry(src, dst):
    """Copy a file or directory preserving metadata."""
    try:
        if os.path.isdir(src):
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
        log(f"[COPIED] '{src}' -> '{dst}'")
    except Exception as e:
        log(f"[ERROR] Failed to copy '{src}' -> '{dst}': {e}")


def fetch_garage61_drivers(team_id, api_key=None):
    """Return a list of driver names from Garage61 for the given team ID."""
    url = f"https://garage61.net/api/teams/{team_id}/drivers"
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        r = requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()
        data = r.json()
        names = [
            clean_name(d.get("name"))
            for d in data.get("drivers", [])
            if d.get("name")
        ]
        return [n for n in names if n]
    except Exception as e:
        log(f"Failed to fetch drivers from Garage61: {e}")
        return None


def check_for_update():
    """Return remote version and script text if a newer version exists."""
    try:
        r = requests.get(UPDATE_URL, timeout=10)
        r.raise_for_status()
        m = re.search(r"^VERSION\s*=\s*\"([^\"]+)\"", r.text, re.MULTILINE)
        if not m:
            return None, None
        remote_ver = m.group(1)
        if remote_ver != VERSION:
            return remote_ver, r.text
    except Exception as e:
        log(f"Update check failed: {e}")
    return None, None


def update_script():
    """Download and replace this script with the latest version."""
    remote_ver, remote_text = check_for_update()
    if not remote_ver:
        log("No update available")
        return False
    try:
        script_path = os.path.abspath(sys.argv[0])
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(remote_text)
        log(f"Updated to version {remote_ver}. Restart the program.")
        return True
    except Exception as e:
        log(f"Failed to write update: {e}")
        return False


def sync_bidirectional(dir_a, dir_b, algorithm="md5"):
    """Synchronise contents of ``dir_a`` and ``dir_b`` in both directions."""
    os.makedirs(dir_a, exist_ok=True)
    os.makedirs(dir_b, exist_ok=True)

    items_a = set(os.listdir(dir_a))
    items_b = set(os.listdir(dir_b))

    for item in items_a - items_b:
        copy_entry(os.path.join(dir_a, item), os.path.join(dir_b, item))

    for item in items_b - items_a:
        copy_entry(os.path.join(dir_b, item), os.path.join(dir_a, item))

    for item in items_a & items_b:
        pa = os.path.join(dir_a, item)
        pb = os.path.join(dir_b, item)
        if os.path.isdir(pa):
            sync_bidirectional(pa, pb, algorithm)
        else:
            ha = calc_hash(pa, algorithm)
            hb = calc_hash(pb, algorithm)
            if ha and hb and ha != hb:
                if os.path.getmtime(pa) >= os.path.getmtime(pb):
                    copy_entry(pa, pb)
                else:
                    copy_entry(pb, pa)


def sync_folders(
    src,
    dst,
    algorithm="md5",
    delete_extras=True,
    copy_all=False,
):
    """Synchronise ``src`` and ``dst``.

    By default only ``.sto`` files are copied. If ``copy_all`` is ``True`` all
    files are processed. When ``delete_extras`` is ``False`` any additional
    files already in ``dst`` are preserved.
    """
    if not os.path.exists(dst):
        os.makedirs(dst)

    for item in os.listdir(src):
        s = os.path.join(src, item)
        d = os.path.join(dst, item)

        if os.path.isdir(s):
            sync_folders(s, d, algorithm, delete_extras, copy_all)
            continue

        if not copy_all and not item.lower().endswith(".sto"):
            # skip other file types
            continue

        copy = False
        if not os.path.exists(d):
            copy = True
        else:
            if calc_hash(s, algorithm) != calc_hash(d, algorithm):
                copy = True
        if copy:
            shutil.copy2(s, d)

    if delete_extras:
        # Remove files in destination that are not wanted or no longer present
        for item in os.listdir(dst):
            d = os.path.join(dst, item)
            s = os.path.join(src, item)
            if os.path.isdir(d):
                if not os.path.exists(s):
                    shutil.rmtree(d)
            else:
                if not os.path.exists(s) or (
                    not copy_all and not item.lower().endswith(".sto")
                ):
                    os.remove(d)


def copy_missing_files(src, dst, copy_all=False):
    """Copy files from ``src`` to ``dst`` without overwriting existing ones."""
    os.makedirs(dst, exist_ok=True)
    for item in os.listdir(src):
        s = os.path.join(src, item)
        d = os.path.join(dst, item)
        if os.path.isdir(s):
            copy_missing_files(s, d, copy_all)
            continue
        if not copy_all and not item.lower().endswith(".sto"):
            continue
        if not os.path.exists(d):
            os.makedirs(os.path.dirname(d), exist_ok=True)
            shutil.copy2(s, d)


def backup_iracing_folder(ir_folder, backup_folder, copy_all=False):
    """Copy new files from the iRacing folder to ``backup_folder``."""
    if not ir_folder or not os.path.exists(ir_folder):
        return
    if not backup_folder:
        return
    copy_missing_files(ir_folder, backup_folder, copy_all)


COMMON_FOLDER = "Common Setups"
DRIVERS_ROOT = "Drivers"


def sync_team_folders(
    iracing_folder,
    src_name,
    dest_name,
    algorithm="md5",
    copy_all=False,
    drivers=None,
):
    """Copy source subfolder ``src_name`` to ``dest_name`` for each car.

    When ``drivers`` is provided, files are synced to ``<dest>/Common Setups``
    and new files are copied into ``<dest>/Drivers/<name>`` without overwriting
    existing ones.
    """
    for car in os.listdir(iracing_folder):
        car_dir = os.path.join(iracing_folder, car)
        if not os.path.isdir(car_dir):
            continue
        src = os.path.join(car_dir, src_name)
        dest_root = os.path.join(car_dir, dest_name)
        if not os.path.exists(src):
            continue

        if drivers:
            common = os.path.join(dest_root, COMMON_FOLDER)
            sync_folders(src, common, algorithm, copy_all=copy_all)
            for name in drivers:
                dpath = os.path.join(dest_root, DRIVERS_ROOT, name)
                copy_missing_files(src, dpath, copy_all)
        else:
            sync_folders(src, dest_root, algorithm, copy_all=copy_all)


def merge_external_into_source(
    iracing_folder,
    ext_names,
    src_name,
    algorithm="md5",
    copy_all=False,
):
    """Copy one or more external folders into the source folder for each car.

    Each folder name in ``ext_names`` will be copied from ``<car>/<name>`` to
    ``<car>/<src_name>/<name>`` without removing existing files.
    """
    if isinstance(ext_names, str):
        ext_names = [ext_names]

    for car in os.listdir(iracing_folder):
        car_dir = os.path.join(iracing_folder, car)
        if not os.path.isdir(car_dir):
            continue
        for ext_name in ext_names:
            ext = os.path.join(car_dir, ext_name)
            if not os.path.exists(ext):
                continue
            dst = os.path.join(car_dir, src_name, ext_name)
            sync_folders(ext, dst, algorithm, delete_extras=False, copy_all=copy_all)


def sync_data_pack_folders(
    iracing_folder,
    src_team,
    dest_team,
    algorithm="md5",
    copy_all=False,
    drivers=None,
):
    """Synchronise the ``Data packs`` subfolder across all cars."""
    src_dp = os.path.join(src_team, "Data packs")
    dest_dp = os.path.join(dest_team, "Data packs")
    sync_group_folders(
        iracing_folder,
        src_dp,
        dest_dp,
        algorithm,
        copy_all,
    )
    sync_team_folders(
        iracing_folder,
        src_dp,
        dest_dp,
        algorithm,
        copy_all,
        drivers,
    )


def sync_group_folders(
    iracing_folder, src_name, dest_name, algorithm="md5", copy_all=False
):
    """For grouped cars (like NASCAR), copy from whichever car has the source
    folder to all cars in the same group."""
    for cars in CAR_GROUPS.values():
        source_path = None
        for car in cars:
            candidate = os.path.join(iracing_folder, car, src_name)
            if os.path.exists(candidate):
                source_path = candidate
                break
        if not source_path:
            continue
        for car in cars:
            dest = os.path.join(iracing_folder, car, dest_name)
            sync_folders(source_path, dest, algorithm, copy_all=copy_all)


def sync_nascar_source_folders(iracing_folder, src_name, algorithm="md5"):
    """Synchronise the source folder across all cars in each NASCAR group."""
    for group in ["nascar nextgen", "nascar xfinity", "nascar trucks"]:
        cars = CAR_GROUPS.get(group, [])
        paths = []
        for car in cars:
            p = os.path.join(iracing_folder, car, src_name)
            if os.path.exists(p):
                paths.append(p)
        for i in range(len(paths)):
            for j in range(i + 1, len(paths)):
                sync_bidirectional(paths[i], paths[j], algorithm)


def sync_nascar_data_packs(iracing_folder, dest_name, algorithm="md5"):
    """Keep NASCAR Data packs synced across all cars and team folders."""
    groups = [
        CAR_GROUPS.get("nascar nextgen", []),
        CAR_GROUPS.get("nascar xfinity", []),
        CAR_GROUPS.get("nascar trucks", []),
    ]

    for cars in groups:
        paths = []
        for car in cars:
            g61 = os.path.join(iracing_folder, car, "Garage 61", "Data packs")
            dest = os.path.join(iracing_folder, car, dest_name, "Data packs")
            if os.path.exists(g61):
                paths.append(g61)
            if os.path.exists(dest):
                paths.append(dest)

        for i in range(len(paths)):
            for j in range(i + 1, len(paths)):
                sync_bidirectional(paths[i], paths[j], algorithm)


# ---------------------- Setup Processing ----------------------


def load_custom_mapping():
    if os.path.exists(MAP_FILE):
        try:
            with open(MAP_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_custom_mapping(mapping):
    try:
        with open(MAP_FILE, "w", encoding="utf-8") as f:
            json.dump(mapping, f, ensure_ascii=False, indent=4)
    except Exception:
        pass


CAR_MAP = {
    "ir18 -> dallarair18": "dallarair18",
    "aston gt4 -> amvantagegt4": "amvantagegt4",
    "bmw gt4 evo -> bmwm4evogt4": "bmwm4evogt4",
    "mclaren gt4 -> mclaren570sgt4": "mclaren570sgt4",
    "bmw gt3 -> bmwm4gt3": "bmwm4gt3",
    "mclaren gt3 -> mclaren720sgt3": "mclaren720sgt3",
    "mclaren gtd -> mclaren720sgt3": "mclaren720sgt3",
    "acura gtp -> acuraarx06gtp": "acuraarx06gtp",
    "audi gtd -> audir8lmsevo2gt3": "audir8lmsevo2gt3",
    "audi gt3 -> audir8lmsevo2gt3": "audir8lmsevo2gt3",
    "bmw gtd -> bmwm4gt3": "bmwm4gt3",
    "bmw gtp -> bmwlmdh": "bmwlmdh",
    "cadillac gtp -> cadillacvseriesrgtp": "cadillacvseriesrgtp",
    "corvette gtd -> chevyvettez06rgt3": "chevyvettez06rgt3",
    "corvette gt3 -> chevyvettez06rgt3": "chevyvettez06rgt3",
    "dallara lmp2 -> dallarap217": "dallarap217",
    "ferrari 499p -> ferrari499p": "ferrari499p",
    "ferrari gtd -> ferrari296gt3": "ferrari296gt3",
    "ferrari gt3 -> ferrari296gt3": "ferrari296gt3",
    "lamborghini gtd -> lamborghinievogt3": "lamborghinievogt3",
    "lamborghini gt3 -> lamborghinievogt3": "lamborghinievogt3",
    "mercedes gtd -> mercedesamgevogt3": "mercedesamgevogt3",
    "mercedes gt3 -> mercedesamgevogt3": "mercedesamgevogt3",
    "mustang gtd -> fordmustanggt3": "fordmustanggt3",
    "mustang gt3 -> fordmustanggt3": "fordmustanggt3",
    "porsche gtd -> porsche992rgt3": "porsche992rgt3",
    "porsche gt3 -> porsche992rgt3": "porsche992rgt3",
    "porsche gtp -> porsche963gtp": "porsche963gtp",
    "fia f4 -> formulair04": "formulair04",
    "porsche gt4 -> porsche718gt4": "porsche718gt4",
    "mercedes gt4 -> mercedesamggt4": "mercedesamggt4",
    "lmp3 -> ligierjsp320": "ligierjsp320",
    "sfl -> superformulalights324": "superformulalights324",
    "pcup -> porsche992cup": "porsche992cup",
    "porsche gte -> porsche991rsr": "porsche991rsr",
    "corvette gte -> c8rvettegte": "c8rvettegte",
    "nsx gt3 -> acuransxevo22gt3": "acuransxevo22gt3",
    "nsx gtd -> acuransxevo22gt3": "acuransxevo22gt3",
    "nascar trucks": [
        "nascar trucks -> trucks toyotatundra2022",
        "nascar trucks -> trucks fordf150",
        "nascar trucks -> trucks silverado2019",
    ],
    "nascar xfinity": [
        "nascar xfinity -> stockcars2 supra2019",
        "nascar xfinity -> stockcars2 mustang2019",
        "nascar xfinity -> stockcars2 camaro2019",
    ],
    "nascar nextgen": [
        "nascar nextgen -> stockcars chevycamarozl12022",
        "nascar nextgen -> stockcars fordmustang2022",
        "nascar nextgen -> stockcars toyotacamry2022",
    ],
}

# Grouped folders used for cross-car sync
CAR_GROUPS = {
    "nascar trucks": [
        "trucks toyotatundra2022",
        "trucks fordf150",
        "trucks silverado2019",
    ],
    "nascar xfinity": [
        "stockcars2 supra2019",
        "stockcars2 mustang2019",
        "stockcars2 camaro2019",
    ],
    "nascar nextgen": [
        "stockcars chevycamarozl12022",
        "stockcars fordmustang2022",
        "stockcars toyotacamry2022",
    ],
}


def identify_setup(car_folder, custom_map):
    parts = car_folder.split("-")
    name = parts[1].strip().lower() if len(parts) >= 2 else car_folder.lower()
    if name in custom_map:
        return custom_map[name]
    for key, dest in CAR_MAP.items():
        if isinstance(dest, list):
            for m in dest:
                clean = m.split("->")[-1].strip().lower()
                if key in name or clean in name:
                    return clean
        else:
            s = key.split("->")[0].strip().lower()
            d = str(dest).split("->")[-1].strip().lower()
            if s in name or d in name:
                return d
    return None


def copy_from_source(source, iracing_folder, cfg, ask=False):
    custom_map = load_custom_mapping()
    subfolders = [f.name for f in os.scandir(source) if f.is_dir()]
    for folder in subfolders:
        setup_name = identify_setup(folder, custom_map)
        if not setup_name and ask:
            setup_name = simpledialog.askstring(
                "Map Car Folder",
                f"Folder '{folder}' not recognised.\n"
                "Enter the name of the target folder in iRacing setups:",
            )
            if setup_name:
                custom_map[folder.lower()] = setup_name.strip()
                save_custom_mapping(custom_map)
        if not setup_name:
            continue
        target = os.path.join(iracing_folder, setup_name)
        personal = os.path.join(target, cfg["personal_folder"])
        team = os.path.join(
            target,
            cfg["team_folder"],
            cfg["driver_folder"],
            cfg["season_folder"],
        )
        for p in [personal, team]:
            os.makedirs(p, exist_ok=True)
        src_path = os.path.join(source, folder)
        for item in os.listdir(src_path):
            s = os.path.join(src_path, item)
            dp = os.path.join(personal, item)
            if os.path.isdir(s):
                if not os.path.exists(dp):
                    shutil.copytree(s, dp)
                else:
                    sync_folders(
                        s,
                        dp,
                        cfg["hash_algorithm"],
                        copy_all=cfg.get("copy_all", False),
                    )
            elif cfg.get("copy_all", False) or item.lower().endswith(".sto"):
                shutil.copy2(s, dp)
        for item in os.listdir(src_path):
            s = os.path.join(src_path, item)
            dp = os.path.join(team, item)
            if os.path.isdir(s):
                if not os.path.exists(dp):
                    shutil.copytree(s, dp)
                else:
                    sync_folders(
                        s,
                        dp,
                        cfg["hash_algorithm"],
                        copy_all=cfg.get("copy_all", False),
                    )
            elif cfg.get("copy_all", False) or item.lower().endswith(".sto"):
                shutil.copy2(s, dp)


def process_zip(zip_file, cfg, ask=False):
    extract_path = os.path.join(
        os.path.dirname(zip_file), os.path.splitext(os.path.basename(zip_file))[0]
    )
    try:
        with zipfile.ZipFile(zip_file, "r") as z:
            z.extractall(extract_path)
    except Exception as e:
        log(f"Failed to extract {zip_file}: {e}", cfg)
        return
    copy_from_source(extract_path, cfg["iracing_folder"], cfg, ask=ask)
    shutil.rmtree(extract_path, ignore_errors=True)


# ---------------------- Silent Entry ----------------------


def run_silent(cfg, ask=False):
    """Execute configured actions without showing the UI."""
    log("Running in silent mode", cfg)
    ir_folder = cfg.get("iracing_folder")
    if not ir_folder or not os.path.exists(ir_folder):
        log("No valid iRacing folder configured. Nothing to do.", cfg)
        return

    if cfg["source_type"] == "zip":
        if os.path.exists(cfg.get("zip_file", "")):
            process_zip(cfg["zip_file"], cfg, ask=ask)
        else:
            log("Zip file not found, skipping import", cfg)
    elif cfg["source_type"] == "folder":
        if os.path.exists(cfg.get("source_folder", "")):
            copy_from_source(cfg["source_folder"], ir_folder, cfg, ask=ask)
        else:
            log("Source folder not found, skipping import", cfg)
    else:
        log("No import selected", cfg)

    src_name = cfg.get("sync_source")
    dst_name = cfg.get("sync_destination")
    if src_name and dst_name:
        if cfg.get("use_garage61") and cfg.get("garage61_team_id"):
            names = fetch_garage61_drivers(
                cfg.get("garage61_team_id"), cfg.get("garage61_api_key")
            )
            if names is not None:
                cfg["drivers"] = [clean_name(n) for n in names if n]
                save_config(cfg)
        if cfg.get("use_external") and cfg.get("extra_folders"):
            merge_external_into_source(
                ir_folder,
                cfg["extra_folders"],
                src_name,
                cfg["hash_algorithm"],
                cfg.get("copy_all", False),
            )
        drivers = (
            [clean_name(n) for n in cfg.get("drivers", [])]
            if cfg.get("use_driver_folders")
            else None
        )
        sync_nascar_source_folders(ir_folder, src_name, cfg["hash_algorithm"])
        sync_team_folders(
            ir_folder,
            src_name,
            dst_name,
            cfg["hash_algorithm"],
            cfg.get("copy_all", False),
            drivers,
        )
        sync_data_pack_folders(
            ir_folder,
            src_name,
            dst_name,
            cfg["hash_algorithm"],
            cfg.get("copy_all", False),
            drivers,
        )
    sync_nascar_data_packs(ir_folder, dst_name, cfg["hash_algorithm"])

    if cfg.get("backup_enabled") and cfg.get("backup_folder"):
        backup_iracing_folder(
            ir_folder,
            cfg["backup_folder"],
            cfg.get("copy_all", False),
        )


# ---------------------- GUI ----------------------


def browse_zip():
    path = filedialog.askopenfilename(filetypes=[("Zip", "*.zip")])
    if path:
        zip_entry.delete(0, tk.END)
        zip_entry.insert(0, path)


def browse_src():
    path = filedialog.askdirectory()
    if path:
        src_entry.delete(0, tk.END)
        src_entry.insert(0, path)


def browse_iracing():
    path = filedialog.askdirectory()
    if path:
        iracing_entry.delete(0, tk.END)
        iracing_entry.insert(0, path)


def browse_backup():
    path = filedialog.askdirectory()
    if path:
        backup_entry.delete(0, tk.END)
        backup_entry.insert(0, path)


def save_and_run():
    cfg = {
        "iracing_folder": iracing_entry.get().strip(),
        "source_type": mode_var.get(),
        "zip_file": zip_entry.get().strip(),
        "source_folder": src_entry.get().strip(),
        "team_folder": clean_name(team_entry.get()),
        "personal_folder": clean_name(personal_entry.get()),
        "driver_folder": clean_name(driver_entry.get()),
        "season_folder": clean_name(season_entry.get()),
        "sync_source": clean_name(sync_source_entry.get()),
        "sync_destination": clean_name(sync_dest_entry.get()),
        "backup_enabled": backup_var.get() == 1,
        "backup_folder": backup_entry.get().strip(),
        "enable_logging": log_var.get() == 1,
        "log_file": log_entry.get().strip(),
        "hash_algorithm": algo_var.get(),
        "run_on_startup": startup_var.get() == 1,
        "use_external": external_var.get() == 1,
        "extra_folders": [clean_name(e.get()) for _, e in external_entries if e.get().strip()],
        "copy_all": copy_all_var.get() == 1,
        "use_driver_folders": driver_var.get() == 1,
        "drivers": [clean_name(e.get()) for _, e in driver_entries if e.get().strip()],
        "use_garage61": garage_var.get() == 1,
        "garage61_team_id": team_id_entry.get().strip(),
        "garage61_api_key": api_key_entry.get().strip(),
    }
    save_config(cfg)
    run_silent(cfg, ask=True)
    messagebox.showinfo("Done", "Processing completed")


# ---------------------- Main ----------------------


def main():
    cfg = load_config()
    if "--silent" in sys.argv or (
        cfg.get("run_on_startup", False) and "--gui" not in sys.argv
    ):
        run_silent(cfg, ask=False)
        return

    global iracing_entry, zip_entry, src_entry
    global team_entry, personal_entry, driver_entry, season_entry
    global sync_source_entry, sync_dest_entry
    global external_entries, external_var, extra_count_var
    global driver_entries, driver_var, driver_count_var
    global algo_var, mode_var, startup_var, copy_all_var
    global garage_var, team_id_entry, api_key_entry
    global backup_entry, backup_var
    global log_entry, log_var
    global anchor_widget

    try:
        root = tk.Tk()
        ttk.Style(root).theme_use("clam")
    except tk.TclError:
        log("GUI not available, running silently", cfg)
        run_silent(cfg, ask=False)
        return

    root.title("Setup Manager")
    root.geometry("600x700")
    root.resizable(False, False)

    canvas = tk.Canvas(root)
    scroll = ttk.Scrollbar(root, orient="vertical", command=canvas.yview)
    scrollable = ttk.Frame(canvas)
    canvas_id = canvas.create_window((0, 0), window=scrollable, anchor="nw")

    def on_frame_configure(event):
        canvas.configure(scrollregion=canvas.bbox("all"))

    scrollable.bind("<Configure>", on_frame_configure)

    MAX_WIDTH = 560

    def on_canvas_configure(event):
        width = min(MAX_WIDTH, event.width)
        canvas.itemconfigure(canvas_id, width=width)
        canvas.coords(canvas_id, (event.width - width) / 2, 0)

    canvas.bind("<Configure>", on_canvas_configure)

    canvas.configure(yscrollcommand=scroll.set)
    canvas.pack(side="left", fill="both", expand=True)
    scroll.pack(side="right", fill="y")

    ttk.Label(
        scrollable,
        text="Fill the options below and press Run.",
    ).pack(pady=(0, 10))

    mode_var = tk.StringVar(value=cfg.get("source_type", "zip"))
    algo_var = tk.StringVar(value=cfg.get("hash_algorithm", "md5"))
    startup_var = tk.IntVar(value=1 if cfg.get("run_on_startup", False) else 0)
    copy_all_var = tk.IntVar(value=1 if cfg.get("copy_all", False) else 0)
    driver_var = tk.IntVar(value=1 if cfg.get("use_driver_folders", False) else 0)
    garage_var = tk.IntVar(value=1 if cfg.get("use_garage61", False) else 0)
    backup_var = tk.IntVar(value=1 if cfg.get("backup_enabled", False) else 0)
    anchor_widget = None

    def update_mode_fields(*args):
        mode = mode_var.get()
        if mode == "zip":
            zip_frame.pack(before=anchor_widget)
            src_frame.pack_forget()
            for f in import_frames:
                f.pack(before=anchor_widget)
        elif mode == "folder":
            src_frame.pack(before=anchor_widget)
            zip_frame.pack_forget()
            for f in import_frames:
                f.pack(before=anchor_widget)
        else:
            zip_frame.pack_forget()
            src_frame.pack_forget()
            for f in import_frames:
                f.pack_forget()

    zip_frame = ttk.Frame(scrollable)
    ttk.Label(zip_frame, text="Zip File to Import").pack()
    zip_entry = ttk.Entry(zip_frame, width=60)
    zip_entry.insert(0, cfg.get("zip_file", ""))
    zip_entry.pack()
    ttk.Button(zip_frame, text="Browse", command=browse_zip).pack()

    src_frame = ttk.Frame(scrollable)
    ttk.Label(src_frame, text="Folder to Import").pack()
    src_entry = ttk.Entry(src_frame, width=60)
    src_entry.insert(0, cfg.get("source_folder", ""))
    src_entry.pack()
    ttk.Button(src_frame, text="Browse", command=browse_src).pack()

    team_frame = ttk.Frame(scrollable)
    ttk.Label(team_frame, text="Team Folder Name (destination)").pack()
    team_entry = ttk.Entry(team_frame, width=40)
    team_entry.insert(0, cfg.get("team_folder", ""))
    team_entry.pack()

    personal_frame = ttk.Frame(scrollable)
    ttk.Label(personal_frame, text="Personal Folder Name (source)").pack()
    personal_entry = ttk.Entry(personal_frame, width=40)
    personal_entry.insert(0, cfg.get("personal_folder", ""))
    personal_entry.pack()

    driver_frame = ttk.Frame(scrollable)
    ttk.Label(
        driver_frame,
        text="Setup Supplier Name (inside team folder)",
    ).pack()
    driver_entry = ttk.Entry(driver_frame, width=40)
    driver_entry.insert(0, cfg.get("driver_folder", ""))
    driver_entry.pack()

    season_frame = ttk.Frame(scrollable)
    ttk.Label(season_frame, text="Season Folder (inside driver folder)").pack()
    season_entry = ttk.Entry(season_frame, width=40)
    season_entry.insert(0, cfg.get("season_folder", ""))
    season_entry.pack()

    import_frames = [team_frame, personal_frame, driver_frame, season_frame]

    ttk.Label(
        scrollable,
        text="iRacing Setups Folder (destination root)",
    ).pack()
    iracing_entry = ttk.Entry(scrollable, width=60)
    iracing_entry.insert(0, cfg.get("iracing_folder", ""))
    iracing_entry.pack()
    ttk.Button(scrollable, text="Browse", command=browse_iracing).pack()

    backup_var_chk = ttk.Checkbutton(
        scrollable,
        text="Enable backup",
        variable=backup_var,
        command=lambda: toggle_backup_fields(),
    )
    backup_var_chk.pack()

    backup_frame = ttk.Frame(scrollable)
    ttk.Label(backup_frame, text="Backup Folder").pack()
    backup_entry = ttk.Entry(backup_frame, width=60)
    backup_entry.insert(0, cfg.get("backup_folder", ""))
    backup_entry.pack()
    ttk.Button(backup_frame, text="Browse", command=browse_backup).pack()

    log_var = tk.IntVar(value=1 if cfg.get("enable_logging", False) else 0)
    log_var_chk = ttk.Checkbutton(
        scrollable,
        text="Enable logging",
        variable=log_var,
        command=lambda: toggle_log_fields(),
    )
    log_var_chk.pack()

    log_frame = ttk.Frame(scrollable)
    ttk.Label(log_frame, text="Log File").pack()
    log_entry = ttk.Entry(log_frame, width=60)
    log_entry.insert(0, cfg.get("log_file", ""))
    log_entry.pack()

    ttk.Label(scrollable, text="Sync Source Folder (copy from)").pack()
    sync_source_entry = ttk.Entry(scrollable, width=40)
    sync_source_entry.insert(0, cfg.get("sync_source", ""))
    sync_source_entry.pack()

    ttk.Label(scrollable, text="Sync Destination Folder (copy to)").pack()
    sync_dest_entry = ttk.Entry(scrollable, width=40)
    sync_dest_entry.insert(0, cfg.get("sync_destination", ""))
    sync_dest_entry.pack()

    external_var = tk.IntVar(value=1 if cfg.get("use_external", False) else 0)
    ttk.Checkbutton(
        scrollable, text="Use extra sync folders", variable=external_var
    ).pack()

    extra_frame = ttk.Frame(scrollable)
    extra_frame.pack()

    ttk.Label(extra_frame, text="Number of extra folders").pack()
    extra_count_var = tk.IntVar(value=len(cfg.get("extra_folders", [])))
    external_entries = []

    def update_extra_fields(*args):
        count = extra_count_var.get()
        while len(external_entries) < count:
            idx = len(external_entries) + 1
            lbl = ttk.Label(extra_frame, text=f"Extra Folder {idx} Name")
            entry = ttk.Entry(extra_frame, width=40)
            if idx <= len(cfg.get("extra_folders", [])):
                entry.insert(0, cfg["extra_folders"][idx - 1])
            lbl.pack()
            entry.pack()
            external_entries.append((lbl, entry))
        while len(external_entries) > count:
            lbl, entry = external_entries.pop()
            lbl.destroy()
            entry.destroy()

    spin = ttk.Spinbox(
        extra_frame, from_=0, to=10, textvariable=extra_count_var, width=5
    )
    spin.config(command=update_extra_fields)
    spin.pack()
    extra_count_var.trace_add("write", lambda *a: update_extra_fields())
    update_extra_fields()

    ttk.Label(scrollable, text="Hash Algorithm (file comparison)").pack()
    ttk.OptionMenu(scrollable, algo_var, "md5", "sha256").pack()

    def on_copy_toggle():
        if copy_all_var.get():
            proceed = messagebox.askyesno(
                "Copy all files?",
                "Online sync tools usually only care about .sto files.\n"
                "Copying everything (.blap, .olap, .rpy, ...) may waste disk space.\n\n"
                "I know, proceed anyway?",
            )
            if not proceed:
                copy_all_var.set(0)

    ttk.Checkbutton(
        scrollable,
        text="Copy everything (not just .sto)",
        variable=copy_all_var,
        command=on_copy_toggle,
    ).pack()

    startup_chk = ttk.Checkbutton(
        scrollable, text="Run silently on startup", variable=startup_var
    )
    startup_chk.pack()

    drivers_frame = ttk.LabelFrame(scrollable, text="Driver Folders")
    ttk.Label(
        drivers_frame,
        text="Sync setups to a common folder and each driver folder.",
    ).pack()

    garage_chk = ttk.Checkbutton(
        drivers_frame,
        text="Use Garage61 API for drivers",
        variable=garage_var,
        command=lambda: toggle_garage_api(),
    )
    garage_chk.pack(anchor="w")

    team_id_frame = ttk.Frame(drivers_frame)
    ttk.Label(team_id_frame, text="Garage61 Team ID").pack()
    team_id_entry = ttk.Entry(team_id_frame, width=40)
    team_id_entry.insert(0, cfg.get("garage61_team_id", ""))
    team_id_entry.pack()

    api_key_frame = ttk.Frame(drivers_frame)
    ttk.Label(api_key_frame, text="Garage61 API Key").pack()
    api_key_entry = ttk.Entry(api_key_frame, width=40, show="*")
    api_key_entry.insert(0, cfg.get("garage61_api_key", ""))
    api_key_entry.pack()

    driver_chk = ttk.Checkbutton(
        drivers_frame,
        text="Manually write drivers names",
        variable=driver_var,
        command=lambda: toggle_driver_fields(),
    )
    driver_chk.pack(anchor="w")

    driver_frame = ttk.Frame(drivers_frame)
    ttk.Label(driver_frame, text="Number of drivers").pack()
    driver_count_var = tk.IntVar(value=len(cfg.get("drivers", [])))
    driver_entries = []

    def update_driver_fields(*args):
        count = driver_count_var.get()
        while len(driver_entries) < count:
            idx = len(driver_entries) + 1
            lbl = ttk.Label(driver_frame, text=f"Driver {idx} Name")
            entry = ttk.Entry(driver_frame, width=40)
            if idx <= len(cfg.get("drivers", [])):
                entry.insert(0, cfg["drivers"][idx - 1])
            lbl.pack()
            entry.pack()
            driver_entries.append((lbl, entry))
        while len(driver_entries) > count:
            lbl, entry = driver_entries.pop()
            lbl.destroy()
            entry.destroy()

    driver_spin = ttk.Spinbox(
        driver_frame, from_=0, to=10, textvariable=driver_count_var, width=5
    )
    driver_spin.config(command=update_driver_fields)
    driver_spin.pack()
    driver_count_var.trace_add("write", lambda *a: update_driver_fields())
    update_driver_fields()

    drivers_frame.pack(pady=5)

    ttk.Label(scrollable, text="Import Mode").pack()
    ttk.Radiobutton(
        scrollable,
        text="Zip Import",
        variable=mode_var,
        value="zip",
        command=update_mode_fields,
    ).pack(anchor="w")
    ttk.Radiobutton(
        scrollable,
        text="Folder Import",
        variable=mode_var,
        value="folder",
        command=update_mode_fields,
    ).pack(anchor="w")
    ttk.Radiobutton(
        scrollable,
        text="No Import",
        variable=mode_var,
        value="none",
        command=update_mode_fields,
    ).pack(anchor="w")

    run_btn = ttk.Button(scrollable, text="Run", command=save_and_run)
    run_btn.pack(pady=10)

    ttk.Button(scrollable, text="Check for Updates", command=update_script).pack(
        pady=(0, 10)
    )

    def toggle_backup_fields():
        if backup_var.get():
            backup_frame.pack(after=backup_var_chk)
        else:
            backup_frame.pack_forget()

    def toggle_log_fields():
        if log_var.get():
            log_frame.pack(after=log_var_chk)
        else:
            log_frame.pack_forget()

    def toggle_driver_fields():
        if driver_var.get() and not garage_var.get():
            driver_frame.pack()
        else:
            driver_frame.pack_forget()

    def toggle_garage_api():
        if garage_var.get():
            team_id_frame.pack()
            api_key_frame.pack()
            driver_chk.pack_forget()
            driver_frame.pack_forget()
        else:
            team_id_frame.pack_forget()
            api_key_frame.pack_forget()
            driver_chk.pack(anchor="w")
            toggle_driver_fields()

    toggle_backup_fields()

    toggle_driver_fields()
    toggle_garage_api()
    toggle_log_fields()

    anchor_widget = run_btn
    update_mode_fields()

    root.update_idletasks()
    root.mainloop()


if __name__ == "__main__":
    if "--update" in sys.argv:
        update_script()
    else:
        main()
