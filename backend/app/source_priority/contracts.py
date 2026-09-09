from __future__ import annotations

from datetime import UTC, datetime
from typing import Final

UNSET: Final = object()


def validate_aware_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware; naive datetimes are not accepted")
    return value.astimezone(UTC)


def validate_non_empty(value: str, field_name: str) -> str:
    if not value:
        raise ValueError(f"{field_name} must be non-empty")
    return value


def validate_positive(value: int, field_name: str) -> int:
    if value < 1:
        raise ValueError(f"{field_name} must be at least 1")
    return value
