from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics import calendar_canonical, canonical_serving, canonical_telemetry
from app.analytics.daily_canonical import (
    DailyCanonicalOutcome,
    DailyCanonicalState,
    run_daily_canonical_read,
)
from app.analytics.daily_point_parity import DailyPointParityClassification
from app.models import (
    HealthSample,
    ImportBatch,
    NutritionTarget,
    TrackingQualitySettings,
    User,
)
from app.nutrition.enums import (
    CoverageState,
    LineageState,
    ObservationKind,
    PresenceState,
    ProjectionGranularity,
    ProjectionLineageRole,
    ProjectionStatus,
    ResolutionState,
)
from app.nutrition.models import (
    NutritionIngestionRun,
    NutritionSourceObservation,
)
from app.nutrition.repositories import (
    create_projection,
    create_projection_fact,
    create_projection_lineage,
    set_projection_head,
)
from app.nutrition.resolution.metrics import CANONICAL_NUTRITION_METRICS
from app.schemas import DailyPoint
from app.source_priority.application import create_policy_with_rules
from app.source_priority.contracts import PriorityRuleSpec

USER_ID = UUID("01234567-89ab-cdef-0123-456789abcdef")


def _point(
    local_date: date,
    *,
    calories: Decimal | None = Decimal("1900"),
    tracking_status: str = "complete",
    tracking_score: int = 1,
    tracking_reasons: list[str] | None = None,
    activity_source_type: str | None = None,
    target: Decimal | None = Decimal("2000"),
) -> DailyPoint:
    return DailyPoint(
        date=local_date,
        calories_kcal=calories,
        target_kcal=target,
        maintenance_kcal=Decimal("2200") if target is not None else None,
        deviation_kcal=(calories - target) if calories is not None and target is not None else None,
        activity_mode="off" if target is not None else None,
        activity_source_type=activity_source_type,
        active_energy_kcal=None,
        activity_credit_kcal=Decimal("0"),
        activity_data_status="disabled",
        effective_budget_kcal=target,
        effective_maintenance_kcal=Decimal("2200") if target is not None else None,
        effective_deviation_kcal=(calories - target) if calories is not None and target is not None else None,
        protein_g=Decimal("100") if calories is not None else None,
        carbs_g=Decimal("200") if calories is not None else None,
        fat_g=Decimal("60") if calories is not None else None,
        tracking_status=tracking_status,
        tracking_score=tracking_score,
        tracking_reasons=(tracking_reasons or ["Kalorienwert vorhanden"]),
    )


def _request(
    points: tuple[DailyPoint, ...],
    *,
    enabled: bool = True,
    eligibility: canonical_serving.CanonicalServingEligibility | None = None,
    end: date | None = None,
    user_id: UUID = USER_ID,
) -> canonical_serving.CanonicalServingRequest:
    return canonical_serving.CanonicalServingRequest(
        user_id=user_id,
        start=points[0].date,
        end=end or points[-1].date,
        legacy_points=points,
        enabled=enabled,
        endpoint=canonical_serving.CanonicalServingEndpoint.DAILY,
        eligibility=eligibility or canonical_serving.CanonicalServingEligibility(eligible=True),
    )


def test_request_is_frozen_slotted_and_keeps_tuple_snapshot() -> None:
    point = _point(date(2026, 9, 10))
    request = _request((point,))

    assert isinstance(request.legacy_points, tuple)
    assert request.__slots__
    with pytest.raises(FrozenInstanceError):
        request.user_id = UUID(int=0)  # type: ignore[misc]


def test_disabled_or_ineligible_or_over_31_days_never_opens_canonical_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened = False

    def fail_session() -> object:
        nonlocal opened
        opened = True
        raise AssertionError("canonical session must not open")

    monkeypatch.setattr(canonical_serving, "SessionLocal", fail_session)
    point = _point(date(2026, 9, 10))
    disabled = canonical_serving.serve_canonical(_request((point,), enabled=False))
    ineligible = canonical_serving.serve_canonical(
        _request(
            (point,),
            eligibility=canonical_serving.CanonicalServingEligibility(
                eligible=False,
                reason=canonical_serving.CanonicalServingEligibilityReason.ENDPOINT_FILTER,
            ),
        )
    )
    too_long = _request(
        tuple(_point(date(2026, 9, 1) + timedelta(days=offset)) for offset in range(32)),
        end=date(2026, 10, 2),
    )
    oversized = canonical_serving.serve_canonical(too_long)

    assert disabled.outcome is canonical_serving.CanonicalServingOutcome.LEGACY_INELIGIBLE
    assert ineligible.outcome is canonical_serving.CanonicalServingOutcome.LEGACY_INELIGIBLE
    assert oversized.outcome is canonical_serving.CanonicalServingOutcome.LEGACY_INELIGIBLE
    assert disabled.source is canonical_serving.CanonicalServingSource.LEGACY_SELECTED
    assert not opened


