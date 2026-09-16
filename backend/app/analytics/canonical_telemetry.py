from __future__ import annotations

from datetime import date, datetime

_MAX_EXCEPTION_CLASS_LENGTH = 64


def _range_bucket(start: date, end: date) -> str:
    if (
        not isinstance(start, date)
        or isinstance(start, datetime)
        or not isinstance(end, date)
        or isinstance(end, datetime)
        or start > end
    ):
        return "invalid"
    days = (end - start).days + 1
    if days == 1:
        return "1"
    if days <= 7:
        return "2-7"
    if days <= 31:
        return "8-31"
    if days <= 366:
        return "32-366"
    return "367+"


def _duration_bucket(duration_seconds: float) -> str:
    milliseconds = duration_seconds * 1_000
    if milliseconds < 10:
        return "<10ms"
    if milliseconds < 50:
        return "10-49ms"
    if milliseconds < 200:
        return "50-199ms"
    return "200ms+"


def _exception_class(exc: BaseException) -> str:
    return type(exc).__name__[:_MAX_EXCEPTION_CLASS_LENGTH]
