from dataclasses import dataclass, field
from typing import Any

from app.config import settings
from app.importers.common import (
    IGNORED_METRIC_TYPES,
    METRIC_MAP,
    CanonicalSample,
    decimal_value,
    normalize_value,
    parse_datetime,
)


class ImportLimitError(ValueError):
    pass


@dataclass(slots=True)
class AdapterResult:
    source_type: str
    samples: list[CanonicalSample] = field(default_factory=list)
    unknown_types: set[str] = field(default_factory=set)
    errors: list[tuple[int | None, str | None, str, str]] = field(default_factory=list)
    received: int = 0
    unknown_count: int = 0
    error_count: int = 0

    def __post_init__(self) -> None:
        self.error_count = max(self.error_count, len(self.errors))

    @property
    def failed_count(self) -> int:
        return max(self.error_count, len(self.errors))

    def add_received(self, count: int = 1) -> None:
        self.received += count
        if self.received > settings.max_import_records:
            raise ImportLimitError("Import enthält zu viele Datensätze")

    def add_sample(self, sample: CanonicalSample) -> None:
        if len(self.samples) >= settings.max_import_samples:
            raise ImportLimitError("Import enthält zu viele unterstützte Ernährungswerte")
        self.samples.append(sample)

    def add_unknown(self, metric_type: str | None, count: int = 1) -> None:
        self.unknown_count += count
        if metric_type and len(self.unknown_types) < settings.max_import_unknown_types:
            self.unknown_types.add(metric_type[:128])

    def add_error(
        self,
        error: tuple[int | None, str | None, str, str],
    ) -> None:
        self.error_count += 1
        if len(self.errors) < settings.max_import_errors:
            item_index, metric_type, code, detail = error
            self.errors.append(
                (
                    item_index,
                    metric_type[:128] if metric_type else None,
                    code[:64],
                    detail[:500],
                )
            )


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
            result.add_error(
                (None, name or None, "invalid_metric", "Metrik enthält keine Datenliste")
            )
            continue
        result.add_received(len(data))
        mapped = METRIC_MAP.get(name)
        if not mapped:
            if name in IGNORED_METRIC_TYPES:
                result.add_unknown(None, len(data))
                item_index += len(data)
                continue
            result.add_unknown(name or "unknown", len(data))
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
                result.add_sample(
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
                result.add_error((item_index, name, "invalid_sample", str(exc)))
            item_index += 1
    return result


def _parse_calograph(payload: dict[str, Any], timezone: str) -> AdapterResult:
    result = AdapterResult(source_type="calograph_sync_v1")
    result.add_received(len(payload["samples"]))
    for index, point in enumerate(payload["samples"]):
        name = str(point.get("type", ""))
        mapped = METRIC_MAP.get(name)
        if not mapped:
            if name in IGNORED_METRIC_TYPES:
                result.add_unknown(None)
                continue
            result.add_unknown(name or "unknown")
            continue
        metric_type, canonical_unit = mapped
        incoming_unit = str(point.get("unit") or canonical_unit)
        try:
            raw_value = decimal_value(point.get("value"))
            start = parse_datetime(str(point.get("start_at")))
            end = parse_datetime(str(point.get("end_at") or point.get("start_at")))
            result.add_sample(
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
            result.add_error((index, name, "invalid_sample", str(exc)))
    return result


def _optional_id(point: dict[str, Any]) -> str | None:
    value = point.get("id") or point.get("uuid") or point.get("external_id")
    return str(value) if value else None
