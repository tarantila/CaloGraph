from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class MetricDefinition:
    metric_key: str
    canonical_unit: str
    value_eligible: bool = True


_CANONICAL_METRICS = {
    "dietary_energy_kcal": MetricDefinition("dietary_energy_kcal", "kcal"),
    "protein_g": MetricDefinition("protein_g", "g"),
    "carbohydrates_g": MetricDefinition("carbohydrates_g", "g"),
    "fat_g": MetricDefinition("fat_g", "g"),
    "fiber_g": MetricDefinition("fiber_g", "g"),
    "sugar_g": MetricDefinition("sugar_g", "g"),
    "saturated_fat_g": MetricDefinition("saturated_fat_g", "g"),
}

CANONICAL_METRICS: Mapping[str, MetricDefinition] = MappingProxyType(_CANONICAL_METRICS)
UNSUPPORTED_METRIC_KEYS = frozenset({"salt"})


def metric_definition(metric_key: str) -> MetricDefinition | None:
    return CANONICAL_METRICS.get(metric_key)


def is_known_metric(metric_key: str) -> bool:
    definition = metric_definition(metric_key)
    return definition is not None and definition.value_eligible


def canonical_unit(metric_key: str) -> str | None:
    definition = metric_definition(metric_key)
    return definition.canonical_unit if definition is not None else None


def is_unsupported_metric(metric_key: str) -> bool:
    return metric_key in UNSUPPORTED_METRIC_KEYS or not is_known_metric(metric_key)


def unit_matches(metric_key: str, unit: str | None) -> bool:
    expected = canonical_unit(metric_key)
    return expected is not None and unit == expected
