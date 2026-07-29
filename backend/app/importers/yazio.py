from datetime import date, datetime, time
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import ValidationError

from app.importers.common import CanonicalSample, decimal_value, normalize_value
from app.importers.errors import (
    ImportFormatError,
    ImportLimitError,
    safe_sample_error,
)
from app.importers.input_models import (
    YazioDayInput,
    YazioExportRootInput,
    YazioSummaryInput,
)
from app.importers.json_adapter import AdapterResult
from app.micronutrients import MICRONUTRIENT_BY_YAZIO_ID

SOURCE_TYPE = "yazio_export_v1"

_NUTRIENT_METRICS = {
    "energy.energy": ("dietary_energy_kcal", "kcal"),
    "nutrient.protein": ("protein_g", "g"),
    "nutrient.carb": ("carbohydrates_g", "g"),
    "nutrient.fat": ("fat_g", "g"),
}

_FLAT_NUTRIENTS = {
    "energy": "energy.energy",
    "calories": "energy.energy",
    "protein": "nutrient.protein",
    "carb": "nutrient.carb",
    "carbs": "nutrient.carb",
    "fat": "nutrient.fat",
}


def parse_yazio_export(
    payload: dict[str, Any],
    timezone: str,
    source_identifier: str = "yazio-account",
) -> AdapterResult:
    """Normalize a yazio-exporter days.json payload into CaloGraph samples."""
    try:
        validated_root = YazioExportRootInput.model_validate(payload).root
    except ValidationError as exc:
        raise ImportFormatError(
            "YAZIO-Struktur enthält ungültige oder zu lange Felder"
        ) from exc
    if "\x00" in source_identifier or len(source_identifier) > 255:
        raise ImportFormatError("YAZIO-Quellkennung ist ungültig")
    try:
        zone = ZoneInfo(timezone)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise ImportFormatError("Unbekannte IANA-Zeitzone") from exc

    if "days" in validated_root and not isinstance(validated_root["days"], dict):
        raise ImportFormatError("YAZIO-Feld 'days' muss ein Objekt sein")
    root = (
        validated_root["days"]
        if isinstance(validated_root.get("days"), dict)
        else validated_root
    )
    dated_items = _dated_items(root)
    micronutrient_root = validated_root.get("nutrients")
    if micronutrient_root is not None and not isinstance(micronutrient_root, dict):
        raise ImportFormatError("YAZIO-Feld 'nutrients' muss ein Objekt sein")
    if micronutrient_root is None and any(
        key in MICRONUTRIENT_BY_YAZIO_ID for key in validated_root
    ):
        micronutrient_root = validated_root
    micronutrient_items = _micronutrient_items(micronutrient_root)
    if not dated_items and not micronutrient_items:
        raise ImportFormatError(
            "Unbekanntes YAZIO-Format: keine Tagesdaten im Format YYYY-MM-DD"
        )

    result = AdapterResult(source_type=SOURCE_TYPE)
    for item_index, (day, day_data) in enumerate(dated_items):
        if not isinstance(day_data, dict):
            result.add_error(
                (item_index, day.isoformat(), "invalid_day", "Tagesdaten sind kein Objekt")
            )
            continue

        try:
            validated_day = YazioDayInput.model_validate(day_data)
        except ValidationError:
            result.add_error(
                (
                    item_index,
                    day.isoformat(),
                    "invalid_day",
                    "YAZIO-Tagesdaten enthalten ungültige oder zu lange Felder",
                )
            )
            continue
        summary: YazioSummaryInput = (
            validated_day.daily_summary or validated_day
        )
        if summary.error is not None:
            result.add_error(
                (
                    item_index,
                    day.isoformat(),
                    "invalid_day",
                    "YAZIO-Tageszusammenfassung ist nicht verfügbar",
                )
            )
            continue

        at = datetime.combine(day, time(hour=12), tzinfo=zone)
        try:
            values = _daily_values(validated_day, summary)
        except (TypeError, ValueError) as exc:
            result.add_error(
                (
                    item_index,
                    day.isoformat(),
                    "invalid_day",
                    safe_sample_error(exc),
                )
            )
            continue
        imported_for_day = 0
        for input_name, metric_type, value, incoming_unit, canonical_unit in values:
            result.add_received()
            try:
                raw_value = decimal_value(value)
                normalized = normalize_value(raw_value, incoming_unit, canonical_unit)
                result.add_sample(
                    CanonicalSample(
                        metric_type=metric_type,
                        value=normalized,
                        unit=canonical_unit,
                        original_value=raw_value,
                        original_unit=incoming_unit,
                        start_at=at,
                        end_at=at,
                        timezone=timezone,
                        source_type=SOURCE_TYPE,
                        source_name="YAZIO",
                        source_identifier=source_identifier,
                        external_sample_id=f"{day.isoformat()}:{metric_type}",
                    )
                )
            except ImportLimitError:
                raise
            except (TypeError, ValueError) as exc:
                result.add_error(
                    (
                        item_index,
                        input_name,
                        "invalid_sample",
                        safe_sample_error(exc),
                    )
                )
                continue
            imported_for_day += 1

        if imported_for_day == 0 and not values:
            result.add_error(
                (
                    item_index,
                    day.isoformat(),
                    "empty_day",
                    "Keine unterstützten YAZIO-Tageswerte gefunden",
                )
            )

    for item_index, (day, nutrient_id, value) in enumerate(
        micronutrient_items, start=len(dated_items)
    ):
        definition = MICRONUTRIENT_BY_YAZIO_ID[nutrient_id]
        result.add_received()
        try:
            raw_value = decimal_value(value)
            # YAZIO's specific-nutrient endpoint returns every nutrient as
            # grams, including vitamins normally displayed as micrograms.
            normalized = normalize_value(raw_value, "g", definition.unit)
            at = datetime.combine(day, time(hour=12), tzinfo=zone)
            result.add_sample(
                CanonicalSample(
                    metric_type=definition.metric_type,
                    value=normalized,
                    unit=definition.unit,
                    original_value=raw_value,
                    original_unit="g",
                    start_at=at,
                    end_at=at,
                    timezone=timezone,
                    source_type=SOURCE_TYPE,
                    source_name="YAZIO",
                    source_identifier=source_identifier,
                    external_sample_id=f"{day.isoformat()}:{definition.metric_type}",
                )
            )
        except ImportLimitError:
            raise
        except (TypeError, ValueError) as exc:
            result.add_error(
                (
                    item_index,
                    nutrient_id,
                    "invalid_sample",
                    safe_sample_error(exc),
                )
            )
            continue
    return result


