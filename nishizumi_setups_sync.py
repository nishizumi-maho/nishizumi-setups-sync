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
    from tkinter import Tk, simpledialog  # pragma: no cover - optional GUI
except Exception:  # pragma: no cover - tkinter may be missing
    Tk = None
    simpledialog = None
try:
    import requests
except ModuleNotFoundError:  # pragma: no cover - handle missing dependency
    print("The 'requests' package is required. Install it with 'pip install requests'.")
    sys.exit(1)
import re
from datetime import datetime

# Store configuration files next to the script or executable. When bundled
# with PyInstaller using ``--onefile`` the temporary extraction directory would
# be removed on exit, so we resolve paths relative to the running file instead
# of the current working directory.
if getattr(sys, "frozen", False):
    _BASE_DIR = os.path.dirname(sys.executable)
else:
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_FILE = os.path.join(_BASE_DIR, "user_config.json")
MAP_FILE = os.path.join(_BASE_DIR, "custom_car_mapping.json")
VERSION = "1.0.0"
# Location of the latest script version for the self-update feature
UPDATE_URL = "https://raw.githubusercontent.com/nishizumi-maho/nishizumi-setups-sync/main/nishizumi_setups_sync.py"

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
    # extra_folders is a list of dicts: {"name": str, "location": "car"|"dest"}
    # older configs may store just a list of folder names
    "extra_folders": [],
    "backup_enabled": False,
    "backup_folder": "",
    "enable_logging": False,
    "log_file": "nishizumi_setups_sync.log",
    "copy_all": False,
    "use_driver_folders": False,
    "drivers": [],
    "use_garage61": False,
    "garage61_team_id": "",
    "garage61_api_key": "",
    "profiles": [],
    "current_profile": 1,
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
                    # normalise extra_folders format
                    ext = data.get("extra_folders")
                    if isinstance(ext, list):
                        new_ext = []
                        for item in ext:
                            if isinstance(item, str):
                                new_ext.append({"name": item, "location": "car"})
                            elif isinstance(item, dict):
                                name = item.get("name") or item.get("folder")
                                loc = item.get("location", "car")
                                if name:
                                    new_ext.append({"name": name, "location": loc})
                        data["extra_folders"] = new_ext
                    cfg.update(data)
        except Exception:
            pass

    if "profiles" not in cfg or not isinstance(cfg.get("profiles"), list):
        prof = {
            k: cfg.get(k)
            for k in [
                "source_type",
                "zip_file",
                "source_folder",
                "team_folder",
                "personal_folder",
                "driver_folder",
                "season_folder",
            ]
        }
        cfg["profiles"] = [prof]
        cfg["current_profile"] = 1

    idx = cfg.get("current_profile", 1) - 1
    if idx < 0 or idx >= len(cfg["profiles"]):
        idx = 0
        cfg["current_profile"] = 1
    prof = cfg["profiles"][idx]
    for key in [
        "source_type",
        "zip_file",
        "source_folder",
        "team_folder",
        "personal_folder",
        "driver_folder",
        "season_folder",
    ]:
        if key in prof:
            cfg[key] = prof.get(key, cfg.get(key))
    return cfg