def test_shared_gate_falls_back_when_tracking_activity_or_target_differs_despite_same_calories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local_date = date(2026, 9, 10)
    legacy = _point(local_date)
    canonical = _point(
        local_date,
        tracking_reasons=["Andere Begründung"],
        activity_source_type="apple_health_xml",
        target=Decimal("2100"),
    )

    class Session:
        def __enter__(self) -> Session:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    session = Session()
    monkeypatch.setattr(canonical_serving, "SessionLocal", lambda: session)
    monkeypatch.setattr(
        canonical_serving,
        "compare_daily_point_range",
        lambda *args, **kwargs: SimpleNamespace(
            start=local_date,
            end=local_date,
            days=(SimpleNamespace(comparable=True, classification=DailyPointParityClassification.MATCH),),
            canonical_points=(canonical,),
            canonical_projection_ids=(),
            canonical_projection_ready=(),
            canonical_calorie_usable=(),
            canonical_policy_snapshot=None,
        ),
    )
    monkeypatch.setattr(
        canonical_serving,
        "collapse_daily_canonical_range",
        lambda *args, **kwargs: SimpleNamespace(
            state=DailyCanonicalState.MATCH,
            points=(canonical,),
            reason=None,
        ),
    )

    result = canonical_serving.serve_canonical(_request((legacy,)))

    assert result.source is canonical_serving.CanonicalServingSource.LEGACY_SELECTED
    assert result.outcome is canonical_serving.CanonicalServingOutcome.FALLBACK_PARITY
    assert result.selected_points == (legacy,)


def test_snapshot_race_keeps_original_legacy_points_and_never_commits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local_date = date(2026, 9, 10)
    legacy = _point(local_date, calories=Decimal("1900"))
    newer = _point(local_date, calories=Decimal("1800"))

    class Session:
        commit_count = 0
        rollback_count = 0
        close_count = 0

        def __enter__(self) -> Session:
            return self

        def __exit__(self, *_args: object) -> None:
            self.close_count += 1

        def commit(self) -> None:
            self.commit_count += 1

        def rollback(self) -> None:
            self.rollback_count += 1

    session = Session()
    monkeypatch.setattr(canonical_serving, "SessionLocal", lambda: session)
    monkeypatch.setattr(
        canonical_serving,
        "run_canonical_parity",
        lambda *args, **kwargs: SimpleNamespace(points=(newer,), outcome=canonical_serving.CanonicalServingOutcome.CANONICAL_SERVED),
    )
    monkeypatch.setattr(
        canonical_serving,
        "collapse_daily_canonical_range",
        lambda *args, **kwargs: SimpleNamespace(
            state=DailyCanonicalState.MATCH,
            points=(newer,),
            reason=None,
        ),
    )

    result = canonical_serving.serve_canonical(_request((legacy,)))

    assert result.selected_points == (legacy,)
    assert result.source is canonical_serving.CanonicalServingSource.LEGACY_SELECTED
    assert result.outcome is canonical_serving.CanonicalServingOutcome.FALLBACK_PARITY
    assert session.commit_count == 0
    assert session.close_count == 1


def test_matching_full_snapshot_selects_canonical_without_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    local_date = date(2026, 9, 10)
    point = _point(local_date)

    class Session:
        commit_count = 0
        close_count = 0

        def __enter__(self) -> Session:
            return self

        def __exit__(self, *_args: object) -> None:
            self.close_count += 1

        def commit(self) -> None:
            self.commit_count += 1

    session = Session()
    monkeypatch.setattr(canonical_serving, "SessionLocal", lambda: session)
    monkeypatch.setattr(canonical_serving, "run_canonical_parity", lambda *args, **kwargs: object())
    monkeypatch.setattr(
        canonical_serving,
        "collapse_daily_canonical_range",
        lambda *args, **kwargs: SimpleNamespace(
            state=DailyCanonicalState.MATCH,
            points=(point,),
            reason=None,
        ),
    )

    result = canonical_serving.serve_canonical(_request((point,)))

    assert result.source is canonical_serving.CanonicalServingSource.CANONICAL_SELECTED
    assert result.outcome is canonical_serving.CanonicalServingOutcome.CANONICAL_SERVED
    assert result.selected_points == (point,)
    assert session.commit_count == 0
    assert session.close_count == 1


