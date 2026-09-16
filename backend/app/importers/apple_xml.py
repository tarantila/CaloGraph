from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Protocol
from xml.etree.ElementTree import Element

from defusedxml.ElementTree import iterparse  # type: ignore[import-untyped]

from app.importers.common import (
    IGNORED_METRIC_TYPES,
    METRIC_MAP,
    CanonicalSample,
    decimal_value,
    normalize_value,
    parse_datetime,
)
from app.importers.errors import ImportLimitError, safe_sample_error
from app.importers.json_adapter import AdapterResult


class ReadableByteStream(Protocol):
    def read(self, size: int = -1) -> bytes: ...


@dataclass(frozen=True, slots=True)
class AppleFoodNutrient:
    raw_type: str
    raw_value: str | None
    value: Decimal | None
    unit: str | None
    start_at: datetime | None
    end_at: datetime | None
    source_name: str | None
    source_version: str | None
    metadata: tuple[tuple[str, str], ...] = ()
    value_error: str | None = None


@dataclass(frozen=True, slots=True)
class AppleFoodCorrelation:
    source_name: str | None
    source_version: str | None
    device: str | None
    start_at: datetime | None
    end_at: datetime | None
    creation_at: datetime | None
    food_name: str | None
    external_uuid: str | None
    metadata: tuple[tuple[str, str], ...]
    nutrients: tuple[AppleFoodNutrient, ...]


@dataclass(slots=True)
class _FoodCorrelationBuilder:
    source_name: str | None
    source_version: str | None
    device: str | None
    start_at: datetime | None
    end_at: datetime | None
    creation_at: datetime | None
    metadata: dict[str, str] = field(default_factory=dict)
    nutrients: list[AppleFoodNutrient] = field(default_factory=list)
    record_metadata: list[tuple[str, str]] | None = None


@dataclass(slots=True)
class AppleHealthRecord:
    sample: CanonicalSample | None = None
    unknown_type: str | None = None
    error: tuple[int | None, str | None, str, str] | None = None
    food_correlation: AppleFoodCorrelation | None = None

_FOOD_CORRELATION_TYPE = "HKCorrelationTypeIdentifierFood"
_FOOD_METADATA_KEYS = frozenset({"HKExternalUUID", "HKFoodType"})
_MAX_APPLE_TEXT_BYTES = 512
_MAX_APPLE_TYPE_BYTES = 128


def _bounded_text(value: str | None, max_bytes: int = _MAX_APPLE_TEXT_BYTES) -> str | None:
    if value is None or "\x00" in value:
        return None
    if len(value.encode("utf-8")) > max_bytes:
        return None
    return value


def _optional_datetime(attrs: dict[str, str], key: str) -> datetime | None:
    value = _bounded_text(attrs.get(key))
    if value is None:
        return None
    try:
        return parse_datetime(value)
    except (TypeError, ValueError):
        return None


def _metadata_entry(element: Element) -> tuple[str, str] | None:
    key = _bounded_text(element.attrib.get("key"), _MAX_APPLE_TYPE_BYTES)
    value = _bounded_text(element.attrib.get("value"))
    if key not in _FOOD_METADATA_KEYS or not value:
        return None
    return key, value


def _food_nutrient_from_element(
    element: Element,
    record_metadata: tuple[tuple[str, str], ...],
) -> AppleFoodNutrient:
    attrs = element.attrib
    raw_type = _bounded_text(attrs.get("type"), _MAX_APPLE_TYPE_BYTES) or "unknown"
    raw_value = _bounded_text(attrs.get("value"), 128)
    unit = _bounded_text(attrs.get("unit"), 64)
    value: Decimal | None = None
    value_error: str | None = None
    if raw_value is None:
        value_error = "missing_value"
    else:
        try:
            value = decimal_value(raw_value)
        except (TypeError, ValueError):
            value_error = "invalid_value"
    return AppleFoodNutrient(
        raw_type=raw_type,
        raw_value=raw_value,
        value=value,
        unit=unit,
        start_at=_optional_datetime(attrs, "startDate"),
        end_at=_optional_datetime(attrs, "endDate"),
        source_name=_bounded_text(attrs.get("sourceName")),
        source_version=_bounded_text(attrs.get("sourceVersion")),
        metadata=record_metadata,
        value_error=value_error,
    )