def save_config(cfg):
    try:
        cfg.pop("external_folder", None)
        # ensure extra_folders are stored as list of dicts
        ext = cfg.get("extra_folders", [])
        if isinstance(ext, list):
            norm = []
            for item in ext:
                if isinstance(item, str):
                    norm.append({"name": item, "location": "car"})
                elif isinstance(item, dict):
                    name = item.get("name") or item.get("folder")
                    loc = item.get("location", "car")
                    if name:
                        norm.append({"name": name, "location": loc})
            cfg["extra_folders"] = norm

        profs = cfg.get("profiles")
        if isinstance(profs, list):
            new_profs = []
            for p in profs:
                if isinstance(p, dict):
                    new = {k: p.get(k, "") for k in [
                        "source_type",
                        "zip_file",
                        "source_folder",
                        "team_folder",
                        "personal_folder",
                        "driver_folder",
                        "season_folder",
                    ]}
                    new_profs.append(new)
            cfg["profiles"] = new_profs
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
        log_path = cfg.get("log_file") or "nishizumi_setups_sync.log"
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
            clean_name(d.get("name")) for d in data.get("drivers", []) if d.get("name")
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
    ignore_dirs=None,
):
    """Synchronise ``src`` and ``dst``.

    By default only ``.sto`` files are copied. If ``copy_all`` is ``True`` all
    files are processed. When ``delete_extras`` is ``False`` any additional
    files already in ``dst`` are preserved. ``ignore_dirs`` can be a collection
    of directory names to skip when copying and deleting.
    """
    ignore_dirs = set(ignore_dirs or [])
    if not os.path.exists(dst):
        os.makedirs(dst)

    for item in os.listdir(src):
        s = os.path.join(src, item)
        d = os.path.join(dst, item)

        if item in ignore_dirs and os.path.isdir(s):
            continue
        if os.path.isdir(s):
            sync_folders(
                s,
                d,
                algorithm,
                delete_extras,
                copy_all,
                ignore_dirs,
            )
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
            if item in ignore_dirs:
                continue
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
    driver_style=False,
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
        if not os.path.isdir(src) or not os.listdir(src):
            continue

        if drivers and driver_style:
            src_common = os.path.join(src, COMMON_FOLDER)
            src_dp = os.path.join(src, "Data packs")
            if os.path.exists(src_common):
                common = os.path.join(dest_root, COMMON_FOLDER)
                sync_folders(src_common, common, algorithm, copy_all=copy_all)
            for name in drivers:
                sdriver = os.path.join(src, DRIVERS_ROOT, name)
                target = os.path.join(dest_root, DRIVERS_ROOT, name)
                if os.path.exists(sdriver):
                    copy_missing_files(sdriver, target, copy_all)
                elif os.path.exists(src_common):
                    copy_missing_files(src_common, target, copy_all)
            if os.path.isdir(src_dp):
                common_dp = os.path.join(dest_root, COMMON_FOLDER, "Data packs")
                sync_folders(
                    src_dp,
                    common_dp,
                    algorithm,
                    delete_extras=False,
                    copy_all=copy_all,
                )
                for name in drivers:
                    dp_dst = os.path.join(
                        dest_root,
                        DRIVERS_ROOT,
                        name,
                        "Data packs",
                    )
                    sync_folders(
                        src_dp,
                        dp_dst,
                        algorithm,
                        delete_extras=False,
                        copy_all=copy_all,
                    )
            sync_folders(
                src,
                dest_root,
                algorithm,
                copy_all=copy_all,
                ignore_dirs={"Data packs", COMMON_FOLDER, DRIVERS_ROOT},
            )
            dp_root = os.path.join(dest_root, "Data packs")
            if os.path.isdir(dp_root):
                shutil.rmtree(dp_root, ignore_errors=True)
        elif drivers:
            common = os.path.join(dest_root, COMMON_FOLDER)
            sync_folders(src, common, algorithm, copy_all=copy_all)
            for name in drivers:
                dpath = os.path.join(dest_root, DRIVERS_ROOT, name)
                copy_missing_files(src, dpath, copy_all)
        else:
            sync_folders(src, dest_root, algorithm, copy_all=copy_all)


def remove_unknown_driver_folders(iracing_folder, dest_name, drivers):
    """Delete driver folders not present in ``drivers``."""
    if drivers is None:
        return
    for car in os.listdir(iracing_folder):
        car_dir = os.path.join(iracing_folder, car)
        if not os.path.isdir(car_dir):
            continue
        root = os.path.join(car_dir, dest_name, DRIVERS_ROOT)
        if not os.path.isdir(root):
            continue
        for folder in os.listdir(root):
            if folder not in drivers:
                shutil.rmtree(os.path.join(root, folder), ignore_errors=True)