def test_exception_rolls_back_closes_and_fails_open(monkeypatch: pytest.MonkeyPatch) -> None:
    point = _point(date(2026, 9, 10))

    class Session:
        rollback_count = 0
        close_count = 0

        def __enter__(self) -> Session:
            return self

        def __exit__(self, *_args: object) -> None:
            self.close_count += 1

        def rollback(self) -> None:
            self.rollback_count += 1

    session = Session()
    monkeypatch.setattr(canonical_serving, "SessionLocal", lambda: session)

    def explode(*args: object, **kwargs: object) -> object:
        raise RuntimeError("synthetic canonical failure")

    monkeypatch.setattr(canonical_serving, "run_canonical_parity", explode)

    result = canonical_serving.serve_canonical(_request((point,)))

    assert result.source is canonical_serving.CanonicalServingSource.LEGACY_SELECTED
    assert result.outcome is canonical_serving.CanonicalServingOutcome.FALLBACK_ERROR
    assert result.selected_points == (point,)
    assert result.exception_class == "RuntimeError"
    assert session.rollback_count == 1
    assert session.close_count == 1
def test_eligibility_reasons_are_closed_typed_values() -> None:
    reason = canonical_serving.CanonicalServingEligibilityReason.RANGE_TOO_LARGE
    eligibility = canonical_serving.CanonicalServingEligibility(
        eligible=False,
        reason=reason,
    )

    assert eligibility.reason is reason
    with pytest.raises(TypeError):
        canonical_serving.CanonicalServingEligibility(
            eligible=False,
            reason="range_too_large",
        )


def _seed_real_serving_scope(
    db: Session,
    user: User,
    *,
    local_date: date,
    calories: Decimal,
    suffix: str,
) -> None:
    values = {
        "dietary_energy_kcal": calories,
        "protein_g": Decimal("100"),
        "carbohydrates_g": Decimal("200"),
        "fat_g": Decimal("60"),
        "fiber_g": Decimal("30"),
        "sugar_g": Decimal("50"),
        "saturated_fat_g": Decimal("20"),
    }
    target = db.scalar(
        select(NutritionTarget).where(
            NutritionTarget.user_id == user.id,
            NutritionTarget.valid_from <= local_date,
        )
    )
    assert target is not None
    target.maintenance_kcal = Decimal("2200")
    target.activity_mode = "off"
    target.activity_source_type = None

    policy = create_policy_with_rules(
        db,
        user.id,
        version=1,
        effective_from=datetime(2020, 1, 1, tzinfo=UTC),
        rules=(PriorityRuleSpec("nutrition", None, "yazio", 1),),
    )
    batch = ImportBatch(
        user_id=user.id,
        source_type="yazio_export_v1",
        status="completed",
    )
    db.add(batch)
    db.flush()
    sample_at = datetime.combine(local_date, datetime.min.time(), tzinfo=UTC)
    db.add_all(
        HealthSample(
            user_id=user.id,
            import_batch_id=batch.id,
            external_sample_id=f"{suffix}-{metric_key}",
            fingerprint=f"{suffix}-{metric_key}".ljust(64, "0")[:64],
            source_type="yazio_export_v1",
            source_name="canonical-serving-test",
            source_identifier=f"{suffix}-legacy",
            metric_type=metric_key,
            value=value,
            unit=CANONICAL_NUTRITION_METRICS[metric_key].canonical_unit,
            original_value=value,
            original_unit=CANONICAL_NUTRITION_METRICS[metric_key].canonical_unit,
            start_at=sample_at,
            end_at=sample_at,
            local_date=local_date,
            timezone="UTC",
        )
        for metric_key, value in values.items()
    )
    run = NutritionIngestionRun(
        user_id=user.id,
        provider_key="yazio",
        source_instance_id=uuid4(),
        status="completed",
        coverage_state=CoverageState.COMPLETE.value,
        requested_start_date=local_date,
        requested_end_date=local_date,
        covered_start_date=local_date,
        covered_end_date=local_date,
    )
    db.add(run)
    db.flush()
    observation = NutritionSourceObservation(
        user_id=user.id,
        ingestion_run_id=run.id,
        provider_key="yazio",
        source_instance_id=run.source_instance_id,
        observation_kind=ObservationKind.DAILY_SUMMARY.value,
        source_namespace="canonical-serving-test",
        source_record_id=f"{suffix}-summary",
        source_revision=1,
        observation_fingerprint=f"{suffix}-observation".ljust(64, "0")[:64],
        local_date=local_date,
        presence_state=PresenceState.SUPPLIED.value,
        coverage_state=CoverageState.COMPLETE.value,
        resolution_state=ResolutionState.RESOLVED.value,
        lineage_state=LineageState.CONFIRMED.value,
    )
    db.add(observation)
    db.flush()
    projection = create_projection(
        db,
        user_id=user.id,
        local_date=local_date,
        projection_version=1,
        projection_algorithm_version="nutrition-daily-v1",
        priority_policy_id=policy.policy_id,
        input_watermark=f"{suffix}-watermark",
        projection_status=ProjectionStatus.READY.value,
    )
    for metric_key, value in values.items():
        fact = create_projection_fact(
            db,
            user_id=user.id,
            projection_id=projection.id,
            metric_key=metric_key,
            value=value,
            unit=CANONICAL_NUTRITION_METRICS[metric_key].canonical_unit,
            selected_provider_key="yazio",
            selected_granularity=ProjectionGranularity.SUMMARY.value,
            presence_state=PresenceState.SUPPLIED.value,
            coverage_state=CoverageState.COMPLETE.value,
            resolution_state=ResolutionState.RESOLVED.value,
            lineage_state=LineageState.CONFIRMED.value,
            diagnostic_metadata={"registry": "nutrition-metrics-v1"},
        )
        create_projection_lineage(
            db,
            user_id=user.id,
            projection_fact_id=fact.id,
            source_observation_id=observation.id,
            provider_key="yazio",
            role=ProjectionLineageRole.SELECTED.value,
            granularity=ProjectionGranularity.SUMMARY.value,
            contribution_value=value,
            presence_state=PresenceState.SUPPLIED.value,
            coverage_state=CoverageState.COMPLETE.value,
            metadata={"source": suffix},
        )
    set_projection_head(db, user.id, local_date, projection.id)


