from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from hashlib import sha256
from uuid import UUID, uuid4, uuid5

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.analytics import nutrition_micronutrients
from app.analytics.provider_selection import NutritionProviderSelection
from app.micronutrients import MICRONUTRIENTS
from app.models import User, YazioConnection
from app.nutrition.enums import (
    ConsumptionEventKind,
    CoverageState,
    LineageState,
    ObservationKind,
    ObservationRole,
    PresenceState,
    ResolutionState,
)
from app.nutrition.models import (
    NutritionConsumptionEvent,
    NutritionFieldObservation,
    NutritionFoodProfile,
    NutritionFoodSnapshot,
    NutritionIngestionRun,
    NutritionSourceObservation,
)
from app.nutrition.resolution import (
    DAILY_PROJECTION_METRICS,
    EvidenceKind,
    MetricContribution,
    ProviderCandidate,
    ReasonCode,
)
from app.nutrition.resolution.metrics import canonical_unit

DAY = date(2026, 1, 1)
USER_ID = UUID(int=101)
SOURCE_A = UUID(int=201)
SOURCE_B = UUID(int=202)


def synthetic_matrix_365(
    provider_key: str,
    *,
    local_date: date,
    user_id: UUID = USER_ID,
    source_instance_id: UUID | None = None,
    macro_evidence: bool = True,
    macro_coverage: CoverageState = CoverageState.COMPLETE,
    nutrient_value: Decimal | None = None,
    nutrient_presence: PresenceState = PresenceState.SUPPLIED,
    nutrient_coverage: CoverageState = CoverageState.COMPLETE,
    nutrient_resolution: ResolutionState = ResolutionState.RESOLVED,
) -> dict[str, ProviderCandidate]:
    source_scope = source_instance_id or (
        SOURCE_A if provider_key == "google_health" else SOURCE_B
    )
    matrix: dict[str, ProviderCandidate] = {}
    metric_keys = (
        *DAILY_PROJECTION_METRICS,
        *(nutrient.metric_type for nutrient in MICRONUTRIENTS),
    )
    for metric_key in metric_keys:
        is_macro = metric_key in DAILY_PROJECTION_METRICS
        value = (
            Decimal("1")
            if is_macro and macro_evidence
            else None
            if is_macro
            else nutrient_value
        )
        presence_state = (
            PresenceState.SUPPLIED
            if value is not None and value != Decimal("0")
            else PresenceState.EXPLICIT_ZERO
            if value == Decimal("0")
            else PresenceState.MISSING
        )
        if not is_macro and nutrient_value is not None:
            presence_state = nutrient_presence
        coverage_state = macro_coverage if is_macro else nutrient_coverage
        resolution_state = (
            ResolutionState.RESOLVED if is_macro else nutrient_resolution
        )
        lineage_state = (
            LineageState.CONFIRMED if value is not None else LineageState.UNKNOWN
        )
        unit = canonical_unit(metric_key)
        source_observation_id = uuid5(
            source_scope, f"{user_id}:{local_date.isoformat()}:source"
        )
        source_lineage: tuple[MetricContribution, ...] = ()
        if value is not None:
            source_lineage_items = [
                MetricContribution(
                    evidence_id=uuid5(
                        source_scope,
                        f"{user_id}:{local_date.isoformat()}:{metric_key}:evidence",
                    ),
                    source_observation_id=source_observation_id,
                    metric_key=metric_key,
                    value=value,
                    unit=unit,
                    presence_state=presence_state,
                    resolution_state=resolution_state,
                    lineage_state=lineage_state,
                    evidence_kind=EvidenceKind.FIELD_OBSERVATION,
                )
            ]
            if coverage_state is CoverageState.PARTIAL:
                source_lineage_items.append(
                    MetricContribution(
                        evidence_id=uuid5(
                            source_scope,
                            f"{user_id}:{local_date.isoformat()}:{metric_key}:missing",
                        ),
                        source_observation_id=source_observation_id,
                        metric_key=metric_key,
                        value=None,
                        unit=None,
                        presence_state=PresenceState.MISSING,
                        resolution_state=resolution_state,
                        lineage_state=LineageState.UNKNOWN,
                        evidence_kind=EvidenceKind.FIELD_OBSERVATION,
                    )
                )
            source_lineage = tuple(source_lineage_items)
        matrix[metric_key] = ProviderCandidate(
            provider_key=provider_key,
            user_id=user_id,
            local_date=local_date,
            metric_key=metric_key,
            value=value,
            unit=unit,
            selected_granularity=None,
            presence_state=presence_state,
            coverage_state=coverage_state,
            resolution_state=resolution_state,
            lineage_state=lineage_state,
            source_lineage=source_lineage,
            reason_code=(
                ReasonCode.EVENT_COMPLETE_CONFIRMED
                if value is not None
                else ReasonCode.ALL_SOURCES_MISSING
            ),
        )
    return matrix


