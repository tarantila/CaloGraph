from typing import IO

from defusedxml.ElementTree import iterparse  # type: ignore[import-untyped]

from app.importers.common import (
    METRIC_MAP,
    CanonicalSample,
    decimal_value,
    normalize_value,
    parse_datetime,
)
from app.importers.json_adapter import AdapterResult


def parse_apple_health_xml(stream: IO[bytes], timezone: str) -> AdapterResult:
    result = AdapterResult(source_type="apple_health_xml")
    for _, element in iterparse(stream, events=("end",)):
        if element.tag != "Record":
            element.clear()
            continue
        result.received += 1
        attrs = element.attrib
        raw_type = attrs.get("type", "")
        mapped = METRIC_MAP.get(raw_type)
        if not mapped:
            result.unknown_types.add(raw_type or "unknown")
            result.unknown_count += 1
            element.clear()
            continue
        metric_type, canonical_unit = mapped
        incoming_unit = attrs.get("unit", canonical_unit)
        try:
            original = decimal_value(attrs.get("value"))
            start = parse_datetime(attrs["startDate"])
            end = parse_datetime(attrs.get("endDate", attrs["startDate"]))
            source_name = attrs.get("sourceName")
            result.samples.append(
                CanonicalSample(
                    metric_type=metric_type,
                    value=normalize_value(original, incoming_unit, canonical_unit),
                    unit=canonical_unit,
                    original_value=original,
                    original_unit=incoming_unit,
                    start_at=start,
                    end_at=end,
                    timezone=timezone,
                    source_type=result.source_type,
                    source_name=source_name,
                    source_identifier=attrs.get("sourceVersion") or source_name or "apple-health",
                    external_sample_id=None,
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            result.errors.append((result.received - 1, raw_type, "invalid_sample", str(exc)))
        element.clear()
    return result
