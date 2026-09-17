from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.analytics import micronutrient_shadow
from app.analytics.micronutrient_shadow import read_canonical_micronutrient_period
from app.micronutrients import MICRONUTRIENT_METRIC_TYPES
from app.nutrition.enums import (
    CoverageState,
    LineageState,
    PresenceState,
    ProjectionGranularity,
    ResolutionState,
)
from app.nutrition.resolution import (
    CANONICAL_NUTRITION_METRICS,
    MetricContribution,
    ProviderCandidate,
    canonical_unit,
    resolve_provider_period,
)
from app.nutrition.resolution.reasons import EvidenceKind, ReasonCode


class _PeriodResolver:
    provider_key = "test"

    def resolve_metric(self, *args, **kwargs):
        raise AssertionError("period reader must not resolve one metric at a time")

    def resolve_period(self, db, *, user_id, source_instance_id, start, end, metric_keys):
        del db
        return {
            current: {
                metric_key: _candidate(
                    user_id=user_id,
                    local_date=current,
                    metric_key=metric_key,
                    value=(
                        Decimal("1800")
                        if metric_key == "dietary_energy_kcal" and current == start
                        else Decimal("0")
                        if metric_key == "iron_mg" and current == start
                        else None
                    ),
                )
                for metric_key in metric_keys
            }
            for current in (start + timedelta(days=offset) for offset in range((end - start).days + 1))
        }


def _candidate(*, user_id: UUID, local_date: date, metric_key: str, value: Decimal | None) -> ProviderCandidate:
    unit = canonical_unit(metric_key) if value is not None else None
    presence_state = (
        PresenceState.MISSING
        if value is None
        else PresenceState.EXPLICIT_ZERO
        if value == Decimal("0")
        else PresenceState.SUPPLIED
    )
    lineage = (
        (
            MetricContribution(
                evidence_id=uuid4(),
                source_observation_id=uuid4(),
                metric_key=metric_key,
                value=value,
                unit=unit,
                presence_state=presence_state,
                resolution_state=ResolutionState.RESOLVED,
                lineage_state=LineageState.CONFIRMED,
                evidence_kind=EvidenceKind.EVENT,
            ),
        )
        if value is not None
        else ()
    )
    return ProviderCandidate(
        provider_key="test",
        user_id=user_id,
        local_date=local_date,
        metric_key=metric_key,
        value=value,
        unit=unit,
        selected_granularity=ProjectionGranularity.EVENT if value is not None else None,
        presence_state=presence_state,
        coverage_state=CoverageState.COMPLETE if value is not None else CoverageState.UNKNOWN,
        resolution_state=ResolutionState.RESOLVED if value is not None else ResolutionState.UNRESOLVED,
        lineage_state=LineageState.CONFIRMED if value is not None else LineageState.UNKNOWN,
        source_lineage=lineage,
        reason_code=ReasonCode.EVENT_COMPLETE_CONFIRMED if value is not None else ReasonCode.ALL_SOURCES_MISSING,
    )


def test_period_reader_uses_one_bounded_provider_read_and_validates_scope() -> None:
    resolver = _PeriodResolver()
    user_id = uuid4()
    source_instance_id = uuid4()
    start = date(2026, 9, 1)
    end = date(2026, 9, 2)

    result = resolve_provider_period(
        object(),
        provider_key="test",
        user_id=user_id,
        source_instance_id=source_instance_id,
        start=start,
        end=end,
        metric_keys=tuple(CANONICAL_NUTRITION_METRICS),
        resolver_registry={"test": resolver},
    )

    assert tuple(result) == (start, end)
    assert set(result[start]) == set(CANONICAL_NUTRITION_METRICS)
    assert all(candidate.user_id == user_id for candidate in result[start].values())
    assert all(candidate.local_date == start for candidate in result[start].values())


def test_period_reader_rejects_ranges_longer_than_31_days() -> None:
    with pytest.raises(ValueError, match="31"):
        resolve_provider_period(
            object(),
            provider_key="test",
            user_id=uuid4(),
            source_instance_id=uuid4(),
            start=date(2026, 1, 1),
            end=date(2026, 2, 1),
            metric_keys=tuple(CANONICAL_NUTRITION_METRICS),
            resolver_registry={"test": _PeriodResolver()},
        )


def test_period_reader_accepts_explicit_larger_bound_for_base_analytics() -> None:
    start = date(2026, 1, 1)
    end = date(2026, 2, 1)

    result = resolve_provider_period(
        object(),
        provider_key="test",
        user_id=uuid4(),
        source_instance_id=uuid4(),
        start=start,
        end=end,
        metric_keys=tuple(CANONICAL_NUTRITION_METRICS),
        resolver_registry={"test": _PeriodResolver()},
        max_days=32,
    )

    assert tuple(result) == tuple(start + timedelta(days=offset) for offset in range(32))


def test_canonical_period_aggregates_bounded_read_without_metric_loop(monkeypatch, user) -> None:
    calls = []

    def fake_period(db, **kwargs):
        del db
        calls.append(kwargs)
        return _PeriodResolver().resolve_period(
            None,
            user_id=kwargs["user_id"],
            source_instance_id=kwargs["source_instance_id"],
            start=kwargs["start"],
            end=kwargs["end"],
            metric_keys=kwargs["metric_keys"],
        )

    monkeypatch.setattr(micronutrient_shadow, "resolve_provider_period", fake_period)

    result = read_canonical_micronutrient_period(
        db=object(),
        user_id=user.id,
        provider_key="test",
        source_instance_id=user.id,
        start=date(2026, 9, 1),
        end=date(2026, 9, 2),
    )

    iron = next(item for item in result.nutrients if item.metric_type == "iron_mg")
    assert len(calls) == 1
    assert calls[0]["start"] == date(2026, 9, 1)
    assert calls[0]["end"] == date(2026, 9, 2)
    assert set(calls[0]["metric_keys"]) == set(CANONICAL_NUTRITION_METRICS)
    assert result.recorded_days == 1
    assert iron.total == Decimal("0")
    assert iron.days_with_value == 1
    assert set(item.metric_type for item in result.nutrients) == set(MICRONUTRIENT_METRIC_TYPES)