def _selection(
    provider_sources: tuple[tuple[str, UUID], ...],
) -> NutritionProviderSelection:
    provider_key, source_instance_id = provider_sources[0]
    return NutritionProviderSelection(
        provider_key, source_instance_id, provider_sources
    )


def _add_yazio_source_observation(
    db: Session,
    *,
    user_id: UUID,
    source_instance_id: UUID,
    source_namespace: str,
    observation_kind: ObservationKind,
    local_date: date | None,
) -> NutritionSourceObservation:
    run = NutritionIngestionRun(
        user_id=user_id,
        provider_key="yazio",
        source_instance_id=source_instance_id,
        requested_start_date=local_date,
        requested_end_date=local_date,
        covered_start_date=local_date,
        covered_end_date=local_date,
        status="completed",
        coverage_state=CoverageState.COMPLETE.value,
    )
    db.add(run)
    db.flush()

    source_id = uuid4()
    source = NutritionSourceObservation(
        id=source_id,
        user_id=user_id,
        ingestion_run_id=run.id,
        provider_key="yazio",
        source_instance_id=source_instance_id,
        observation_kind=observation_kind.value,
        source_namespace=source_namespace,
        source_record_id=f"analysis-365-{uuid4()}",
        source_revision=1,
        observation_fingerprint=sha256(source_id.bytes).hexdigest(),
        local_date=local_date,
        presence_state=PresenceState.SUPPLIED.value,
        coverage_state=CoverageState.COMPLETE.value,
        resolution_state=ResolutionState.RESOLVED.value,
        lineage_state=LineageState.CONFIRMED.value,
    )
    db.add(source)
    db.flush()
    return source


