from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from threading import Lock
from typing import Literal

APP_LOG_BUFFER_LIMIT = 500
_APP_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR"})


@dataclass(frozen=True, slots=True)
class AppLogEntry:
    occurred_at: datetime
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"]
    action: str
    duration_ms: int
    request_id: str
    status: int


_entries: deque[AppLogEntry] = deque(maxlen=APP_LOG_BUFFER_LIMIT)
_lock = Lock()


def record_app_log(
    *,
    level: str,
    action: str,
    duration_ms: int,
    request_id: str,
    status: int,
) -> None:
    normalized_level = level.upper()
    if normalized_level not in _APP_LOG_LEVELS:
        normalized_level = "INFO"
    if not action or len(action) > 256:
        return
    if len(request_id) != 32 or any(character not in "0123456789abcdef" for character in request_id):
        return
    entry = AppLogEntry(
        occurred_at=datetime.now(UTC),
        level=normalized_level,  # type: ignore[arg-type]
        action=action,
        duration_ms=max(0, min(duration_ms, 86_400_000)),
        request_id=request_id,
        status=max(0, min(status, 999)),
    )
    with _lock:
        _entries.append(entry)


def get_app_logs(
    *,
    request_id: str | None = None,
    action: str | None = None,
    level: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 100,
) -> list[dict[str, object]]:
    normalized_level = level.upper() if level else None
    with _lock:
        entries = list(_entries)
    filtered = []
    for entry in reversed(entries):
        if request_id and entry.request_id != request_id:
            continue
        if action and action.casefold() not in entry.action.casefold():
            continue
        if normalized_level and entry.level != normalized_level:
            continue
        if since and entry.occurred_at < since:
            continue
        if until and entry.occurred_at >= until:
            continue
        item = asdict(entry)
        item["occurred_at"] = entry.occurred_at.isoformat()
        filtered.append(item)
        if len(filtered) >= limit:
            break
    return filtered


def clear_app_logs() -> None:
    with _lock:
        _entries.clear()
