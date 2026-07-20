from dataclasses import dataclass, field
from typing import Any

from app.importers.common import (
    METRIC_MAP,
    CanonicalSample,
    decimal_value,
    normalize_value,
    parse_datetime,
)


@dataclass(slots=True)
class AdapterResult:
    source_type: str
    samples: list[CanonicalSample] = field(default_factory=list)
    unknown_types: set[str] = field(default_factory=set)
    errors: list[tuple[int | None, str | None, str, str]] = field(default_factory=list)
    received: int = 0
    unknown_count: int = 0


def parse_json_payload(payload: dict[str, Any], timezone: str) -> AdapterResult:
    if isinstance(payload.get("samples"), list):
        return _parse_calograph(payload, timezone)
    root = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if isinstance(root, dict) and isinstance(root.get("metrics"), list):
        return _parse_health_auto_export(root, timezone)
    raise ValueError("Unbekanntes JSON-Format: 'metrics' oder 'samples' fehlt")


def _parse_health_auto_export(payload: dict[str, Any], timezone: str) -> AdapterResult:
    result = AdapterResult(source_type="health_auto_export_v2")
    item_index = 0
    for metric in payload["metrics"]:
        name = str(metric.get("name", ""))
        data = metric.get("data")
        if not isinstance(data, list):
            result.errors.append(
                (None, name or None, "invalid_metric", "Metrik enthält keine Datenliste")
            )
            continue
        result.received += len(data)
        mapped = METRIC_MAP.get(name)
        if not mapped:
            result.unknown_types.add(name or "unknown")
            result.unknown_count += len(data)
            item_index += len(data)
            continue
        metric_type, canonical_unit = mapped
        incoming_unit = str(metric.get("units") or metric.get("unit") or canonical_unit)
        for point in data:
            try:
                raw_value = decimal_value(point.get("qty", point.get("value")))
                start_raw = point.get("startDate") or point.get("start_date") or point.get("date")
                if not start_raw:
                    raise ValueError("Startzeit fehlt")
                start = parse_datetime(str(start_raw))
                end = parse_datetime(
                    str(point.get("endDate") or point.get("end_date") or start_raw)
                )
                source_name = point.get("source") or point.get("sourceName")
                source_id = str(
                    point.get("sourceBundle")
                    or point.get("sourceId")
                    or source_name
                    or payload.get("source")
                    or "health-auto-export"
                )
                result.samples.append(
                    CanonicalSample(
                        metric_type=metric_type,
                        value=normalize_value(raw_value, incoming_unit, canonical_unit),
                        unit=canonical_unit,
                        original_value=raw_value,
                        original_unit=incoming_unit,
                        start_at=start,
                        end_at=end,
                        timezone=timezone,
                        source_type=result.source_type,
                        source_name=str(source_name) if source_name else None,
                        source_identifier=source_id,
                        external_sample_id=_optional_id(point),
                    )
                )
            except (TypeError, ValueError) as exc:
                result.errors.append((item_index, name, "invalid_sample", str(exc)))
            item_index += 1
    return result


def _parse_calograph(payload: dict[str, Any], timezone: str) -> AdapterResult:
    result = AdapterResult(source_type="calograph_sync_v1", received=len(payload["samples"]))
    for index, point in enumerate(payload["samples"]):
        name = str(point.get("type", ""))
        mapped = METRIC_MAP.get(name)
        if not mapped:
            result.unknown_types.add(name or "unknown")
            result.unknown_count += 1
            continue
        metric_type, canonical_unit = mapped
        incoming_unit = str(point.get("unit") or canonical_unit)
        try:
            raw_value = decimal_value(point.get("value"))
            start = parse_datetime(str(point.get("start_at")))
            end = parse_datetime(str(point.get("end_at") or point.get("start_at")))
            result.samples.append(
                CanonicalSample(
                    metric_type=metric_type,
                    value=normalize_value(raw_value, incoming_unit, canonical_unit),
                    unit=canonical_unit,
                    original_value=raw_value,
                    original_unit=incoming_unit,
                    start_at=start,
                    end_at=end,
                    timezone=str(point.get("timezone") or timezone),
                    source_type=result.source_type,
                    source_name=point.get("source_name"),
                    source_identifier=str(point.get("source_identifier") or "calograph-client"),
                    external_sample_id=_optional_id(point),
                )
            )
        except (TypeError, ValueError) as exc:
            result.errors.append((index, name, "invalid_sample", str(exc)))
    return result


def _optional_id(point: dict[str, Any]) -> str | None:
    value = point.get("id") or point.get("uuid") or point.get("external_id")
    return str(value) if value else None