def _add_yazio_product_event(
    db: Session,
    *,
    user_id: UUID,
    source_instance_id: UUID,
    local_date: date,
    vitamin_a_value: Decimal,
) -> None:
    profile = NutritionFoodProfile(
        user_id=user_id,
        provider_key="yazio",
        source_instance_id=source_instance_id,
    )
    db.add(profile)
    db.flush()
    profile_source = _add_yazio_source_observation(
        db,
        user_id=user_id,
        source_instance_id=source_instance_id,
        source_namespace="yazio.product",
        observation_kind=ObservationKind.PRODUCT,
        local_date=None,
    )
    snapshot = NutritionFoodSnapshot(
        user_id=user_id,
        food_profile_id=profile.id,
        source_observation_id=profile_source.id,
        content_hash=sha256(profile_source.id.bytes).hexdigest(),
        name="synthetic product",
        base_unit="serving",
    )
    db.add(snapshot)
    db.flush()

    values = {
        **{metric_key: Decimal("1") for metric_key in DAILY_PROJECTION_METRICS},
        "vitamin_a_ug": vitamin_a_value,
    }
    parent_fields: dict[str, NutritionFieldObservation] = {}
    for metric_key, value in values.items():
        unit = canonical_unit(metric_key)
        field = NutritionFieldObservation(
            user_id=user_id,
            source_observation_id=profile_source.id,
            provider_field_path=f"nutrition.{metric_key}",
            provider_raw_value_decimal=value,
            provider_raw_unit=unit,
            metric_key=metric_key,
            canonical_value=value,
            canonical_unit=unit,
            observation_role=ObservationRole.PROVIDER.value,
            presence_state=(
                PresenceState.EXPLICIT_ZERO.value
                if value == Decimal("0")
                else PresenceState.SUPPLIED.value
            ),
            coverage_state=CoverageState.COMPLETE.value,
            resolution_state=ResolutionState.RESOLVED.value,
            lineage_state=LineageState.CONFIRMED.value,
        )
        db.add(field)
        parent_fields[metric_key] = field
    db.flush()

    event_source = _add_yazio_source_observation(
        db,
        user_id=user_id,
        source_instance_id=source_instance_id,
        source_namespace="yazio.consumed_item",
        observation_kind=ObservationKind.CONSUMPTION_EVENT,
        local_date=local_date,
    )
    event = NutritionConsumptionEvent(
        user_id=user_id,
        source_observation_id=event_source.id,
        provider_key="yazio",
        source_instance_id=source_instance_id,
        event_kind=ConsumptionEventKind.PRODUCT.value,
        food_snapshot_id=snapshot.id,
        revision=1,
        local_date=local_date,
        amount=Decimal("1"),
        amount_unit="serving",
        presence_state=PresenceState.SUPPLIED.value,
        coverage_state=CoverageState.COMPLETE.value,
        resolution_state=ResolutionState.RESOLVED.value,
        lineage_state=LineageState.CONFIRMED.value,
    )
    db.add(event)
    db.flush()

    for metric_key, value in values.items():
        unit = canonical_unit(metric_key)
        db.add(
            NutritionFieldObservation(
                user_id=user_id,
                source_observation_id=event_source.id,
                provider_field_path=f"nutrition.{metric_key}",
                provider_raw_value_decimal=value,
                provider_raw_unit=unit,
                metric_key=metric_key,
                canonical_value=value,
                canonical_unit=unit,
                observation_role=ObservationRole.DERIVED.value,
                derived_from_field_observation_id=parent_fields[metric_key].id,
                presence_state=(
                    PresenceState.EXPLICIT_ZERO.value
                    if value == Decimal("0")
                    else PresenceState.SUPPLIED.value
                ),
                coverage_state=CoverageState.COMPLETE.value,
                resolution_state=ResolutionState.RESOLVED.value,
                lineage_state=LineageState.CONFIRMED.value,
            )
        )
    db.flush()


@pytest.mark.parametrize("days", [1, 31, 32, 90, 180, 365])
@pytest.mark.parametrize("metric_key", [None, "vitamin_a_ug"])
def test_analysis_reader_chunks_full_inclusive_range(
    monkeypatch: pytest.MonkeyPatch, days: int, metric_key: str | None
) -> None:
    calls: list[tuple[str, date, date, UUID, UUID]] = []
    selection = _selection((("yazio", SOURCE_B),))

    def fake_period(
        _db: object,
        *,
        provider_key: str,
        user_id: UUID,
        source_instance_id: UUID,
        start: date,
        end: date,
        metric_keys: tuple[str, ...],
    ) -> dict[date, dict[str, ProviderCandidate]]:
        calls.append((provider_key, start, end, user_id, source_instance_id))
        assert 1 <= (end - start).days + 1 <= 31
        return {
            current: {
                key: synthetic_matrix_365(
                    provider_key,
                    local_date=current,
                    user_id=user_id,
                    source_instance_id=source_instance_id,
                    nutrient_value=Decimal("0.1"),
                    nutrient_presence=PresenceState.SUPPLIED,
                )[key]
                for key in metric_keys
            }
            for current in (
                start + timedelta(days=offset)
                for offset in range((end - start).days + 1)
            )
        }

    monkeypatch.setattr(nutrition_micronutrients, "resolve_provider_period", fake_period)
    last = DAY + timedelta(days=days - 1)
    result = nutrition_micronutrients.read_analysis_micronutrients(
        object(),
        user_id=USER_ID,
        selection=selection,
        start=DAY,
        end=last,
        metric_key=metric_key,
    )

    expected_chunks = [
        (DAY + timedelta(days=offset), DAY + timedelta(days=min(offset + 30, days - 1)))
        for offset in range(0, days, 31)
    ]
    assert [(start, end) for _, start, end, _, _ in calls] == expected_chunks
    assert all(
        (provider, user_id, source_id) == ("yazio", USER_ID, SOURCE_B)
        for provider, _, _, user_id, source_id in calls
    )
    assert result.period_days == days
    assert result.start_date == DAY and result.end_date == last
    for metric in result.metrics:
        assert metric.total == Decimal("0.1") * days
        assert metric.days_with_data == days
        assert [(item.provider_key, item.days_with_data) for item in metric.contributors] == [
            ("yazio", days)
        ]
    assert result.selected_provider.provider_key == "yazio"
    assert result.period_capabilities.supports_all_data is False
    assert result.period_capabilities.all_data_start_date is None
    if metric_key:
        assert [point.date for point in result.series] == [
            DAY + timedelta(days=offset) for offset in range(days)
        ]
        assert [point.value for point in result.series] == [Decimal("0.1")] * days
    else:
        assert result.series == []
        assert len(result.metrics) == len(MICRONUTRIENTS)


