"""Car folder naming: aliases, groups and the folder → car resolver.

Setup suppliers name their folders freely (``"03 - Ferrari GT3"``), while
iRacing expects a fixed directory per car (``ferrari296gt3``).  This module maps
one onto the other, with a user editable override file.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Iterable

from .paths import mapping_file

#: Characters Windows refuses in a path component.
INVALID_CHARS = '<>:"/\\|?*'
_CONTROL_CHARS = "".join(chr(c) for c in range(32))
_WHITESPACE_RE = re.compile(r"\s+")

#: Reserved device names on Windows; a folder may not be called any of these.
_RESERVED_NAMES = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


def clean_name(name: str | None) -> str:
    """Return ``name`` reduced to something usable as a folder component."""
    if not name:
        return ""
    # Collapse whitespace first so a tab between words becomes a space rather
    # than disappearing with the other control characters.
    text = _WHITESPACE_RE.sub(" ", str(name))
    text = "".join(c for c in text if c not in INVALID_CHARS and c not in _CONTROL_CHARS)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    text = text.rstrip(". ")
    if text.lower() in _RESERVED_NAMES:
        text = f"{text}_"
    return text


def normalise(name: str | None) -> str:
    """Lower-cased, whitespace-collapsed form used for matching."""
    if not name:
        return ""
    return _WHITESPACE_RE.sub(" ", str(name).replace("_", " ").strip().lower())


# ---------------------------------------------------------------------------
# Static data
# ---------------------------------------------------------------------------

#: Supplier folder wording → iRacing car directory.
CAR_ALIASES: dict[str, str] = {
    "ir18": "dallarair18",
    "aston gt4": "amvantagegt4",
    "bmw gt4 evo": "bmwm4evogt4",
    "mclaren gt4": "mclaren570sgt4",
    "bmw gt3": "bmwm4gt3",
    "bmw gtd": "bmwm4gt3",
    "mclaren gt3": "mclaren720sgt3",
    "mclaren gtd": "mclaren720sgt3",
    "acura gtp": "acuraarx06gtp",
    "audi gt3": "audir8lmsevo2gt3",
    "audi gtd": "audir8lmsevo2gt3",
    "bmw gtp": "bmwlmdh",
    "cadillac gtp": "cadillacvseriesrgtp",
    "corvette gt3": "chevyvettez06rgt3",
    "corvette gtd": "chevyvettez06rgt3",
    "dallara lmp2": "dallarap217",
    "ferrari 499p": "ferrari499p",
    "ferrari gt3": "ferrari296gt3",
    "ferrari gtd": "ferrari296gt3",
    "lamborghini gt3": "lamborghinievogt3",
    "lamborghini gtd": "lamborghinievogt3",
    "mercedes gt3": "mercedesamgevogt3",
    "mercedes gtd": "mercedesamgevogt3",
    "mustang gt3": "fordmustanggt3",
    "mustang gtd": "fordmustanggt3",
    "porsche gt3": "porsche992rgt3",
    "porsche gtd": "porsche992rgt3",
    "porsche gtp": "porsche963gtp",
    "fia f4": "formulair04",
    "porsche gt4": "porsche718gt4",
    "mercedes gt4": "mercedesamggt4",
    "lmp3": "ligierjsp320",
    "sfl": "superformulalights324",
    "pcup": "porsche992cup",
    "porsche gte": "porsche991rsr",
    "corvette gte": "c8rvettegte",
    "nsx gt3": "acuransxevo22gt3",
    "nsx gtd": "acuransxevo22gt3",
}

#: Cars that share setups and are kept in sync with each other.
CAR_GROUPS: dict[str, list[str]] = {
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
    "superformula sf23": [
        "superformulasf23 honda",
        "superformulasf23 toyota",
    ],
}

#: Extra wording that should resolve to a group (e.g. "nascar cup" → nextgen).
GROUP_ALIASES: dict[str, str] = {
    "nascar cup": "nascar nextgen",
    "cup": "nascar nextgen",
    "nextgen": "nascar nextgen",
    "next gen": "nascar nextgen",
    "xfinity": "nascar xfinity",
    "trucks": "nascar trucks",
    "truck": "nascar trucks",
    "sf23": "superformula sf23",
}


def _match_table() -> list[tuple[str, list[str]]]:
    """All match keys ordered from most to least specific."""
    table: dict[str, list[str]] = {}
    for group, cars in CAR_GROUPS.items():
        table[normalise(group)] = list(cars)
        for car in cars:
            table.setdefault(normalise(car), [car])
    for alias, group in GROUP_ALIASES.items():
        table.setdefault(normalise(alias), list(CAR_GROUPS[group]))
    for alias, target in CAR_ALIASES.items():
        table.setdefault(normalise(alias), [target])
        table.setdefault(normalise(target), [target])
    # Longest keys first so "bmw gt4 evo" wins over "bmw gt4".
    return sorted(table.items(), key=lambda item: (-len(item[0]), item[0]))


_MATCH_TABLE = _match_table()


def _candidate_names(folder: str) -> list[str]:
    """Fragments of a supplier folder name worth matching against.

    ``"03 - Ferrari GT3"`` yields ``["03 - ferrari gt3", "ferrari gt3", "03"]``
    so a leading index does not defeat the lookup.
    """
    full = normalise(folder)
    if not full:
        return []
    candidates = [full]
    for part in re.split(r"\s*[-–—_|]\s*", full):
        part = part.strip()
        if part and part not in candidates:
            candidates.append(part)
    return candidates


def identify_setup(folder: str, custom_map: dict[str, str] | None = None) -> list[str]:
    """Resolve a supplier folder name to one or more iRacing car folders.

    Returns an empty list when the folder cannot be identified.  Group folders
    (NASCAR, Super Formula) resolve to every car in the group so the import
    reaches all variants instead of relying on a later propagation pass.
    """
    custom_map = custom_map or {}
    candidates = _candidate_names(folder)
    if not candidates:
        return []

    normalised_custom = {normalise(k): v for k, v in custom_map.items() if v}
    for candidate in candidates:
        target = normalised_custom.get(candidate)
        if target:
            return expand_target(target)

    # Exact matches beat substring matches, whatever their length.
    lookup = dict(_MATCH_TABLE)
    for candidate in candidates:
        if candidate in lookup:
            return list(lookup[candidate])

    for key, targets in _MATCH_TABLE:
        for candidate in candidates:
            if key in candidate:
                return list(targets)
    return []


def expand_target(target: str) -> list[str]:
    """Expand a group name stored in the custom mapping into its cars."""
    key = normalise(target)
    if key in CAR_GROUPS:
        return list(CAR_GROUPS[key])
    if key in GROUP_ALIASES:
        return list(CAR_GROUPS[GROUP_ALIASES[key]])
    return [target.strip()]


def group_for_car(car: str) -> list[str] | None:
    """Return the group a car belongs to, if any."""
    key = normalise(car)
    for cars in CAR_GROUPS.values():
        if key in {normalise(c) for c in cars}:
            return list(cars)
    return None


def known_targets() -> list[str]:
    """Every car directory this module knows about, sorted."""
    targets = set(CAR_ALIASES.values())
    for cars in CAR_GROUPS.values():
        targets.update(cars)
    return sorted(targets)


# ---------------------------------------------------------------------------
# Custom mapping persistence
# ---------------------------------------------------------------------------

def load_custom_mapping(
    path: str | os.PathLike[str] | None = None,
    *,
    logger: logging.Logger | None = None,
) -> dict[str, str]:
    target = Path(path) if path is not None else mapping_file()
    if not target.exists():
        return {}
    try:
        data: Any = json.loads(target.read_text(encoding="utf-8") or "{}")
    except (OSError, ValueError) as exc:
        if logger:
            logger.error("Could not read car mapping '%s': %s", target, exc)
        return {}
    if not isinstance(data, dict):
        if logger:
            logger.error("Car mapping '%s' is not a JSON object; ignoring it", target)
        return {}
    return {normalise(k): str(v).strip() for k, v in data.items() if str(v or "").strip()}


def save_custom_mapping(
    mapping: dict[str, str],
    path: str | os.PathLike[str] | None = None,
    *,
    logger: logging.Logger | None = None,
) -> Path:
    """Persist the custom mapping atomically."""
    import tempfile

    target = Path(path) if path is not None else mapping_file()
    payload = {normalise(k): str(v).strip() for k, v in mapping.items() if str(v or "").strip()}
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=str(target.parent), prefix=target.name, suffix=".tmp", delete=False
        ) as handle:
            tmp_path = handle.name
            json.dump(payload, handle, ensure_ascii=False, indent=4, sort_keys=True)
            handle.write("\n")
        os.replace(tmp_path, target)
        tmp_path = None
    except OSError as exc:
        if logger:
            logger.error("Could not write car mapping '%s': %s", target, exc)
        raise
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    return target


def unmapped_folders(folders: Iterable[str], custom_map: dict[str, str] | None = None) -> list[str]:
    """Folders from ``folders`` that cannot be resolved to a car."""
    return [f for f in folders if not identify_setup(f, custom_map)]
