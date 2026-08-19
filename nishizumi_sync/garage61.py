"""Garage 61 team driver lookup."""

from __future__ import annotations

import logging
from typing import Any, Iterable

from .cars import clean_name
from .http import HttpError, get

API_ROOT = "https://garage61.net/api"
DEFAULT_TIMEOUT = 15


def team_endpoint(team_id: str) -> str:
    return f"{API_ROOT}/teams/{team_id.strip()}/drivers"


def _iter_records(payload: Any) -> Iterable[dict]:
    """Garage61 has returned several shapes over time; accept all of them."""
    if isinstance(payload, list):
        candidates: Iterable[Any] = payload
    elif isinstance(payload, dict):
        for key in ("drivers", "members", "items", "data", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                candidates = value
                break
        else:
            candidates = []
    else:
        candidates = []
    for item in candidates:
        if isinstance(item, dict):
            yield item
        elif isinstance(item, str):
            yield {"name": item}


def _record_name(record: dict) -> str:
    for key in ("name", "displayName", "display_name", "fullName", "full_name", "username"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value
    first = str(record.get("firstName") or record.get("first_name") or "").strip()
    last = str(record.get("lastName") or record.get("last_name") or "").strip()
    return f"{first} {last}".strip()


def fetch_drivers(
    team_id: str,
    api_key: str | None,
    logger: logging.Logger,
    *,
    timeout: int = DEFAULT_TIMEOUT,
) -> list[str] | None:
    """Return the team's driver names, or ``None`` when the lookup failed.

    ``None`` and ``[]`` mean different things: ``None`` keeps the configured
    driver list untouched, an empty list would wipe it.
    """
    team_id = str(team_id or "").strip()
    if not team_id:
        logger.warning("Garage61 team ID is empty; skipping driver lookup")
        return None

    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key.strip()}"

    try:
        response = get(team_endpoint(team_id), headers=headers, timeout=timeout)
        payload = response.json()
    except HttpError as exc:
        if exc.status in (401, 403):
            logger.error("Garage61 rejected the API key (HTTP %s)", exc.status)
        elif exc.status == 404:
            logger.error("Garage61 team '%s' was not found", team_id)
        else:
            logger.warning("Could not fetch drivers from Garage61: %s", exc)
        return None

    names: list[str] = []
    seen: set[str] = set()
    for record in _iter_records(payload):
        name = clean_name(_record_name(record))
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        names.append(name)

    if not names:
        logger.warning("Garage61 returned no drivers for team '%s'", team_id)
        return None
    logger.info("Garage61 returned %d driver(s)", len(names))
    return names
