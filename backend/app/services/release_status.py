from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

GITHUB_LATEST_RELEASE_URL = "https://api.github.com/repos/tarantila/CaloGraph/releases/latest"
RELEASE_CACHE_TTL_SECONDS = 15 * 60
_RELEASE_TAG_PATTERN = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
ReleaseState = Literal["current", "update_available", "development", "unknown"]


@dataclass(frozen=True)
class ReleaseStatus:
    running: str
    latest: str | None
    status: ReleaseState
    release_url: str | None
    checked_at: datetime


_cache_lock = threading.Lock()
_cached_status: tuple[float, ReleaseStatus] | None = None


def _version_parts(value: str) -> tuple[int, int, int] | None:
    match = _RELEASE_TAG_PATTERN.fullmatch(value)
    if match is None:
        return None
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch)


def _valid_release_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "github.com"
        or not parsed.path.startswith("/tarantila/CaloGraph/releases/")
    ):
        return None
    return value


def _unknown_status(running: str) -> ReleaseStatus:
    return ReleaseStatus(
        running=running,
        latest=None,
        status="unknown",
        release_url=None,
        checked_at=datetime.now(UTC),
    )


def _fetch_release_status(running: str) -> ReleaseStatus:
    request = Request(
        GITHUB_LATEST_RELEASE_URL,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"CaloGraph/{running}",
        },
    )
    try:
        with urlopen(request, timeout=3) as response:
            if response.status != 200:
                return _unknown_status(running)
            payload = json.load(response)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, ValueError):
        return _unknown_status(running)
    if not isinstance(payload, dict):
        return _unknown_status(running)
    tag_name = payload.get("tag_name")
    release_url = _valid_release_url(payload.get("html_url"))
    latest_parts = _version_parts(tag_name) if isinstance(tag_name, str) else None
    running_parts = _version_parts(running)
    published_at = payload.get("published_at")
    try:
        if not isinstance(published_at, str):
            raise ValueError
        datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except ValueError:
        return _unknown_status(running)
    if latest_parts is None or running_parts is None or release_url is None:
        return _unknown_status(running)
    if running_parts == latest_parts:
        state: ReleaseState = "current"
    elif running_parts < latest_parts:
        state = "update_available"
    else:
        state = "development"
    return ReleaseStatus(
        running=running,
        latest=".".join(str(part) for part in latest_parts),
        status=state,
        release_url=release_url,
        checked_at=datetime.now(UTC),
    )


def get_release_status(running: str, *, enabled: bool = True) -> dict[str, object]:
    global _cached_status
    if not enabled:
        return asdict(_unknown_status(running))
    now = time.monotonic()
    with _cache_lock:
        if _cached_status is not None and now - _cached_status[0] < RELEASE_CACHE_TTL_SECONDS:
            return asdict(_cached_status[1])
        status = _fetch_release_status(running)
        _cached_status = (now, status)
        return asdict(status)


def clear_release_status_cache() -> None:
    global _cached_status
    with _cache_lock:
        _cached_status = None