def merge_external_into_source(
    iracing_folder,
    ext_names,
    src_name,
    dest_name,
    algorithm="md5",
    copy_all=False,
    drivers=None,
    driver_style=False,
):
    """Copy one or more external folders into the source folder for each car.

    ``ext_names`` may be a list of dicts with ``name`` and ``location`` keys.
    ``location`` can be ``"car"`` for a folder directly inside the car directory
    or ``"dest"`` for a folder located inside the sync destination folder.

    Each folder is merged into ``<car>/<src_name>/<name>`` without deleting
    existing files. When ``driver_style`` is ``True`` the folders are also
    copied into ``Common Setups`` and each driver directory inside ``src_name``.
    """
    if isinstance(ext_names, str):
        ext_names = [{"name": ext_names, "location": "car"}]
    elif isinstance(ext_names, list):
        fixed = []
        for item in ext_names:
            if isinstance(item, str):
                fixed.append({"name": item, "location": "car"})
            elif isinstance(item, dict):
                name = item.get("name") or item.get("folder")
                loc = item.get("location", "car")
                if name:
                    fixed.append({"name": name, "location": loc})
        ext_names = fixed
    else:
        return

    for car in os.listdir(iracing_folder):
        car_dir = os.path.join(iracing_folder, car)
        if not os.path.isdir(car_dir):
            continue
        for ext_def in ext_names:
            folder_name = ext_def.get("name")
            loc = ext_def.get("location", "car")
            if loc == "dest":
                ext = os.path.join(car_dir, dest_name, folder_name)
                remove_after = True
            else:
                ext = os.path.join(car_dir, folder_name)
                remove_after = False
            if not os.path.exists(ext):
                continue
            if driver_style and drivers is not None:
                common_dst = os.path.join(car_dir, src_name, COMMON_FOLDER, folder_name)
                sync_folders(
                    ext,
                    common_dst,
                    algorithm,
                    delete_extras=False,
                    copy_all=copy_all,
                )
                for driver_name in drivers:
                    dst = os.path.join(
                        car_dir,
                        src_name,
                        DRIVERS_ROOT,
                        driver_name,
                        folder_name,
                    )
                    sync_folders(
                        ext,
                        dst,
                        algorithm,
                        delete_extras=False,
                        copy_all=copy_all,
                    )
            else:
                dst = os.path.join(car_dir, src_name, folder_name)
                sync_folders(
                    ext,
                    dst,
                    algorithm,
                    delete_extras=False,
                    copy_all=copy_all,
                )
            if remove_after:
                shutil.rmtree(ext, ignore_errors=True)


