import hashlib
import json
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation, localcontext
from typing import Annotated, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import ConfigDict, Field, field_validator
from pydantic.dataclasses import dataclass

from app.importers.errors import ImportFieldError
from app.micronutrients import MICRONUTRIENTS

ORIGINAL_VALUE_LIMIT = Decimal("1000000000000")
CANONICAL_VALUE_LIMIT = Decimal("100000000000000")
CANONICAL_QUANTUM = Decimal("0.000001")


def _decimal_places(value: Decimal) -> int:
    exponent = value.as_tuple().exponent
    return max(0, -exponent) if isinstance(exponent, int) else 0


@dataclass(config=ConfigDict(strict=True, validate_assignment=True), slots=True)
class CanonicalSample:
    metric_type: Annotated[str, Field(max_length=64)]
    value: Decimal
    unit: Annotated[str, Field(max_length=32)]
    original_value: Decimal
    original_unit: Annotated[str, Field(max_length=64)]
    start_at: datetime
    end_at: datetime
    timezone: Annotated[str, Field(max_length=64)]
    source_type: Annotated[str, Field(max_length=64)]
    source_name: Annotated[str | None, Field(max_length=190)]
    source_identifier: Annotated[str, Field(max_length=255)]
    external_sample_id: Annotated[str | None, Field(max_length=255)]

    @field_validator(
        "metric_type",
        "unit",
        "original_unit",
        "timezone",
        "source_type",
        "source_name",
        "source_identifier",
        "external_sample_id",
    )
    @classmethod
    def strings_are_database_safe(cls, value: str | None) -> str | None:
        if value is not None and "\x00" in value:
            raise ValueError("NUL-Zeichen sind nicht erlaubt")
        return value

    @field_validator("value")
    @classmethod
    def canonical_value_fits_database(cls, value: Decimal) -> Decimal:
        if not value.is_finite() or value < 0 or value >= CANONICAL_VALUE_LIMIT:
            raise ValueError("Normalisierter Messwert liegt außerhalb des erlaubten Bereichs")
        try:
            rounded = value.quantize(CANONICAL_QUANTUM, rounding=ROUND_HALF_EVEN)
        except InvalidOperation as exc:
            raise ValueError(
                "Normalisierter Messwert liegt außerhalb des erlaubten Bereichs"
            ) from exc
        if rounded >= CANONICAL_VALUE_LIMIT:
            raise ValueError("Normalisierter Messwert liegt außerhalb des erlaubten Bereichs")
        return rounded

    @field_validator("original_value")
    @classmethod
    def original_value_fits_database(cls, value: Decimal) -> Decimal:
        if (
            not value.is_finite()
            or value < 0
            or value >= ORIGINAL_VALUE_LIMIT
            or _decimal_places(value) > 12
        ):
            raise ValueError("Originalwert liegt außerhalb des erlaubten Bereichs")
        return value

    @field_validator("start_at", "end_at")
    @classmethod
    def timestamps_are_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Zeitstempel benötigt eine Zeitzone")
        return value

    @field_validator("timezone")
    @classmethod
    def timezone_is_known(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError("Unbekannte IANA-Zeitzone") from exc
        return value

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
    "active_energy": ("active_energy_kcal", "kcal"),
    "active_energy_burned": ("active_energy_kcal", "kcal"),
    "HKQuantityTypeIdentifierActiveEnergyBurned": ("active_energy_kcal", "kcal"),
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
        try:
            parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S %z")
        except ValueError as exc:
            raise ImportFieldError("Zeitstempel ist ungültig") from exc
    if parsed.tzinfo is None:
        raise ImportFieldError("Zeitstempel benötigt eine Zeitzone")
    return parsed.astimezone(UTC)


def decimal_value(value: Any) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError) as exc:
        raise ImportFieldError("Messwert ist keine gültige Zahl") from exc
    if (
        not result.is_finite()
        or result < 0
        or result >= ORIGINAL_VALUE_LIMIT
        or _decimal_places(result) > 12
    ):
        raise ImportFieldError(
            "Messwert muss in Numeric(24,12) passen und nicht negativ sein"
        )
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
    same_unit = unit == canonical
    with localcontext() as context:
        context.prec = 50
        conversions: dict[tuple[str, str], Decimal] = {
            ("cal", "kcal"): Decimal("0.001"),
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
        factor = Decimal(1) if same_unit else conversions.get((unit, canonical))
        if factor is None:
            raise ImportFieldError("Einheit wird nicht unterstützt")
        normalized = value * factor
        if normalized >= CANONICAL_VALUE_LIMIT:
            raise ImportFieldError("Normalisierter Messwert ist zu groß")
        try:
            normalized = normalized.quantize(
                CANONICAL_QUANTUM,
                rounding=ROUND_HALF_EVEN,
            )
        except InvalidOperation as exc:
            raise ImportFieldError("Normalisierter Messwert ist zu groß") from exc
    if normalized >= CANONICAL_VALUE_LIMIT:
        raise ImportFieldError("Normalisierter Messwert ist zu groß")
    return normalized


def local_date_for(start_at: datetime, timezone: str) -> date:
    try:
        zone = ZoneInfo(timezone)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise ImportFieldError("Unbekannte IANA-Zeitzone") from exc
    return start_at.astimezone(zone).date()