@pytest.mark.parametrize("fallback_value", [None, Decimal("5"), Decimal("0")])
def test_analysis_reader_preserves_day_winner_across_chunks(
    monkeypatch: pytest.MonkeyPatch, fallback_value: Decimal | None
) -> None:
    calls: list[tuple[str, date, date]] = []
    selection = _selection(
        (("google_health", SOURCE_A), ("yazio", SOURCE_B))
    )
    last = DAY + timedelta(days=31)

    def fake_period(
        _db: object,
        *,
        provider_key: str,
        user_id: UUID,
        source_instance_id: UUID,
        start: date,
        end: date,
        metric_keys: tuple[str, ...],
    ) -> dict[date, dict[str, ProviderCandidate]]:
        assert user_id == USER_ID
        assert source_instance_id == (
            SOURCE_A if provider_key == "google_health" else SOURCE_B
        )
        assert 1 <= (end - start).days + 1 <= 31
        calls.append((provider_key, start, end))
        rows: dict[date, dict[str, ProviderCandidate]] = {}
        for offset in range((end - start).days + 1):
            current = start + timedelta(days=offset)
            absolute_offset = (current - DAY).days
            if provider_key == "google_health" and absolute_offset < 31:
                matrix = synthetic_matrix_365(
                    provider_key,
                    local_date=current,
                    user_id=user_id,
                    source_instance_id=source_instance_id,
                    nutrient_value=Decimal("1"),
                    nutrient_presence=PresenceState.SUPPLIED,
                    nutrient_coverage=(
                        CoverageState.PARTIAL
                        if absolute_offset == 30
                        else CoverageState.COMPLETE
                    ),
                )
            elif provider_key == "google_health":
                matrix = synthetic_matrix_365(
                    provider_key,
                    local_date=current,
                    user_id=user_id,
                    source_instance_id=source_instance_id,
                    macro_evidence=fallback_value is None,
                    macro_coverage=(
                        CoverageState.PARTIAL
                        if fallback_value is None
                        else CoverageState.COMPLETE
                    ),
                    nutrient_value=(
                        Decimal("9") if fallback_value is None else fallback_value
                    ),
                    nutrient_presence=(
                        PresenceState.EXPLICIT_ZERO
                        if fallback_value == Decimal("0")
                        else PresenceState.SUPPLIED
                    ),
                )
            else:
                matrix = synthetic_matrix_365(
                    provider_key,
                    local_date=current,
                    user_id=user_id,
                    source_instance_id=source_instance_id,
                    macro_evidence=fallback_value is None,
                    nutrient_value=(
                        Decimal("0") if fallback_value is None else Decimal("99")
                    ),
                    nutrient_presence=(
                        PresenceState.EXPLICIT_ZERO
                        if fallback_value is None
                        else PresenceState.SUPPLIED
                    ),
                )
            rows[current] = {key: matrix[key] for key in metric_keys}
        return rows

    monkeypatch.setattr(nutrition_micronutrients, "resolve_provider_period", fake_period)
    result = nutrition_micronutrients.read_analysis_micronutrients(
        object(),
        user_id=USER_ID,
        selection=selection,
        start=DAY,
        end=last,
        metric_key="vitamin_a_ug",
    )

    expected_value = Decimal("0") if fallback_value is None else fallback_value
    expected_provider = "yazio" if fallback_value is None else "google_health"
    assert [(point.provider_key, point.value) for point in result.series[-2:]] == [
        ("google_health", None),
        (expected_provider, expected_value),
    ]
    assert result.metrics[0].total == Decimal("30") + expected_value
    assert result.metrics[0].days_with_data == 31
    expected_contributors = (
        [("google_health", 30), ("yazio", 1)]
        if fallback_value is None
        else [("google_health", 31)]
    )
    assert [
        (item.provider_key, item.days_with_data)
        for item in result.metrics[0].contributors
    ] == expected_contributors
    assert result.selected_provider.provider_key == "google_health"
    assert calls == [
        ("google_health", DAY, DAY + timedelta(days=30)),
        ("google_health", last, last),
        ("yazio", last, last),
    ]
    assert [point.date for point in result.series] == [
        DAY + timedelta(days=offset) for offset in range(32)
    ]