def _dated_items(root: object) -> list[tuple[date, object]]:
    if not isinstance(root, dict):
        return []
    result: list[tuple[date, object]] = []
    for key, value in root.items():
        try:
            parsed = date.fromisoformat(str(key))
        except ValueError:
            continue
        result.append((parsed, value))
    return sorted(result, key=lambda item: item[0])


def _micronutrient_items(root: object) -> list[tuple[date, str, object]]:
    if not isinstance(root, dict):
        return []
    result: list[tuple[date, str, object]] = []
    for nutrient_id, daily_values in root.items():
        if nutrient_id not in MICRONUTRIENT_BY_YAZIO_ID or not isinstance(
            daily_values, dict
        ):
            continue
        for day_value, value in daily_values.items():
            try:
                day = date.fromisoformat(str(day_value))
            except ValueError:
                continue
            if value is not None:
                result.append((day, nutrient_id, value))
    return sorted(result, key=lambda item: (item[0], item[1]))


def _daily_values(
    day_data: YazioDayInput,
    summary: YazioSummaryInput,
) -> list[tuple[str, str, Any, str, str]]:
    values: list[tuple[str, str, Any, str, str]] = []
    energy_unit = summary.units.unit_energy if summary.units else "kcal"

    nutrients = _meal_nutrient_totals(summary)
    if not nutrients:
        nutrients = _flat_nutrients(day_data)
    if nutrients and all(decimal_value(value) == 0 for value in nutrients.values()):
        nutrients = {}
    for input_name, value in nutrients.items():
        metric_type, canonical_unit = _NUTRIENT_METRICS[input_name]
        incoming_unit = energy_unit if input_name == "energy.energy" else "g"
        values.append((input_name, metric_type, value, incoming_unit, canonical_unit))

    return values


def _meal_nutrient_totals(summary: YazioSummaryInput) -> dict[str, Decimal]:
    meals = summary.meals
    if meals is None:
        return {}

    raw_values: dict[str, list[Any]] = {key: [] for key in _NUTRIENT_METRICS}
    for meal in meals.values():
        nutrients = meal.nutrients
        if nutrients is None:
            continue
        for nutrient_name in raw_values:
            if nutrients.get(nutrient_name) is not None:
                raw_values[nutrient_name].append(nutrients[nutrient_name])

    totals: dict[str, Decimal] = {}
    for nutrient_name, items in raw_values.items():
        if items:
            totals[nutrient_name] = sum((decimal_value(value) for value in items), Decimal())
    return totals


def _flat_nutrients(day_data: YazioDayInput) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for flat_name, nutrient_name in _FLAT_NUTRIENTS.items():
        value = getattr(day_data, flat_name)
        if nutrient_name not in result and value is not None:
            result[nutrient_name] = value
    return result