def sync_data_pack_folders(
    iracing_folder,
    src_team,
    dest_team,
    algorithm="md5",
    copy_all=False,
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
        drivers=None,
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
            if os.path.isdir(candidate) and os.listdir(candidate):
                source_path = candidate
                break
        if not source_path:
            continue
        for car in cars:
            dest = os.path.join(iracing_folder, car, dest_name)
            sync_folders(source_path, dest, algorithm, copy_all=copy_all)


def sync_nascar_source_folders(
    iracing_folder, src_name, algorithm="md5", drivers=None, driver_style=False
):
    """Synchronise the source folder across all cars in each NASCAR group."""
    for group in ["nascar nextgen", "nascar xfinity", "nascar trucks"]:
        cars = CAR_GROUPS.get(group, [])
        paths = []
        for car in cars:
            base = os.path.join(iracing_folder, car, src_name)
            if driver_style and drivers is not None:
                common = os.path.join(base, COMMON_FOLDER)
                if os.path.exists(common):
                    paths.append(common)
                for name in drivers:
                    p = os.path.join(base, DRIVERS_ROOT, name)
                    if os.path.exists(p):
                        paths.append(p)
            else:
                if os.path.exists(base):
                    paths.append(base)
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


def _prompt_map_folder(folder):
    """Ask the user to map an unknown car folder to an iRacing directory."""
    if simpledialog and Tk:
        try:
            root = Tk()
            root.withdraw()
            ans = simpledialog.askstring(
                "Map Car Folder",
                (
                    f"Folder '{folder}' not recognised.\n"
                    "Enter the name of the target folder in iRacing setups:"
                ),
            )
            root.destroy()
            if ans:
                return ans.strip()
        except Exception:
            pass
    try:
        user = input(
            (
                f"Folder '{folder}' not recognised.\n"
                "Enter the name of the target folder in iRacing setups: "
            )
        )
        return user.strip() if user else None
    except Exception:
        return None


def copy_from_source(source, iracing_folder, cfg, ask=False):
    custom_map = load_custom_mapping()
    subfolders = [f.name for f in os.scandir(source) if f.is_dir()]

    def _import_dir(src_path, dest):
        os.makedirs(dest, exist_ok=True)
        for item in os.listdir(src_path):
            s = os.path.join(src_path, item)
            d = os.path.join(dest, item)
            if os.path.isdir(s):
                if not os.path.exists(d):
                    shutil.copytree(s, d)
                else:
                    sync_folders(
                        s,
                        d,
                        cfg["hash_algorithm"],
                        copy_all=cfg.get("copy_all", False),
                    )
            elif cfg.get("copy_all", False) or item.lower().endswith(".sto"):
                shutil.copy2(s, d)

    driver_mode = cfg.get("use_driver_folders")
    drivers = [clean_name(n) for n in cfg.get("drivers", [])] if driver_mode else []

    for folder in subfolders:
        setup_name = identify_setup(folder, custom_map)
        if not setup_name and ask:
            setup_name = _prompt_map_folder(folder)
            if setup_name:
                custom_map[folder.lower()] = setup_name
                save_custom_mapping(custom_map)
        if not setup_name:
            continue
        target = os.path.join(iracing_folder, setup_name)
        personal_base = os.path.join(target, cfg["personal_folder"])
        team_base = os.path.join(target, cfg["team_folder"])
        supplier = cfg["driver_folder"]
        season = cfg["season_folder"]
        src_path = os.path.join(source, folder)

        if driver_mode:
            # Copy to Common and each driver folder
            for base in [personal_base, team_base]:
                common = os.path.join(base, COMMON_FOLDER, supplier, season)
                _import_dir(src_path, common)
                for name in drivers:
                    dpath = os.path.join(
                        base,
                        DRIVERS_ROOT,
                        name,
                        supplier,
                        season,
                    )
                    _import_dir(src_path, dpath)
        else:
            personal = os.path.join(personal_base, supplier, season)
            team = os.path.join(team_base, supplier, season)
            _import_dir(src_path, personal)
            _import_dir(src_path, team)


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


# ---------------------- Sync Handling ----------------------


def perform_sync(ir_folder, cfg):
    """Synchronise configured folders regardless of import mode."""
    src_name = cfg.get("sync_source")
    dst_name = cfg.get("sync_destination")
    if not src_name or not dst_name:
        log("Sync source or destination not configured, skipping", cfg)
        return

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
            dst_name,
            cfg["hash_algorithm"],
            cfg.get("copy_all", False),
            drivers=(
                [clean_name(n) for n in cfg.get("drivers", [])]
                if cfg.get("use_driver_folders")
                else None
            ),
            driver_style=cfg.get("use_driver_folders", False),
        )

    drivers = (
        [clean_name(n) for n in cfg.get("drivers", [])]
        if cfg.get("use_driver_folders")
        else None
    )
    if drivers is not None:
        remove_unknown_driver_folders(ir_folder, dst_name, drivers)

    sync_nascar_source_folders(
        ir_folder,
        src_name,
        cfg["hash_algorithm"],
        drivers=drivers,
        driver_style=cfg.get("use_driver_folders", False),
    )
    sync_team_folders(
        ir_folder,
        src_name,
        dst_name,
        cfg["hash_algorithm"],
        cfg.get("copy_all", False),
        drivers,
        driver_style=cfg.get("use_driver_folders", False),
    )
    if not cfg.get("use_driver_folders"):
        sync_data_pack_folders(
            ir_folder,
            src_name,
            dst_name,
            cfg["hash_algorithm"],
            cfg.get("copy_all", False),
        )
        sync_nascar_data_packs(ir_folder, dst_name, cfg["hash_algorithm"])


# ---------------------- Silent Entry ----------------------


def run_silent(cfg, ask=False):
    """Execute configured actions without showing the UI."""
    log("Running in silent mode", cfg)
    ir_folder = cfg.get("iracing_folder")
    if not ir_folder or not os.path.exists(ir_folder):
        log("No valid iRacing folder configured. Nothing to do.", cfg)
        return

    # Backup the entire iRacing folder before making any changes
    if cfg.get("backup_enabled") and cfg.get("backup_folder"):
        backup_iracing_folder(
            ir_folder,
            cfg["backup_folder"],
            cfg.get("copy_all", False),
        )

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

    perform_sync(ir_folder, cfg)