def test_shared_serving_is_user_scoped_for_two_users(
    db: Session,
    user: User,
) -> None:
    local_date = date(2026, 9, 10)
    other_user = User(
        username="shared-serving-other",
        password_hash="synthetic-password-hash",
        timezone="UTC",
    )
    db.add(other_user)
    db.flush()
    db.add(TrackingQualitySettings(user_id=other_user.id))
    db.add(
        NutritionTarget(
            user_id=other_user.id,
            valid_from=date(2024, 1, 1),
            calories_kcal=Decimal("2000"),
            protein_g=Decimal("100"),
        )
    )
    db.flush()

    point_a = _point(local_date, calories=Decimal("1800"))
    point_b = _point(local_date, calories=Decimal("2100"))
    _seed_real_serving_scope(
        db,
        user,
        local_date=local_date,
        calories=Decimal("1800"),
        suffix="scope-a",
    )
    _seed_real_serving_scope(
        db,
        other_user,
        local_date=local_date,
        calories=Decimal("2100"),
        suffix="scope-b",
    )
    db.commit()

    result_a = canonical_serving.serve_canonical(
        _request((point_a,), user_id=user.id),
    )
    result_b = canonical_serving.serve_canonical(
        _request((point_b,), user_id=other_user.id),
    )

    assert result_a.source is canonical_serving.CanonicalServingSource.CANONICAL_SELECTED
    assert result_b.source is canonical_serving.CanonicalServingSource.CANONICAL_SELECTED
    assert result_a.selected_points == (point_a,)
    assert result_b.selected_points == (point_b,)
    assert result_a.selected_points != (point_b,)
    assert result_b.selected_points != (point_a,)