def _food_builder_from_element(element: Element) -> _FoodCorrelationBuilder:
    attrs = element.attrib
    return _FoodCorrelationBuilder(
        source_name=_bounded_text(attrs.get("sourceName")),
        source_version=_bounded_text(attrs.get("sourceVersion")),
        device=_bounded_text(attrs.get("device")),
        start_at=_optional_datetime(attrs, "startDate"),
        end_at=_optional_datetime(attrs, "endDate"),
        creation_at=_optional_datetime(attrs, "creationDate"),
    )


def _food_correlation_from_builder(
    builder: _FoodCorrelationBuilder,
) -> AppleFoodCorrelation:
    return AppleFoodCorrelation(
        source_name=builder.source_name,
        source_version=builder.source_version,
        device=builder.device,
        start_at=builder.start_at,
        end_at=builder.end_at,
        creation_at=builder.creation_at,
        food_name=builder.metadata.get("HKFoodType"),
        external_uuid=builder.metadata.get("HKExternalUUID"),
        metadata=tuple(sorted(builder.metadata.items())),
        nutrients=tuple(builder.nutrients),
    )


def _record_from_element(
    element: Element,
    timezone: str,
    item_index: int,
) -> AppleHealthRecord:
    attrs = element.attrib
    raw_type = _bounded_text(attrs.get("type"), _MAX_APPLE_TYPE_BYTES) or ""
    mapped = METRIC_MAP.get(raw_type)
    if not mapped:
        return AppleHealthRecord(
            unknown_type=None if raw_type in IGNORED_METRIC_TYPES else raw_type or "unknown"
        )
    metric_type, canonical_unit = mapped
    incoming_unit = _bounded_text(attrs.get("unit"), 64) or canonical_unit
    raw_value = _bounded_text(attrs.get("value"), 128)
    start_text = _bounded_text(attrs.get("startDate"))
    end_text = _bounded_text(attrs.get("endDate")) or start_text
    source_name = _bounded_text(attrs.get("sourceName"), 190)
    source_version = _bounded_text(attrs.get("sourceVersion"), 255)
    try:
        if start_text is None or end_text is None:
            raise ValueError("missing sample date")
        original = decimal_value(raw_value)
        start = parse_datetime(start_text)
        end = parse_datetime(end_text)
        return AppleHealthRecord(
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
                source_identifier=source_version or source_name or "apple-health",
                external_sample_id=None,
            )
        )
    except (KeyError, TypeError, ValueError) as exc:
        return AppleHealthRecord(
            error=(
                item_index,
                raw_type,
                "invalid_sample",
                safe_sample_error(exc),
            )
        )


def iter_apple_health_xml(
    stream: ReadableByteStream,
    timezone: str,
    *,
    max_records: int | None = None,
) -> Iterator[AppleHealthRecord]:
    item_index = 0
    food_builder: _FoodCorrelationBuilder | None = None
    for event, element in iterparse(stream, events=("start", "end")):
        if event == "start":
            if element.tag == "Correlation" and element.attrib.get("type") == _FOOD_CORRELATION_TYPE:
                food_builder = _food_builder_from_element(element)
            elif element.tag == "Record":
                if max_records is not None and item_index >= max_records:
                    raise ImportLimitError("Import enthält zu viele XML-Datensätze")
                if food_builder is not None:
                    food_builder.record_metadata = []
            continue

        if element.tag == "MetadataEntry":
            entry = _metadata_entry(element)
            if entry is not None and food_builder is not None:
                if food_builder.record_metadata is not None:
                    food_builder.record_metadata.append(entry)
                else:
                    food_builder.metadata[entry[0]] = entry[1]
            element.clear()
            continue

        if element.tag == "Record":
            if food_builder is not None:
                food_builder.nutrients.append(
                    _food_nutrient_from_element(
                        element,
                        tuple(food_builder.record_metadata or ()),
                    )
                )
                food_builder.record_metadata = None
            yield _record_from_element(element, timezone, item_index)
            item_index += 1
            element.clear()
            continue

        if element.tag == "Correlation":
            if food_builder is not None:
                yield AppleHealthRecord(
                    food_correlation=_food_correlation_from_builder(food_builder)
                )
                food_builder = None
            element.clear()
            continue

        element.clear()


def parse_apple_health_xml(stream: ReadableByteStream, timezone: str) -> AdapterResult:
    result = AdapterResult(source_type="apple_health_xml")
    for record in iter_apple_health_xml(stream, timezone):
        if record.food_correlation is not None:
            continue
        result.add_received()
        if record.sample is not None:
            result.add_sample(record.sample)
        elif record.error is not None:
            result.add_error(record.error)
        else:
            result.add_unknown(record.unknown_type)
    return result
