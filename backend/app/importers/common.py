import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from app.micronutrients import MICRONUTRIENTS


@dataclass(slots=True)
class CanonicalSample:
    metric_type: str
    value: Decimal
    unit: str
    original_value: Decimal
    original_unit: str
    start_at: datetime
    end_at: datetime
    timezone: str
    source_type: str
    source_name: str | None
    source_identifier: str
    external_sample_id: str | None

    def fingerprint(self, user_id: object) -> str:
        payload = {
            "user": str(user_id),
            "source_type": self.source_type,
            "metric": self.metric_type,
            "start": self.start_at.astimezone(UTC).isoformat(),
            "end": self.end_at.astimezone(UTC).isoformat(),
            "value": format(self.value.normalize(), "f"),
            "unit": self.unit,
            "source": self.source_identifier,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()


METRIC_MAP = {
    "dietary_energy": ("dietary_energy_kcal", "kcal"),
    "dietary_energy_consumed": ("dietary_energy_kcal", "kcal"),
    "HKQuantityTypeIdentifierDietaryEnergyConsumed": ("dietary_energy_kcal", "kcal"),
    "dietary_protein": ("protein_g", "g"),
    "HKQuantityTypeIdentifierDietaryProtein": ("protein_g", "g"),
    "dietary_carbohydrates": ("carbohydrates_g", "g"),
    "HKQuantityTypeIdentifierDietaryCarbohydrates": ("carbohydrates_g", "g"),
    "dietary_fat_total": ("fat_g", "g"),
    "HKQuantityTypeIdentifierDietaryFatTotal": ("fat_g", "g"),
    "dietary_saturated_fat": ("saturated_fat_g", "g"),
    "HKQuantityTypeIdentifierDietaryFatSaturated": ("saturated_fat_g", "g"),
    "dietary_fiber": ("fiber_g", "g"),
    "HKQuantityTypeIdentifierDietaryFiber": ("fiber_g", "g"),
    "dietary_sugar": ("sugar_g", "g"),
    "HKQuantityTypeIdentifierDietarySugar": ("sugar_g", "g"),
    "dietary_sodium": ("sodium_mg", "mg"),
    "HKQuantityTypeIdentifierDietarySodium": ("sodium_mg", "mg"),
}
for micronutrient in MICRONUTRIENTS:
    for healthkit_type in micronutrient.healthkit_types:
        METRIC_MAP[healthkit_type] = (micronutrient.metric_type, micronutrient.unit)

IGNORED_METRIC_TYPES = {
    "dietary_water",
    "HKQuantityTypeIdentifierDietaryWater",
    "active_energy",
    "active_energy_burned",
    "HKQuantityTypeIdentifierActiveEnergyBurned",
    "step_count",
    "HKQuantityTypeIdentifierStepCount",
    "apple_exercise_time",
    "HKQuantityTypeIdentifierAppleExerciseTime",
    "weight_&_body_mass",
    "body_mass",
    "weight",
    "HKQuantityTypeIdentifierBodyMass",
    "body_fat_percentage",
    "HKQuantityTypeIdentifierBodyFatPercentage",
}


def parse_datetime(value: str) -> datetime:
    value = value.strip()
    if value.endswith("Z"):
        value = f"{value[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S %z")
    if parsed.tzinfo is None:
        raise ValueError("Zeitstempel benötigt eine Zeitzone")
    return parsed.astimezone(UTC)


def decimal_value(value: Any) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError) as exc:
        raise ValueError("Messwert ist keine gültige Zahl") from exc
    if not result.is_finite() or result < 0:
        raise ValueError("Messwert muss endlich und nicht negativ sein")
    return result


def normalize_value(value: Decimal, incoming_unit: str, canonical_unit: str) -> Decimal:
    unit = (
        incoming_unit.strip()
        .lower()
        .replace("µ", "u")
        .replace("μ", "u")
        .replace("mcg", "ug")
    )
    canonical = canonical_unit.lower()
    if unit == canonical or (unit in {"cal", "kcal"} and canonical == "kcal"):
        return value
    conversions: dict[tuple[str, str], Decimal] = {
        ("kj", "kcal"): Decimal(1) / Decimal("4.184"),
        ("j", "kcal"): Decimal("0.000239005736"),
        ("mg", "g"): Decimal("0.001"),
        ("ug", "g"): Decimal("0.000001"),
        ("g", "mg"): Decimal("1000"),
        ("ug", "mg"): Decimal("0.001"),
        ("mg", "ug"): Decimal("1000"),
        ("g", "ug"): Decimal("1000000"),
        ("l", "ml"): Decimal("1000"),
        ("fl_oz_us", "ml"): Decimal("29.5735295625"),
        ("min", "min"): Decimal("1"),
        ("count", "count"): Decimal("1"),
        ("%", "%"): Decimal("1"),
    }
    factor = conversions.get((unit, canonical))
    if factor is None:
        raise ValueError(f"Nicht unterstützte Einheit: {incoming_unit} → {canonical_unit}")
    return (value * factor).quantize(Decimal("0.000000000001"))


def local_date_for(start_at: datetime, timezone: str) -> date:
    return start_at.astimezone(ZoneInfo(timezone)).date()
