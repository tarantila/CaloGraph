from datetime import date, datetime, time
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from app.importers.common import CanonicalSample, decimal_value, normalize_value
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
    root = payload.get("days") if isinstance(payload.get("days"), dict) else payload
    dated_items = _dated_items(root)
    micronutrient_root = payload.get("nutrients")
    if micronutrient_root is None and any(
        key in MICRONUTRIENT_BY_YAZIO_ID for key in payload
    ):
        micronutrient_root = payload
    micronutrient_items = _micronutrient_items(micronutrient_root)
    if not dated_items and not micronutrient_items:
        raise ValueError("Unbekanntes YAZIO-Format: keine Tagesdaten im Format YYYY-MM-DD")

    zone = ZoneInfo(timezone)
    result = AdapterResult(source_type=SOURCE_TYPE)
    for item_index, (day, day_data) in enumerate(dated_items):
        if not isinstance(day_data, dict):
            result.errors.append(
                (item_index, day.isoformat(), "invalid_day", "Tagesdaten sind kein Objekt")
            )
            continue

        summary = day_data.get("daily_summary", day_data)
        if not isinstance(summary, dict) or "error" in summary:
            result.errors.append(
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
            values = _daily_values(day_data, summary)
        except (TypeError, ValueError) as exc:
            result.errors.append(
                (item_index, day.isoformat(), "invalid_day", str(exc))
            )
            continue
        imported_for_day = 0
        for input_name, metric_type, value, incoming_unit, canonical_unit in values:
            result.received += 1
            try:
                raw_value = decimal_value(value)
                normalized = normalize_value(raw_value, incoming_unit, canonical_unit)
            except (TypeError, ValueError) as exc:
                result.errors.append(
                    (item_index, input_name, "invalid_sample", str(exc))
                )
                continue
            result.samples.append(
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
            imported_for_day += 1

        if imported_for_day == 0 and not values:
            result.errors.append(
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
        result.received += 1
        try:
            raw_value = decimal_value(value)
            # YAZIO's specific-nutrient endpoint returns every nutrient as
            # grams, including vitamins normally displayed as micrograms.
            normalized = normalize_value(raw_value, "g", definition.unit)
        except (TypeError, ValueError) as exc:
            result.errors.append(
                (item_index, nutrient_id, "invalid_sample", str(exc))
            )
            continue
        at = datetime.combine(day, time(hour=12), tzinfo=zone)
        result.samples.append(
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
    day_data: dict[str, Any], summary: dict[str, Any]
) -> list[tuple[str, str, Any, str, str]]:
    values: list[tuple[str, str, Any, str, str]] = []
    units = summary.get("units")
    energy_unit = (
        str(units.get("unit_energy", "kcal")) if isinstance(units, dict) else "kcal"
    )

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


def _meal_nutrient_totals(summary: dict[str, Any]) -> dict[str, Decimal]:
    meals = summary.get("meals")
    if not isinstance(meals, dict):
        return {}

    raw_values: dict[str, list[Any]] = {key: [] for key in _NUTRIENT_METRICS}
    for meal in meals.values():
        if not isinstance(meal, dict):
            continue
        nutrients = meal.get("nutrients")
        if not isinstance(nutrients, dict):
            continue
        for nutrient_name in raw_values:
            if nutrients.get(nutrient_name) is not None:
                raw_values[nutrient_name].append(nutrients[nutrient_name])

    totals: dict[str, Decimal] = {}
    for nutrient_name, items in raw_values.items():
        if items:
            totals[nutrient_name] = sum((decimal_value(value) for value in items), Decimal())
    return totals


def _flat_nutrients(day_data: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for flat_name, nutrient_name in _FLAT_NUTRIENTS.items():
        if nutrient_name not in result and day_data.get(flat_name) is not None:
            result[nutrient_name] = day_data[flat_name]
    return result