def test_analysis_reader_365_sql_scope(db: Session, user: User) -> None:
    selected_source_id = uuid4()
    db.add(
        YazioConnection(
            id=selected_source_id,
            user_id=user.id,
            encrypted_email=b"synthetic@example.invalid",
            encrypted_password=b"synthetic-only",
            source_identifier="analysis-365-test",
        )
    )
    foreign_user = User(
        username="analysis-365-foreign",
        password_hash="synthetic-password-hash",
        timezone="Europe/Berlin",
    )
    db.add(foreign_user)
    db.flush()

    selection = _selection((("yazio", selected_source_id),))
    for offset in (0, 31, 89, 364):
        current = DAY + timedelta(days=offset)
        _add_yazio_product_event(
            db,
            user_id=foreign_user.id,
            source_instance_id=selected_source_id,
            local_date=current,
            vitamin_a_value=Decimal("7"),
        )
        _add_yazio_product_event(
            db,
            user_id=user.id,
            source_instance_id=uuid4(),
            local_date=current,
            vitamin_a_value=Decimal("9"),
        )
        result = nutrition_micronutrients.read_analysis_micronutrients(
            db,
            user_id=user.id,
            selection=selection,
            start=DAY,
            end=current,
            metric_key="vitamin_a_ug",
        )
        assert [point.value for point in result.series] == [
            None
        ] * (offset + 1)
        assert result.metrics[0].total is None
        assert result.metrics[0].days_with_data == 0
        assert result.metrics[0].contributors == []

    final_day = DAY + timedelta(days=364)
    _add_yazio_product_event(
        db,
        user_id=user.id,
        source_instance_id=selected_source_id,
        local_date=final_day,
        vitamin_a_value=Decimal("2.5"),
    )
    result = nutrition_micronutrients.read_analysis_micronutrients(
        db,
        user_id=user.id,
        selection=selection,
        start=DAY,
        end=final_day,
        metric_key="vitamin_a_ug",
    )
    assert [point.date for point in result.series] == [
        DAY + timedelta(days=offset) for offset in range(365)
    ]
    assert [point.value for point in result.series] == [
        *([None] * 364),
        Decimal("2.5"),
    ]
    assert result.metrics[0].total == Decimal("2.5")
    assert result.metrics[0].days_with_data == 1
    assert [(item.provider_key, item.days_with_data) for item in result.metrics[0].contributors] == [
        ("yazio", 1)
    ]