def main():
    cfg = load_config()
    if "--silent" in sys.argv or (
        cfg.get("run_on_startup", False)
        and "--gui" not in sys.argv
        and not sys.stdin.isatty()
    ):
        run_silent(cfg, ask=False)
        return

    try:
        from PySide6 import QtWidgets
    except Exception:
        log("GUI not available, running silently", cfg)
        run_silent(cfg, ask=False)
        return

    class MainWindow(QtWidgets.QWidget):
        """PySide6 interface for configuring and running the tool."""

        def __init__(self, cfg):
            super().__init__()
            self.cfg = cfg
            self.setWindowTitle("Nishizumi Setups Sync")
            self.resize(600, 700)
            self._build_ui()

        def _add_entry(self, layout, label, text="", password=False):
            widget = QtWidgets.QWidget()
            h = QtWidgets.QHBoxLayout(widget)
            h.setContentsMargins(0, 0, 0, 0)
            h.addWidget(QtWidgets.QLabel(label))
            edit = QtWidgets.QLineEdit()
            if password:
                edit.setEchoMode(QtWidgets.QLineEdit.Password)
            edit.setText(text)
            h.addWidget(edit)
            layout.addWidget(widget)
            return edit

        def _add_browse(self, layout, label, file_mode, callback, text=""):
            widget = QtWidgets.QWidget()
            h = QtWidgets.QHBoxLayout(widget)
            h.setContentsMargins(0, 0, 0, 0)
            h.addWidget(QtWidgets.QLabel(label))
            edit = QtWidgets.QLineEdit()
            edit.setText(text)
            btn = QtWidgets.QPushButton("Browse")
            btn.clicked.connect(callback)
            h.addWidget(edit)
            h.addWidget(btn)
            layout.addWidget(widget)
            return edit

        def _build_ui(self):
            main = QtWidgets.QVBoxLayout(self)
            scroll = QtWidgets.QScrollArea()
            scroll.setWidgetResizable(True)
            main.addWidget(scroll)
            container = QtWidgets.QWidget()
            scroll.setWidget(container)
            layout = QtWidgets.QVBoxLayout(container)

            layout.addWidget(QtWidgets.QLabel("Fill the options below and press Run."))
            layout.addWidget(
                QtWidgets.QLabel(
                    "All folder names must be inside your iRacing setups folder."
                    " Missing folders will be created automatically."
                )
            )

            prof_row = QtWidgets.QHBoxLayout()
            prof_row.addWidget(QtWidgets.QLabel("Import Profile"))
            self.profile_spin = QtWidgets.QSpinBox()
            self.profile_spin.setRange(1, 100)
            self.profile_spin.setValue(self.cfg.get("current_profile", 1))
            self.profile_spin.valueChanged.connect(self.load_profile)
            prof_row.addWidget(self.profile_spin)
            layout.addLayout(prof_row)

            self.iracing_entry = self._add_browse(
                layout,
                "iRacing Setups Folder (destination root)",
                False,
                self.browse_iracing,
                self.cfg.get("iracing_folder", ""),
            )

            self.mode_combo = QtWidgets.QComboBox()
            self.mode_combo.addItems(["zip", "folder", "none"])
            self.mode_combo.setCurrentText(self.cfg.get("source_type", "zip"))
            layout.addWidget(QtWidgets.QLabel("Import Mode"))
            layout.addWidget(self.mode_combo)
            self.mode_combo.currentTextChanged.connect(self.update_mode_fields)

            self.zip_entry = self._add_browse(
                layout,
                "Zip File to Import",
                True,
                self.browse_zip,
                self.cfg.get("zip_file", ""),
            )
            self.src_entry = self._add_browse(
                layout,
                "Folder to Import",
                False,
                self.browse_src,
                self.cfg.get("source_folder", ""),
            )

            self.team_entry = self._add_entry(
                layout,
                "Team Folder Name (destination)",
                self.cfg.get("team_folder"),
            )
            self.personal_entry = self._add_entry(
                layout,
                "Personal Folder Name (source)",
                self.cfg.get("personal_folder"),
            )
            self.driver_entry = self._add_entry(
                layout,
                "Setup Supplier Folder Name (inside team folder)",
                self.cfg.get("driver_folder"),
            )
            self.season_entry = self._add_entry(
                layout,
                "Season Folder Name (inside supplier folder)",
                self.cfg.get("season_folder"),
            )

            self.backup_check = QtWidgets.QCheckBox("Enable backup")
            self.backup_check.setChecked(self.cfg.get("backup_enabled", False))
            layout.addWidget(self.backup_check)
            self.backup_entry = self._add_browse(
                layout,
                "Backup Folder",
                False,
                self.browse_backup,
                self.cfg.get("backup_folder", ""),
            )
            self.backup_entry.parent().setVisible(self.backup_check.isChecked())
            self.backup_check.toggled.connect(
                lambda v: self.backup_entry.parent().setVisible(v)
            )

            self.log_check = QtWidgets.QCheckBox("Enable logging")
            self.log_check.setChecked(self.cfg.get("enable_logging", False))
            layout.addWidget(self.log_check)
            self.log_entry = self._add_entry(
                layout, "Log File", self.cfg.get("log_file")
            )
            self.log_entry.parent().setVisible(self.log_check.isChecked())
            self.log_check.toggled.connect(
                lambda v: self.log_entry.parent().setVisible(v)
            )

            self.sync_source_entry = self._add_entry(
                layout,
                "Sync Source Folder (copy from)",
                self.cfg.get("sync_source"),
            )
            self.sync_dest_entry = self._add_entry(
                layout,
                "Sync Destination Folder (copy to)",
                self.cfg.get("sync_destination"),
            )

            self.external_check = QtWidgets.QCheckBox("Use extra sync folders")
            self.external_check.setChecked(self.cfg.get("use_external", False))
            layout.addWidget(self.external_check)
            self.extra_count_label = QtWidgets.QLabel("Number of extra folders")
            layout.addWidget(self.extra_count_label)
            self.extra_count_spin = QtWidgets.QSpinBox()
            self.extra_count_spin.setRange(0, 10)
            self.extra_count_spin.setValue(len(self.cfg.get("extra_folders", [])))
            layout.addWidget(self.extra_count_spin)
            self.extra_entries = []
            self.extra_layout = QtWidgets.QVBoxLayout()
            layout.addLayout(self.extra_layout)
            self.extra_count_spin.valueChanged.connect(self.update_extra_fields)
            self.external_check.toggled.connect(self.update_extra_option_visibility)
            self.update_extra_option_visibility()
            self.update_extra_fields()

            layout.addWidget(QtWidgets.QLabel("Hash Algorithm (file comparison)"))
            self.algo_combo = QtWidgets.QComboBox()
            self.algo_combo.addItems(["md5", "sha256"])
            self.algo_combo.setCurrentText(self.cfg.get("hash_algorithm", "md5"))
            layout.addWidget(self.algo_combo)

            self.copy_all_check = QtWidgets.QCheckBox("Copy everything (not just .sto)")
            self.copy_all_check.setChecked(self.cfg.get("copy_all", False))
            self.copy_all_check.clicked.connect(self.on_copy_toggle)
            layout.addWidget(self.copy_all_check)

            self.startup_check = QtWidgets.QCheckBox("Run silently on startup")
            self.startup_check.setChecked(self.cfg.get("run_on_startup", False))
            layout.addWidget(self.startup_check)

            drivers_group = QtWidgets.QGroupBox("Driver Folders")
            d_layout = QtWidgets.QVBoxLayout(drivers_group)
            d_layout.addWidget(
                QtWidgets.QLabel(
                    "Sync setups to a common folder and each driver folder."
                )
            )
            self.garage_check = QtWidgets.QCheckBox("Use Garage61 API for drivers")
            self.garage_check.setChecked(self.cfg.get("use_garage61", False))
            d_layout.addWidget(self.garage_check)
            self.team_id_entry = self._add_entry(
                d_layout, "Garage61 Team ID", self.cfg.get("garage61_team_id")
            )
            self.api_key_entry = self._add_entry(
                d_layout,
                "Garage61 API Key",
                self.cfg.get("garage61_api_key"),
                password=True,
            )
            self.driver_check = QtWidgets.QCheckBox("Manually write drivers names")
            self.driver_check.setChecked(self.cfg.get("use_driver_folders", False))
            d_layout.addWidget(self.driver_check)
            self.driver_check.toggled.connect(self.update_garage_fields)
            d_layout.addWidget(QtWidgets.QLabel("Number of drivers"))
            self.driver_count_spin = QtWidgets.QSpinBox()
            # Allow a very large number of drivers to be configured
            # Previously the limit was 10 which was restrictive for
            # bigger teams. Increase upper bound to accommodate up to
            # one thousand driver entries.
            self.driver_count_spin.setRange(0, 1000)
            self.driver_count_spin.setValue(len(self.cfg.get("drivers", [])))
            d_layout.addWidget(self.driver_count_spin)
            self.driver_entries = []
            self.driver_layout = QtWidgets.QVBoxLayout()
            d_layout.addLayout(self.driver_layout)
            self.driver_count_spin.valueChanged.connect(self.update_driver_fields)
            self.update_driver_fields()
            layout.addWidget(drivers_group)

            self.garage_check.toggled.connect(self.update_garage_fields)
            self.update_garage_fields()
            self.update_mode_fields()

            save_btn = QtWidgets.QPushButton("Save Config")
            save_btn.clicked.connect(self.save_only)
            layout.addWidget(save_btn)

            run_btn = QtWidgets.QPushButton("Run")
            run_btn.clicked.connect(self.save_and_run)
            layout.addWidget(run_btn)

            upd_btn = QtWidgets.QPushButton("Check for Updates")
            upd_btn.clicked.connect(update_script)
            layout.addWidget(upd_btn)

        def browse_zip(self):
            path, _ = QtWidgets.QFileDialog.getOpenFileName(
                self, "Select Zip", filter="Zip (*.zip)"
            )
            if path:
                self.zip_entry.setText(path)

        def browse_src(self):
            path = QtWidgets.QFileDialog.getExistingDirectory(self, "Select Folder")
            if path:
                self.src_entry.setText(path)

        def browse_iracing(self):
            path = QtWidgets.QFileDialog.getExistingDirectory(
                self, "Select iRacing Folder"
            )
            if path:
                self.iracing_entry.setText(path)

        def browse_backup(self):
            path = QtWidgets.QFileDialog.getExistingDirectory(
                self, "Select Backup Folder"
            )
            if path:
                self.backup_entry.setText(path)

        def load_profile(self):
            idx = self.profile_spin.value() - 1
            profiles = self.cfg.get("profiles", [])
            while len(profiles) <= idx:
                profiles.append({})
            self.cfg["profiles"] = profiles
            prof = profiles[idx]
            self.mode_combo.setCurrentText(prof.get("source_type", self.cfg.get("source_type", "zip")))
            self.zip_entry.setText(prof.get("zip_file", ""))
            self.src_entry.setText(prof.get("source_folder", ""))
            self.team_entry.setText(prof.get("team_folder", self.cfg.get("team_folder", "")))
            self.personal_entry.setText(prof.get("personal_folder", self.cfg.get("personal_folder", "")))
            self.driver_entry.setText(prof.get("driver_folder", self.cfg.get("driver_folder", "")))
            self.season_entry.setText(prof.get("season_folder", self.cfg.get("season_folder", "")))
            self.update_mode_fields()

        def update_extra_fields(self):
            count = self.extra_count_spin.value()
            while len(self.extra_entries) < count:
                idx = len(self.extra_entries) + 1
                e = QtWidgets.QLineEdit()
                cb = QtWidgets.QComboBox()
                cb.addItems(["car", "dest"])
                if idx <= len(self.cfg.get("extra_folders", [])):
                    data = self.cfg["extra_folders"][idx - 1]
                    if isinstance(data, dict):
                        e.setText(data.get("name", ""))
                        cb.setCurrentText(data.get("location", "car"))
                    else:
                        e.setText(str(data))
                lbl = QtWidgets.QLabel(f"Extra Folder {idx} Name")
                self.extra_layout.addWidget(lbl)
                self.extra_layout.addWidget(e)
                self.extra_layout.addWidget(cb)
                self.extra_entries.append((lbl, e, cb))
            while len(self.extra_entries) > count:
                lbl, e, cb = self.extra_entries.pop()
                lbl.deleteLater()
                e.deleteLater()
                cb.deleteLater()

        def update_extra_option_visibility(self):
            use_extra = self.external_check.isChecked()
            self.extra_count_label.setVisible(use_extra)
            self.extra_count_spin.setVisible(use_extra)
            for lbl, _, _ in self.extra_entries:
                lbl.setVisible(use_extra)
            for _, e, cb in self.extra_entries:
                e.setVisible(use_extra)
                cb.setVisible(use_extra)

        def update_driver_fields(self):
            count = (
                self.driver_count_spin.value()
                if self.driver_check.isChecked() and not self.garage_check.isChecked()
                else 0
            )
            while len(self.driver_entries) < count:
                idx = len(self.driver_entries) + 1
                e = QtWidgets.QLineEdit()
                if idx <= len(self.cfg.get("drivers", [])):
                    e.setText(self.cfg["drivers"][idx - 1])
                lbl = QtWidgets.QLabel(f"Driver {idx} Name")
                self.driver_layout.addWidget(lbl)
                self.driver_layout.addWidget(e)
                self.driver_entries.append((lbl, e))
            while len(self.driver_entries) > count:
                lbl, e = self.driver_entries.pop()
                lbl.deleteLater()
                e.deleteLater()

        def update_garage_fields(self):
            use_api = self.garage_check.isChecked()
            self.team_id_entry.parent().setVisible(use_api)
            self.api_key_entry.parent().setVisible(use_api)
            self.driver_check.setVisible(not use_api)
            self.driver_count_spin.setEnabled(
                self.driver_check.isChecked() and not use_api
            )
            self.update_driver_fields()

        def update_mode_fields(self):
            mode = self.mode_combo.currentText()
            self.zip_entry.parent().setVisible(mode == "zip")
            self.src_entry.parent().setVisible(mode == "folder")
            show = mode != "none"
            self.team_entry.parent().setVisible(show)
            self.personal_entry.parent().setVisible(show)
            self.driver_entry.parent().setVisible(show)
            self.season_entry.parent().setVisible(show)

        def on_copy_toggle(self):
            if self.copy_all_check.isChecked():
                reply = QtWidgets.QMessageBox.question(
                    self,
                    "Copy all files?",
                    "Online sync tools usually only care about .sto files.\n"
                    "Copying everything (.blap, .olap, .rpy, ...) may waste disk space.\n\n"
                    "I know, proceed anyway?",
                )
                if reply != QtWidgets.QMessageBox.Yes:
                    self.copy_all_check.setChecked(False)

        def collect_config(self):
            cfg = {
                "iracing_folder": self.iracing_entry.text().strip(),
                "source_type": self.mode_combo.currentText(),
                "zip_file": self.zip_entry.text().strip(),
                "source_folder": self.src_entry.text().strip(),
                "team_folder": clean_name(self.team_entry.text()),
                "personal_folder": clean_name(self.personal_entry.text()),
                "driver_folder": clean_name(self.driver_entry.text()),
                "season_folder": clean_name(self.season_entry.text()),
                "sync_source": clean_name(self.sync_source_entry.text()),
                "sync_destination": clean_name(self.sync_dest_entry.text()),
                "backup_enabled": self.backup_check.isChecked(),
                "backup_folder": self.backup_entry.text().strip(),
                "enable_logging": self.log_check.isChecked(),
                "log_file": self.log_entry.text().strip(),
                "hash_algorithm": self.algo_combo.currentText(),
                "run_on_startup": self.startup_check.isChecked(),
                "use_external": self.external_check.isChecked(),
                "extra_folders": [
                    {
                        "name": clean_name(e.text()),
                        "location": cb.currentText(),
                    }
                    for _, e, cb in self.extra_entries
                    if e.text().strip()
                ],
                "copy_all": self.copy_all_check.isChecked(),
                "use_driver_folders": self.driver_check.isChecked(),
                "drivers": [
                    clean_name(e.text())
                    for _, e in self.driver_entries
                    if e.text().strip()
                ],
                "use_garage61": self.garage_check.isChecked(),
                "garage61_team_id": self.team_id_entry.text().strip(),
                "garage61_api_key": self.api_key_entry.text().strip(),
            }

            idx = self.profile_spin.value() - 1
            cfg["current_profile"] = idx + 1
            profiles = self.cfg.get("profiles", [])
            while len(profiles) <= idx:
                profiles.append({})
            prof = {
                "source_type": cfg["source_type"],
                "zip_file": cfg["zip_file"],
                "source_folder": cfg["source_folder"],
                "team_folder": cfg["team_folder"],
                "personal_folder": cfg["personal_folder"],
                "driver_folder": cfg["driver_folder"],
                "season_folder": cfg["season_folder"],
            }
            profiles[idx] = prof
            cfg["profiles"] = profiles
            return cfg

        def save_and_run(self):
            cfg = self.collect_config()
            save_config(cfg)
            self.cfg = cfg
            run_silent(cfg, ask=True)
            QtWidgets.QMessageBox.information(self, "Done", "Processing completed")

        def save_only(self):
            cfg = self.collect_config()
            save_config(cfg)
            self.cfg = cfg
            QtWidgets.QMessageBox.information(self, "Saved", "Configuration saved")

    app = QtWidgets.QApplication(sys.argv)
    win = MainWindow(cfg)
    win.show()
    app.exec()


if __name__ == "__main__":
    if "--update" in sys.argv:
        update_script()
    else:
        main()
