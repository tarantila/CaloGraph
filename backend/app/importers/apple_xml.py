from collections.abc import Iterator
from dataclasses import dataclass
from typing import Protocol

from defusedxml.ElementTree import iterparse  # type: ignore[import-untyped]

from app.importers.common import (
    IGNORED_METRIC_TYPES,
    METRIC_MAP,
    CanonicalSample,
    decimal_value,
    normalize_value,
    parse_datetime,
)
from app.importers.errors import safe_sample_error
from app.importers.json_adapter import AdapterResult


class ReadableByteStream(Protocol):
    def read(self, size: int = -1) -> bytes: ...


@dataclass(slots=True)
class AppleHealthRecord:
    sample: CanonicalSample | None = None
    unknown_type: str | None = None
    error: tuple[int | None, str | None, str, str] | None = None


def iter_apple_health_xml(
    stream: ReadableByteStream,
    timezone: str,
) -> Iterator[AppleHealthRecord]:
    item_index = 0
    for _, element in iterparse(stream, events=("end",)):
        if element.tag != "Record":
            element.clear()
            continue
        attrs = element.attrib
        raw_type = attrs.get("type", "")
        mapped = METRIC_MAP.get(raw_type)
        if not mapped:
            yield AppleHealthRecord(
                unknown_type=None if raw_type in IGNORED_METRIC_TYPES else raw_type or "unknown"
            )
            item_index += 1
            element.clear()
            continue
        metric_type, canonical_unit = mapped
        incoming_unit = attrs.get("unit", canonical_unit)
        try:
            original = decimal_value(attrs.get("value"))
            start = parse_datetime(attrs["startDate"])
            end = parse_datetime(attrs.get("endDate", attrs["startDate"]))
            source_name = attrs.get("sourceName")
            record = AppleHealthRecord(
                sample=CanonicalSample(
                    metric_type=metric_type,
                    value=normalize_value(original, incoming_unit, canonical_unit),
                    unit=canonical_unit,
                    original_value=original,
                    original_unit=incoming_unit,
                    start_at=start,
                    end_at=end,
                    timezone=timezone,
                    source_type="apple_health_xml",
                    source_name=source_name,
                    source_identifier=attrs.get("sourceVersion") or source_name or "apple-health",
                    external_sample_id=None,
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            record = AppleHealthRecord(
                error=(
                    item_index,
                    raw_type,
                    "invalid_sample",
                    safe_sample_error(exc),
                )
            )
        yield record
        item_index += 1
        element.clear()


def parse_apple_health_xml(stream: ReadableByteStream, timezone: str) -> AdapterResult:
    result = AdapterResult(source_type="apple_health_xml")
    for record in iter_apple_health_xml(stream, timezone):
        result.add_received()
        if record.sample is not None:
            result.add_sample(record.sample)
        elif record.error is not None:
            result.add_error(record.error)
        else:
            result.add_unknown(record.unknown_type)
    return result