@pytest.mark.parametrize("late_failure", ["exception", "conflict"])
def test_analysis_reader_never_returns_partial_response(
    monkeypatch: pytest.MonkeyPatch, late_failure: str
) -> None:
    def fake_period(
        _db: object,
        *,
        provider_key: str,
        user_id: UUID,
        source_instance_id: UUID,
        start: date,
        end: date,
        metric_keys: tuple[str, ...],
    ) -> dict[date, dict[str, ProviderCandidate]]:
        if start > DAY and late_failure == "exception":
            raise ValueError("synthetic late chunk failure")
        rows: dict[date, dict[str, ProviderCandidate]] = {}
        for offset in range((end - start).days + 1):
            current = start + timedelta(days=offset)
            matrix = synthetic_matrix_365(
                provider_key,
                local_date=current,
                user_id=user_id,
                source_instance_id=source_instance_id,
                nutrient_value=Decimal("1"),
                nutrient_presence=PresenceState.SUPPLIED,
                nutrient_resolution=(
                    ResolutionState.CONFLICT
                    if start > DAY
                    else ResolutionState.RESOLVED
                ),
            )
            rows[current] = {key: matrix[key] for key in metric_keys}
        return rows

    monkeypatch.setattr(nutrition_micronutrients, "resolve_provider_period", fake_period)
    with pytest.raises(nutrition_micronutrients.AnalysisMicronutrientReadError):
        nutrition_micronutrients.read_analysis_micronutrients(
            object(),
            user_id=USER_ID,
            selection=_selection((("yazio", SOURCE_B),)),
            start=DAY,
            end=DAY + timedelta(days=31),
            metric_key="vitamin_a_ug",
        )


def test_analysis_reader_365_rejects_invalid_bounds_and_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[date] = []

    def fake_period(
        _db: object,
        *,
        start: date,
        end: date,
        metric_keys: tuple[str, ...],
        **_scope: object,
    ) -> dict[date, dict[str, ProviderCandidate]]:
        calls.append(start)
        return {}

    monkeypatch.setattr(nutrition_micronutrients, "resolve_provider_period", fake_period)
    selection = _selection((("yazio", SOURCE_B),))
    with pytest.raises(nutrition_micronutrients.AnalysisMicronutrientReadError):
        nutrition_micronutrients.read_analysis_micronutrients(
            object(),
            user_id=USER_ID,
            selection=selection,
            start=DAY,
            end=DAY + timedelta(days=365),
            metric_key="vitamin_a_ug",
        )
    with pytest.raises(nutrition_micronutrients.AnalysisMicronutrientReadError):
        nutrition_micronutrients.read_analysis_micronutrients(
            object(),
            user_id=USER_ID,
            selection=selection,
            start=DAY + timedelta(days=1),
            end=DAY,
            metric_key="vitamin_a_ug",
        )
    with pytest.raises(nutrition_micronutrients.AnalysisMicronutrientReadError):
        nutrition_micronutrients.read_analysis_micronutrients(
            object(),
            user_id=USER_ID,
            selection=selection,
            start=DAY,
            end=DAY,
            metric_key="not-a-micronutrient",
        )
    with pytest.raises(nutrition_micronutrients.AnalysisMicronutrientReadError):
        nutrition_micronutrients.read_analysis_micronutrients(
            object(),
            user_id=USER_ID,
            selection=_selection(
                (("yazio", SOURCE_B), ("yazio", SOURCE_A))
            ),
            start=DAY,
            end=DAY,
            metric_key="vitamin_a_ug",
        )
    assert calls == []
ANALYSIS_MICRONUTRIENT_PATH = "/api/v1/analytics/nutrition/micronutrients"
ANALYSIS_PASSWORD = "correct-horse-battery-staple"


def _add_yazio_connection_365(
    db: Session, *, user_id: UUID, source_instance_id: UUID
) -> None:
    db.add(
        YazioConnection(
            id=source_instance_id,
            user_id=user_id,
            encrypted_email=b"synthetic-encrypted-email",
            encrypted_password=b"synthetic-encrypted-password",
            source_identifier=f"analysis-365-{source_instance_id}",
        )
    )
    db.flush()


def login_365(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": ANALYSIS_PASSWORD},
    )
    assert response.status_code == 200


def _fail_legacy_reader_365(*_args: object, **_kwargs: object) -> None:
    pytest.fail("canonical Analysis reader called a legacy micronutrient reader")