def test_shared_serving_rejects_partial_canonical_range_without_mixing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dates = (date(2026, 9, 10), date(2026, 9, 11))
    legacy_points = tuple(_point(local_date) for local_date in dates)
    canonical_points = (legacy_points[0], _point(dates[1], calories=Decimal("1800")))

    class Session:
        def __enter__(self) -> Session:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    monkeypatch.setattr(canonical_serving, "SessionLocal", Session)
    monkeypatch.setattr(canonical_serving, "run_canonical_parity", lambda *args, **kwargs: object())
    monkeypatch.setattr(
        canonical_serving,
        "collapse_daily_canonical_range",
        lambda *args, **kwargs: SimpleNamespace(
            state=DailyCanonicalState.MATCH,
            points=canonical_points,
            reason=None,
        ),
    )

    result = canonical_serving.serve_canonical(_request(legacy_points))

    assert result.source is canonical_serving.CanonicalServingSource.LEGACY_SELECTED
    assert result.outcome is canonical_serving.CanonicalServingOutcome.FALLBACK_PARITY
    assert result.selected_points == legacy_points


def test_shared_snapshot_telemetry_redacts_user_and_date(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    point = _point(date(2026, 9, 10))
    monkeypatch.setattr(
        canonical_serving,
        "serve_canonical",
        lambda request: canonical_serving.CanonicalServingResult(
            selected_points=request.legacy_points,
            source=canonical_serving.CanonicalServingSource.CANONICAL_SELECTED,
            outcome=canonical_serving.CanonicalServingOutcome.CANONICAL_SERVED,
        ),
    )

    with caplog.at_level("INFO", logger="app.analytics.daily_canonical"):
        result = run_daily_canonical_read(
            USER_ID,
            point.date,
            point.date,
            None,
            None,
            None,
            enabled=True,
            max_days=31,
            legacy_points=(point,),
        )

    payload = json.loads(caplog.records[-1].getMessage())
    assert result.state is DailyCanonicalState.MATCH
    assert payload["event"] == "analytics.daily.canonical"
    assert payload["version"] == "d4b.v1"
    assert payload["outcome"] == "canonical_served"
    assert set(payload) == {"event", "version", "outcome", "range_bucket", "duration_bucket"}
    assert str(USER_ID) not in caplog.records[-1].getMessage()
    assert point.date.isoformat() not in caplog.records[-1].getMessage()


def test_direct_daily_read_without_snapshot_runs_legacy_parity_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    point = _point(date(2026, 9, 10))
    calls: list[dict[str, object]] = []

    class Session:
        def __enter__(self) -> Session:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    def compare(*args: object, **kwargs: object) -> SimpleNamespace:
        del args
        calls.append(kwargs)
        return SimpleNamespace()

    monkeypatch.setattr("app.analytics.daily_canonical.SessionLocal", Session)
    monkeypatch.setattr("app.analytics.daily_canonical.compare_daily_point_range", compare)
    monkeypatch.setattr(
        "app.analytics.daily_canonical.collapse_daily_canonical_range",
        lambda *args, **kwargs: DailyCanonicalOutcome(
            DailyCanonicalState.MATCH,
            points=(point,),
        ),
    )

    result = run_daily_canonical_read(
        USER_ID,
        point.date,
        point.date,
        None,
        None,
        None,
        enabled=True,
        max_days=31,
        emit_telemetry=False,
    )

    assert result.state is DailyCanonicalState.MATCH
    assert len(calls) == 1
    assert "legacy_points" not in calls[0]

def test_canonical_telemetry_primitives_keep_all_buckets() -> None:
    assert canonical_telemetry._range_bucket(date(2026, 9, 10), date(2026, 9, 9)) == "invalid"
    assert canonical_telemetry._range_bucket(date(2026, 9, 10), date(2026, 9, 10)) == "1"
    assert canonical_telemetry._range_bucket(date(2026, 9, 10), date(2026, 9, 16)) == "2-7"
    assert canonical_telemetry._range_bucket(date(2026, 9, 10), date(2026, 9, 17)) == "8-31"
    assert canonical_telemetry._range_bucket(date(2026, 9, 10), date(2026, 10, 16)) == "32-366"
    assert canonical_telemetry._range_bucket(date(2026, 1, 1), date(2027, 1, 2)) == "367+"

    assert canonical_telemetry._duration_bucket(0.009) == "<10ms"
    assert canonical_telemetry._duration_bucket(0.01) == "10-49ms"
    assert canonical_telemetry._duration_bucket(0.05) == "50-199ms"
    assert canonical_telemetry._duration_bucket(0.2) == "200ms+"

    exception_type = type("E" * 80, (Exception,), {})
    assert canonical_telemetry._exception_class(exception_type()) == "E" * 64


def test_daily_and_calendar_telemetry_use_shared_primitives(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls: list[str] = []

    def range_bucket(*args: object) -> str:
        del args
        calls.append("range")
        return "shared-range"

    def duration_bucket(*args: object) -> str:
        del args
        calls.append("duration")
        return "shared-duration"

    monkeypatch.setattr(canonical_telemetry, "_range_bucket", range_bucket)
    monkeypatch.setattr(canonical_telemetry, "_duration_bucket", duration_bucket)
    start = date(2026, 1, 1)
    end = start + timedelta(days=366)
    monkeypatch.setattr(
        calendar_canonical,
        "run_daily_canonical_read",
        lambda *args, **kwargs: DailyCanonicalOutcome(DailyCanonicalState.ERROR),
    )

    with caplog.at_level("INFO"):
        run_daily_canonical_read(
            USER_ID,
            start,
            end,
            "filtered",
            None,
            None,
            enabled=True,
            max_days=31,
        )
        calendar_canonical.run_calendar_canonical_read(USER_ID, start, end)

    payloads = [
        json.loads(record.getMessage())
        for record in caplog.records
        if record.name == "app.analytics.daily_canonical"
        or record.name == "app.analytics.calendar_canonical"
    ]
    assert [payload["event"] for payload in payloads] == [
        "analytics.daily.canonical",
        "analytics.calendar.canonical",
    ]
    assert all(payload["range_bucket"] == "shared-range" for payload in payloads)
    assert all(payload["duration_bucket"] == "shared-duration" for payload in payloads)
    assert calls == ["range", "duration", "range", "duration"]
def test_shared_fallback_preserves_bounded_exception_class(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    point = _point(date(2026, 9, 10))

    class Session:
        def __enter__(self) -> Session:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    class ParityFailure(Exception):
        pass

    monkeypatch.setattr(canonical_serving, "SessionLocal", Session)

    def fail_parity(*args: object, **kwargs: object) -> object:
        raise ParityFailure("must not escape")

    result = canonical_serving.serve_canonical(
        _request((point,)),
        parity_runner=fail_parity,
    )

    assert result.outcome is canonical_serving.CanonicalServingOutcome.FALLBACK_ERROR
    assert result.exception_class == "ParityFailure"
    with pytest.raises(ValueError):
        canonical_serving.CanonicalServingResult(
            selected_points=(point,),
            source=canonical_serving.CanonicalServingSource.LEGACY_SELECTED,
            outcome=canonical_serving.CanonicalServingOutcome.FALLBACK_ERROR,
            exception_class="x" * 65,
        )


def test_daily_and_calendar_preserve_shared_exception_class_in_telemetry(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    point = _point(date(2026, 9, 10))
    caplog.set_level("INFO")
    shared_result = canonical_serving.CanonicalServingResult(
        selected_points=(point,),
        source=canonical_serving.CanonicalServingSource.LEGACY_SELECTED,
        outcome=canonical_serving.CanonicalServingOutcome.FALLBACK_ERROR,
        detail=canonical_serving.CanonicalServingDetail.ERROR,
        exception_class="SharedFailure",
    )
    monkeypatch.setattr(canonical_serving, "serve_canonical", lambda request: shared_result)
    daily_outcome = run_daily_canonical_read(
        USER_ID,
        point.date,
        point.date,
        None,
        None,
        None,
        enabled=True,
        max_days=31,
        legacy_points=(point,),
    )
    daily_payload = json.loads(caplog.records[-1].getMessage())
    monkeypatch.setattr(
        calendar_canonical,
        "serve_canonical",
        lambda request: shared_result,
    )
    calendar_outcome = calendar_canonical.run_calendar_canonical_read(
        USER_ID,
        point.date,
        point.date,
        legacy_points=(point,),
    )
    calendar_payload = json.loads(caplog.records[-1].getMessage())

    assert daily_outcome.state is DailyCanonicalState.ERROR
    assert daily_outcome.exception_class == "SharedFailure"
    assert calendar_outcome.state is DailyCanonicalState.ERROR
    assert calendar_outcome.exception_class == "SharedFailure"
    assert daily_payload["exception_class"] == "SharedFailure"
    assert calendar_payload["exception_class"] == "SharedFailure"


def test_canonical_endpoint_kind_is_not_an_exported_alias() -> None:
    assert "CanonicalServingEndpointKind" not in canonical_serving.__all__

def test_canonical_serving_wildcard_export_contract() -> None:
    namespace: dict[str, object] = {}
    exec("from app.analytics.canonical_serving import *", namespace)

    assert set(canonical_serving.__all__) <= set(namespace)