"""Tiny HTTP helper.

``requests`` is an optional dependency, but update checks and the Garage61
integration should not silently disable themselves when it is missing — the
standard library can do the job.  This module offers one interface backed by
``requests`` when available and ``urllib`` otherwise.
"""

from __future__ import annotations

import json
import shutil
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from . import PROJECT_URL, __version__

try:  # pragma: no cover - exercised implicitly depending on the environment
    import requests
except Exception:  # pragma: no cover
    requests = None  # type: ignore[assignment]

USER_AGENT = f"NishizumiSetupsSync/{__version__} (+{PROJECT_URL})"
DEFAULT_TIMEOUT = 15


class HttpError(Exception):
    """Any network or protocol level failure."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass
class Response:
    status: int
    body: bytes
    headers: Mapping[str, str]

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self) -> Any:
        try:
            return json.loads(self.text)
        except ValueError as exc:
            raise HttpError(f"Invalid JSON response: {exc}") from exc


def _headers(extra: Mapping[str, str] | None) -> dict[str, str]:
    headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    if extra:
        headers.update({k: v for k, v in extra.items() if v})
    return headers


def get(
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> Response:
    """Perform a GET request, raising :class:`HttpError` on failure."""
    all_headers = _headers(headers)
    if requests is not None:
        try:
            response = requests.get(url, headers=all_headers, timeout=timeout)
            response.raise_for_status()
            return Response(response.status_code, response.content, dict(response.headers))
        except requests.exceptions.HTTPError as exc:  # type: ignore[union-attr]
            status = exc.response.status_code if exc.response is not None else None
            raise HttpError(f"HTTP {status} for {url}", status) from exc
        except Exception as exc:  # network errors, invalid URLs, …
            raise HttpError(f"Request to {url} failed: {exc}") from exc

    request = urllib.request.Request(url, headers=all_headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=ssl.create_default_context()) as handle:
            return Response(handle.status, handle.read(), dict(handle.headers))
    except urllib.error.HTTPError as exc:
        raise HttpError(f"HTTP {exc.code} for {url}", exc.code) from exc
    except Exception as exc:
        raise HttpError(f"Request to {url} failed: {exc}") from exc


def download(
    url: str,
    destination: Path,
    *,
    headers: Mapping[str, str] | None = None,
    timeout: int = 60,
    progress: Callable[[int, int], None] | None = None,
) -> Path:
    """Stream ``url`` into ``destination``.

    ``progress`` receives ``(downloaded_bytes, total_bytes)``; ``total_bytes``
    is ``0`` when the server does not advertise a length.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    all_headers = _headers(headers)

    if requests is not None:
        try:
            with requests.get(url, headers=all_headers, timeout=timeout, stream=True) as response:
                response.raise_for_status()
                total = int(response.headers.get("Content-Length") or 0)
                done = 0
                with open(destination, "wb") as handle:
                    for chunk in response.iter_content(chunk_size=256 * 1024):
                        if not chunk:
                            continue
                        handle.write(chunk)
                        done += len(chunk)
                        if progress:
                            progress(done, total)
            return destination
        except Exception as exc:
            raise HttpError(f"Download of {url} failed: {exc}") from exc

    request = urllib.request.Request(url, headers=all_headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=ssl.create_default_context()) as source:
            total = int(source.headers.get("Content-Length") or 0)
            done = 0
            with open(destination, "wb") as handle:
                if progress is None:
                    shutil.copyfileobj(source, handle)
                else:
                    while True:
                        chunk = source.read(256 * 1024)
                        if not chunk:
                            break
                        handle.write(chunk)
                        done += len(chunk)
                        progress(done, total)
    except Exception as exc:
        raise HttpError(f"Download of {url} failed: {exc}") from exc
    return destination