@pytest.mark.parametrize("days", [1, 31, 32, 90, 180, 365])
def test_analysis_micronutrient_api_365_range(
    client: TestClient,
    db: Session,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
    days: int,
) -> None:
    from app.api import analytics as analytics_api

    _add_yazio_connection_365(
        db, user_id=user.id, source_instance_id=SOURCE_B
    )
    db.commit()
    monkeypatch.setattr(
        analytics_api,
        "read_legacy_micronutrient_period",
        _fail_legacy_reader_365,
    )
    login_365(client)

    end = DAY + timedelta(days=days - 1)
    params = {"start": DAY.isoformat(), "end": end.isoformat()}
    overview = client.get(ANALYSIS_MICRONUTRIENT_PATH, params=params)
    assert overview.status_code == 200
    overview_body = overview.json()
    assert overview_body["start_date"] == DAY.isoformat()
    assert overview_body["end_date"] == end.isoformat()
    assert overview_body["period_days"] == days
    assert [item["metric_key"] for item in overview_body["metrics"]] == [
        item.metric_type for item in MICRONUTRIENTS
    ]
    assert all(item["total"] is None for item in overview_body["metrics"])
    assert overview_body["series"] == []
    assert overview_body["period_capabilities"] == {
        "supports_all_data": False,
        "all_data_start_date": None,
    }

    detail = client.get(
        ANALYSIS_MICRONUTRIENT_PATH,
        params={**params, "metric_key": "vitamin_a_ug"},
    )
    assert detail.status_code == 200
    detail_body = detail.json()
    assert detail_body["start_date"] == DAY.isoformat()
    assert detail_body["end_date"] == end.isoformat()
    assert detail_body["period_days"] == days
    assert [item["metric_key"] for item in detail_body["metrics"]] == [
        "vitamin_a_ug"
    ]
    assert [point["date"] for point in detail_body["series"]] == [
        (DAY + timedelta(days=offset)).isoformat() for offset in range(days)
    ]
    assert [point["value"] for point in detail_body["series"]] == [None] * days
    assert detail_body["period_capabilities"] == {
        "supports_all_data": False,
        "all_data_start_date": None,
    }


def test_analysis_micronutrient_api_rejects_366_and_keeps_validation_baselines(
    client: TestClient,
    db: Session,
    user: User,
) -> None:
    _add_yazio_connection_365(
        db, user_id=user.id, source_instance_id=SOURCE_B
    )
    db.commit()

    assert client.get(ANALYSIS_MICRONUTRIENT_PATH).status_code == 401
    login_365(client)
    default_period = client.get(ANALYSIS_MICRONUTRIENT_PATH)
    assert default_period.status_code == 200
    assert default_period.json()["period_days"] == 30
    assert client.get(
        ANALYSIS_MICRONUTRIENT_PATH,
        params={"start": "2026-01-02", "end": "2026-01-01"},
    ).status_code == 422
    assert client.get(
        ANALYSIS_MICRONUTRIENT_PATH,
        params={
            "start": DAY.isoformat(),
            "end": DAY.isoformat(),
            "metric_key": "unknown_metric",
        },
    ).status_code == 422
    assert client.get(
        ANALYSIS_MICRONUTRIENT_PATH,
        params={
            "start": DAY.isoformat(),
            "end": (DAY + timedelta(days=365)).isoformat(),
        },
    ).status_code == 422


@pytest.mark.parametrize("late_failure", ["exception", "conflict"])
def test_analysis_micronutrient_api_late_chunk_failure_is_atomic(
    client: TestClient,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
    late_failure: str,
) -> None:
    from app.api import analytics as analytics_api
    from app.problem_types import PROVIDER_SELECTION_NOT_READY

    selection_calls: list[tuple[UUID, bool]] = []

    def fixed_selection(
        _db: object, *, user_id: UUID, include_analysis_demo: bool = False
    ) -> NutritionProviderSelection:
        selection_calls.append((user_id, include_analysis_demo))
        return _selection((("yazio", SOURCE_B),))

    def failing_period(
        _db: object,
        *,
        provider_key: str,
        start: date,
        end: date,
        metric_keys: tuple[str, ...],
        **scope: object,
    ) -> dict[date, dict[str, ProviderCandidate]]:
        assert scope["user_id"] == user.id
        assert scope["source_instance_id"] == SOURCE_B
        if start > DAY and late_failure == "exception":
            raise ValueError("synthetic required chunk failure")
        rows: dict[date, dict[str, ProviderCandidate]] = {}
        for offset in range((end - start).days + 1):
            current = start + timedelta(days=offset)
            matrix = synthetic_matrix_365(
                provider_key,
                local_date=current,
                user_id=user.id,
                nutrient_value=Decimal("1"),
                nutrient_presence=PresenceState.SUPPLIED,
                nutrient_resolution=(
                    ResolutionState.CONFLICT
                    if start > DAY
                    else ResolutionState.RESOLVED
                ),
            )
            rows[current] = {key: matrix[key] for key in metric_keys}
        return rows

    monkeypatch.setattr(
        analytics_api, "resolve_nutrition_provider", fixed_selection
    )
    monkeypatch.setattr(
        nutrition_micronutrients, "resolve_provider_period", failing_period
    )
    login_365(client)
    response = client.get(
        ANALYSIS_MICRONUTRIENT_PATH,
        params={
            "start": DAY.isoformat(),
            "end": (DAY + timedelta(days=31)).isoformat(),
            "metric_key": "vitamin_a_ug",
        },
    )
    assert response.status_code == 503
    assert response.json()["type"] == PROVIDER_SELECTION_NOT_READY
    assert "metrics" not in response.json()
    assert "series" not in response.json()
    assert selection_calls == [(user.id, True)]


def test_analysis_micronutrient_api_missing_selection_keeps_503_contract(
    client: TestClient,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api import analytics as analytics_api
    from app.problem_types import PROVIDER_SELECTION_UNAVAILABLE

    calls: list[tuple[UUID, bool]] = []

    def no_selection(
        _db: object, *, user_id: UUID, include_analysis_demo: bool = False
    ) -> None:
        calls.append((user_id, include_analysis_demo))
        return None

    monkeypatch.setattr(
        analytics_api, "resolve_nutrition_provider", no_selection
    )
    login_365(client)
    response = client.get(
        ANALYSIS_MICRONUTRIENT_PATH,
        params={
            "start": DAY.isoformat(),
            "end": (DAY + timedelta(days=364)).isoformat(),
            "metric_key": "vitamin_a_ug",
        },
    )
    assert response.status_code == 503
    assert response.json()["type"] == PROVIDER_SELECTION_UNAVAILABLE
    assert "metrics" not in response.json()
    assert "series" not in response.json()
    assert calls == [(user.id, True)]


def test_analysis_micronutrient_api_365_isolates_user_evidence(
    client: TestClient,
    db: Session,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api import analytics as analytics_api

    foreign_user = User(
        username="analysis-365-api-foreign",
        password_hash="synthetic-password-hash",
        timezone="Europe/Berlin",
    )
    db.add(foreign_user)
    db.flush()
    foreign_source_id = uuid4()
    _add_yazio_connection_365(
        db, user_id=user.id, source_instance_id=SOURCE_B
    )
    _add_yazio_connection_365(
        db, user_id=foreign_user.id, source_instance_id=foreign_source_id
    )
    _add_yazio_product_event(
        db,
        user_id=foreign_user.id,
        source_instance_id=foreign_source_id,
        local_date=DAY + timedelta(days=364),
        vitamin_a_value=Decimal("999"),
    )
    db.commit()
    monkeypatch.setattr(
        analytics_api,
        "read_legacy_micronutrient_period",
        _fail_legacy_reader_365,
    )
    login_365(client)

    response = client.get(
        ANALYSIS_MICRONUTRIENT_PATH,
        params={
            "start": DAY.isoformat(),
            "end": (DAY + timedelta(days=364)).isoformat(),
            "metric_key": "vitamin_a_ug",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["period_days"] == 365
    assert [point["value"] for point in body["series"]] == [None] * 365
    assert body["metrics"][0]["total"] is None
    assert body["metrics"][0]["days_with_data"] == 0
