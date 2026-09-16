import io
import json
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy.orm import Session

from app.analytics import micronutrient_shadow
from app.analytics.micronutrient_shadow import (
    MicronutrientEvaluation,
    MicronutrientMetricResult,
    MicronutrientParityClassification,
    MicronutrientPeriodResult,
    MicronutrientShadowState,
    ShadowEligibility,
    read_canonical_micronutrient_period,
    read_legacy_micronutrient_period,
    resolve_shadow_source_mapping,
    run_micronutrient_canonical_read,
    run_micronutrient_shadow,
)
from app.api.analytics import micronutrients
from app.config import Settings
from app.importers.common import CanonicalSample
from app.importers.json_adapter import AdapterResult
from app.micronutrients import MICRONUTRIENT_METRIC_TYPES, MICRONUTRIENTS
from app.models import User, YazioConnection
from app.services.import_service import persist_apple_health_stream, persist_import
from app.services.yazio_nutrition_ingestion import ingest_yazio_food_diary
from app.services.yazio_provider import (
    YazioDailyNutrientSummary,
    YazioFoodDiary,
    YazioNutrientValues,
)


def _sample(day: int, metric_type: str, value: str, source_type: str = "test") -> CanonicalSample:
    timestamp = datetime(2024, 1, day, 12, tzinfo=UTC)
    unit = "kcal" if metric_type == "dietary_energy_kcal" else "mg"
    return CanonicalSample(
        metric_type,
        Decimal(value),
        unit,
        Decimal(value),
        unit,
        timestamp,
        timestamp,
        "Europe/Berlin",
        source_type,
        "Characterization",
        "characterization",
        f"{day}-{source_type}-{metric_type}",
    )


def _persist(db: Session, user: User, samples: list[CanonicalSample]) -> None:
    persist_import(
        db,
        user,
        AdapterResult("characterization", samples, received=len(samples)),
        None,
        "micronutrient-characterization",
        "test",
    )


def test_legacy_micronutrients_response_contract_is_characterized(db: Session, user: User) -> None:
    _persist(
        db,
        user,
        [
            _sample(1, "dietary_energy_kcal", "1800"),
            _sample(2, "dietary_energy_kcal", "1900"),
            _sample(1, "iron_mg", "7"),
            _sample(2, "iron_mg", "14"),
            _sample(1, "vitamin_d_ug", "10"),
        ],
    )

    result = micronutrients(
        start=date(2024, 1, 1),
        end=date(2024, 1, 2),
        source="test",
        period=None,
        user=user,
        db=db,
    )
    by_metric = {item["metric_type"]: item for item in result["nutrients"]}

    assert set(by_metric) == set(MICRONUTRIENT_METRIC_TYPES)
    assert len(result["nutrients"]) == len(MICRONUTRIENTS)
    assert result["start_date"] == "2024-01-01"
    assert result["end_date"] == "2024-01-02"
    assert result["source"] == "test"
    assert result["recorded_days"] == 2
    assert result["available_sources"] == [{"source_type": "test", "last_updated_at": result["last_updated_at"]}]
    assert result["definition"] == {
        "reference": "EU-NRV für Erwachsene, Verordnung (EU) Nr. 1169/2011, Anhang XIII",
        "average": "Summe im Zeitraum geteilt durch Tage mit Ernährungseinträgen derselben Quelle",
        "coverage_threshold": 0.7,
        "orientation_threshold_percent": 80,
    }
    assert by_metric["iron_mg"] == {
        "id": "mineral.iron",
        "metric_type": "iron_mg",
        "label": "Eisen",
        "category": "mineral",
        "unit": "mg",
        "eu_nrv": 14.0,
        "total": 21.0,
        "average_daily": 10.5,
        "days_with_value": 2,
        "coverage_ratio": 1.0,
        "percent_of_nrv": 75.0,
        "status": "below_orientation",
    }
    assert by_metric["vitamin_d_ug"]["total"] == 10.0
    assert by_metric["vitamin_d_ug"]["average_daily"] == 5.0
    assert by_metric["vitamin_d_ug"]["days_with_value"] == 1
    assert by_metric["vitamin_d_ug"]["coverage_ratio"] == 0.5
    assert by_metric["vitamin_d_ug"]["status"] == "insufficient_data"


def test_legacy_micronutrients_source_none_aggregates_all_sources(db: Session, user: User) -> None:
    _persist(
        db,
        user,
        [
            _sample(1, "dietary_energy_kcal", "1800", "test-a"),
            _sample(2, "dietary_energy_kcal", "1900", "test-b"),
            _sample(1, "iron_mg", "7", "test-a"),
            _sample(2, "iron_mg", "14", "test-b"),
        ],
    )

    result = micronutrients(
        start=date(2024, 1, 1),
        end=date(2024, 1, 2),
        source=None,
        period=None,
        user=user,
        db=db,
    )
    iron = next(item for item in result["nutrients"] if item["metric_type"] == "iron_mg")

    assert result["source"] is None
    assert result["recorded_days"] == 2
    assert result["available_sources"] == [
        {"source_type": "test-a", "last_updated_at": result["available_sources"][0]["last_updated_at"]},
        {"source_type": "test-b", "last_updated_at": result["available_sources"][1]["last_updated_at"]},
    ]
    assert iron["total"] == 21.0
    assert iron["average_daily"] == 10.5
    assert iron["days_with_value"] == 2




def test_canonical_period_uses_one_fixed_catalog_read_per_day(monkeypatch, user: User) -> None:
    calls: list[date] = []

    def fake_resolve_daily_nutrients(
        db: object, **kwargs: object
    ) -> tuple[SimpleNamespace, ...]:
        del db
        current_day = kwargs["local_date"]
        metric_keys = kwargs["metric_keys"]
        assert isinstance(current_day, date)
        assert set(metric_keys) >= set(MICRONUTRIENT_METRIC_TYPES)
        calls.append(current_day)
        values = {
            (date(2024, 1, 1), "dietary_energy_kcal"): Decimal("1800"),
            (date(2024, 1, 1), "iron_mg"): Decimal("7"),
            (date(2024, 1, 2), "iron_mg"): Decimal("14"),
        }
        return tuple(
            SimpleNamespace(value=values.get((current_day, metric_type)))
            for metric_type in metric_keys
        )

    monkeypatch.setattr(micronutrient_shadow, "resolve_daily_nutrients", fake_resolve_daily_nutrients)

    result = read_canonical_micronutrient_period(
        db=object(),
        user_id=user.id,
        provider_key="yazio",
        source_instance_id=user.id,
        start=date(2024, 1, 1),
        end=date(2024, 1, 2),
    )
    iron = next(item for item in result.nutrients if item.metric_type == "iron_mg")

    assert calls == [date(2024, 1, 1), date(2024, 1, 2)]
    assert result.recorded_days == 1
    assert iron.total == Decimal("21")
    assert iron.average_daily == Decimal("21")
    assert iron.days_with_value == 2


def test_canonical_period_falls_back_to_micronutrient_days_without_primary_values(
    monkeypatch, user: User
) -> None:
    def fake_resolve_daily_nutrients(
        db: object, **kwargs: object
    ) -> tuple[SimpleNamespace, ...]:
        del db
        current_day = kwargs["local_date"]
        metric_keys = kwargs["metric_keys"]
        values = {
            (date(2024, 1, 1), "iron_mg"): Decimal("7"),
            (date(2024, 1, 2), "vitamin_d_ug"): Decimal("10"),
        }
        return tuple(
            SimpleNamespace(value=values.get((current_day, metric_type)))
            for metric_type in metric_keys
        )

    monkeypatch.setattr(micronutrient_shadow, "resolve_daily_nutrients", fake_resolve_daily_nutrients)

    result = read_canonical_micronutrient_period(
        db=object(),
        user_id=user.id,
        provider_key="yazio",
        source_instance_id=user.id,
        start=date(2024, 1, 1),
        end=date(2024, 1, 2),
    )

    assert result.recorded_days == 2


def test_micronutrient_parity_exposes_closed_classification_set() -> None:
    legacy = MicronutrientPeriodResult(
        recorded_days=2,
        nutrients=(
            MicronutrientMetricResult(
                metric_type="iron_mg",
                total=Decimal("21"),
                average_daily=Decimal("10.5"),
                days_with_value=2,
                eu_nrv=Decimal("14"),
                percent_of_nrv=Decimal("75"),
                status="below_orientation",
            ),
        ),
    )
    canonical = MicronutrientPeriodResult(
        recorded_days=1,
        nutrients=legacy.nutrients,
    )

    parity = micronutrient_shadow.compare_micronutrient_periods(legacy, canonical)

    assert parity.classification is MicronutrientParityClassification.RECORDED_DAYS_MISMATCH
    assert {item.value for item in MicronutrientParityClassification} == {
        "match",
        "total_mismatch",
        "average_mismatch",
        "days_with_value_mismatch",
        "status_mismatch",
        "recorded_days_mismatch",
        "legacy_only",
        "canonical_only",
        "not_comparable",
    }


def test_micronutrient_shadow_eligibility_is_bounded_and_read_only() -> None:
    assert ShadowEligibility.disabled().state.value == "disabled"
    assert (
        ShadowEligibility.check(
            enabled=True,
            source="test",
            period=None,
            start=date(2024, 1, 1),
            end=date(2024, 2, 1),
        ).state.value
        == "range_too_long"
    )
    assert (
        ShadowEligibility.check(
            enabled=True,
            source=None,
            period=None,
            start=date(2024, 1, 1),
            end=date(2024, 1, 1),
        ).state.value
        == "not_comparable"
    )
    assert (
        ShadowEligibility.check(
            enabled=True,
            source="yazio_export_v1",
            period="all",
            start=date(2024, 1, 1),
            end=date(2024, 1, 1),
        ).state.value
        == "period_all"
    )
def test_legacy_micronutrients_recorded_days_falls_back_to_micronutrient_days(
    db: Session, user: User
) -> None:
    _persist(
        db,
        user,
        [
            _sample(1, "iron_mg", "7"),
            _sample(2, "vitamin_d_ug", "10"),
        ],
    )

    result = micronutrients(
        start=date(2024, 1, 1),
        end=date(2024, 1, 2),
        source="test",
        period=None,
        user=user,
        db=db,
    )

    assert result["recorded_days"] == 2


def test_legacy_micronutrients_primary_recorded_days_require_positive_value(
    db: Session, user: User
) -> None:
    _persist(
        db,
        user,
        [
            _sample(1, "dietary_energy_kcal", "0"),
            _sample(1, "iron_mg", "7"),
            _sample(2, "dietary_energy_kcal", "1800"),
            _sample(2, "iron_mg", "14"),
        ],
    )

    result = micronutrients(
        start=date(2024, 1, 1),
        end=date(2024, 1, 2),
        source="test",
        period=None,
        user=user,
        db=db,
    )

    assert result["recorded_days"] == 1


def test_shadow_source_mapping_is_explicit_and_provider_scoped(monkeypatch, user: User) -> None:
    source_instance_id = user.id

    class Bindings:
        def for_provider(self, provider_key: str) -> SimpleNamespace | None:
            return (
                SimpleNamespace(provider_key=provider_key, source_instance_id=source_instance_id)
                if provider_key == "yazio"
                else None
            )

    monkeypatch.setattr(
        micronutrient_shadow,
        "resolve_default_provider_sources",
        lambda db, *, user_id, provider_keys: Bindings(),
    )

    yazio = resolve_shadow_source_mapping(
        object(), user_id=user.id, source="yazio_export_v1"
    )
    apple = resolve_shadow_source_mapping(
        object(), user_id=user.id, source="apple_health_xml"
    )
    unsupported = resolve_shadow_source_mapping(object(), user_id=user.id, source="google_health")

    assert yazio is not None
    assert yazio.provider_key == "yazio"
    assert yazio.source_instance_id == user.id
    assert apple is not None
    assert apple.provider_key == "apple_health"
    assert apple.source_instance_id != user.id
    assert unsupported is None


def test_shadow_disabled_and_not_comparable_paths_do_not_open_sessions(
    monkeypatch, user: User
) -> None:
    opened = False

    def fail_session() -> object:
        nonlocal opened
        opened = True
        raise AssertionError("shadow session must not open")

    monkeypatch.setattr(micronutrient_shadow, "SessionLocal", fail_session)
    empty = MicronutrientPeriodResult(recorded_days=0, nutrients=())
    start = date(2024, 1, 1)
    disabled = run_micronutrient_shadow(
        user.id,
        start,
        start,
        "yazio_export_v1",
        None,
        empty,
        enabled=False,
    )
    not_comparable = run_micronutrient_shadow(
        user.id,
        start,
        start,
        None,
        None,
        empty,
        enabled=True,
    )
    google = run_micronutrient_shadow(
        user.id,
        start,
        start,
        "google_health",
        None,
        empty,
        enabled=True,
    )
    period_all = run_micronutrient_shadow(
        user.id,
        start,
        start,
        "yazio_export_v1",
        "all",
        empty,
        enabled=True,
    )

    assert disabled.state is MicronutrientShadowState.DISABLED
    assert not_comparable.state is MicronutrientShadowState.NOT_COMPARABLE
    assert google.state is MicronutrientShadowState.NOT_COMPARABLE
    assert period_all.state is MicronutrientShadowState.PERIOD_ALL
    assert opened is False


def test_shadow_fail_open_rolls_back_and_redacts_telemetry(monkeypatch, user: User) -> None:
    events: list[dict[str, object]] = []

    class Session:
        def __enter__(self) -> Session:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def rollback(self) -> None:
            events.append({"rollback": True})

    monkeypatch.setattr(micronutrient_shadow, "SessionLocal", lambda: Session())
    monkeypatch.setattr(
        micronutrient_shadow,
        "resolve_shadow_source_mapping",
        lambda db, *, user_id, source: SimpleNamespace(
            provider_key="yazio", source_instance_id=user.id
        ),
    )

    def fail_canonical(*args: object, **kwargs: object) -> MicronutrientPeriodResult:
        raise ValueError("raw payload secret")

    monkeypatch.setattr(micronutrient_shadow, "read_canonical_micronutrient_period", fail_canonical)
    monkeypatch.setattr(
        micronutrient_shadow.LOGGER,
        "info",
        lambda message, *, extra: events.append({"message": message, **extra}),
    )

    result = run_micronutrient_shadow(
        user.id,
        date(2024, 1, 1),
        date(2024, 1, 1),
        "yazio_export_v1",
        None,
        MicronutrientPeriodResult(recorded_days=0, nutrients=()),
        enabled=True,
    )

    assert result.state is MicronutrientShadowState.ERROR
    assert result.exception_class == "ValueError"
    assert any(event.get("rollback") for event in events)
    message = next(str(event["message"]) for event in events if "message" in event)
    assert "raw payload secret" not in message
    assert "user" not in message
    assert "source" not in message


def test_shadow_uses_own_session_without_commit_or_flush(monkeypatch, user: User) -> None:
    calls: list[str] = []

    class Session:
        def __enter__(self) -> Session:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def commit(self) -> None:
            calls.append("commit")

        def flush(self) -> None:
            calls.append("flush")

        def rollback(self) -> None:
            calls.append("rollback")

    monkeypatch.setattr(micronutrient_shadow, "SessionLocal", lambda: Session())
    monkeypatch.setattr(
        micronutrient_shadow,
        "resolve_shadow_source_mapping",
        lambda db, *, user_id, source: SimpleNamespace(
            provider_key="yazio", source_instance_id=user.id
        ),
    )
    empty = MicronutrientPeriodResult(recorded_days=0, nutrients=())
    monkeypatch.setattr(
        micronutrient_shadow,
        "read_canonical_micronutrient_period",
        lambda *args, **kwargs: empty,
    )

    run_micronutrient_shadow(
        user.id,
        date(2024, 1, 1),
        date(2024, 1, 1),
        "yazio_export_v1",
        None,
        empty,
        enabled=True,
    )

    assert "commit" not in calls
    assert "flush" not in calls


def test_micronutrient_endpoint_keeps_legacy_response_when_shadow_disabled(
    monkeypatch, db: Session, user: User
) -> None:
    _persist(
        db,
        user,
        [
            _sample(1, "dietary_energy_kcal", "1800"),
            _sample(1, "iron_mg", "7"),
        ],
    )
    calls: list[dict[str, object]] = []

    def observe_shadow(*args: object, **kwargs: object) -> None:
        calls.append(kwargs)

    monkeypatch.setattr("app.api.analytics.run_micronutrient_shadow", observe_shadow)
    result = micronutrients(
        start=date(2024, 1, 1),
        end=date(2024, 1, 1),
        source="test",
        period=None,
        user=user,
        db=db,
    )
    expected = read_legacy_micronutrient_period(
        db,
        user_id=user.id,
        start=date(2024, 1, 1),
        end=date(2024, 1, 1),
        source="test",
    ).to_public(start=date(2024, 1, 1), end=date(2024, 1, 1), source="test")

    assert result == expected
    assert calls[0]["enabled"] is False


def test_micronutrient_endpoint_keeps_legacy_response_when_enabled_shadow_fails(
    monkeypatch, db: Session, user: User
) -> None:
    _persist(
        db,
        user,
        [
            _sample(1, "dietary_energy_kcal", "1800"),
            _sample(1, "iron_mg", "7"),
        ],
    )
    start = date(2024, 1, 1)
    expected = read_legacy_micronutrient_period(
        db,
        user_id=user.id,
        start=start,
        end=start,
        source="test",
    ).to_public(start=start, end=start, source="test")

    monkeypatch.setattr(
        "app.api.analytics.settings.analytics_micronutrients_shadow_read_enabled",
        True,
    )

    def fail_shadow(*args: object, **kwargs: object) -> None:
        raise RuntimeError("shadow must remain invisible")

    monkeypatch.setattr("app.api.analytics.run_micronutrient_shadow", fail_shadow)

    result = micronutrients(
        start=start,
        end=start,
        source="test",
        period=None,
        user=user,
        db=db,
    )

    assert result == expected






def test_yazio_legacy_and_canonical_micronutrients_have_decimal_parity(
    db: Session, user: User
) -> None:
    connection = YazioConnection(
        user_id=user.id,
        encrypted_email=b"encrypted-email",
        encrypted_password=b"encrypted-password",
        source_identifier="n4-yazio-source",
    )
    db.add(connection)
    db.flush()
    day = date(2024, 1, 1)
    ingest_yazio_food_diary(
        db,
        user_id=user.id,
        source_instance_id=connection.id,
        requested_start=day,
        requested_end=day,
        diary=YazioFoodDiary(
            requested_start_day=day,
            requested_end_day=day,
            consumed_products=(),
            consumed_simple_products=(),
            product_profiles=(),
            daily_summaries=(
                YazioDailyNutrientSummary(
                    local_date=day,
                    nutrients=YazioNutrientValues(
                        energy=Decimal("1800"),
                        additional={"mineral.iron": Decimal("0.007")},
                    ),
                    energy_goal=None,
                ),
            ),
        ),
    )
    _persist(
        db,
        user,
        [
            _sample(1, "dietary_energy_kcal", "1800", "yazio_export_v1"),
            _sample(1, "iron_mg", "7", "yazio_export_v1"),
        ],
    )

    legacy = read_legacy_micronutrient_period(
        db,
        user_id=user.id,
        start=day,
        end=day,
        source="yazio_export_v1",
    )
    mapping = resolve_shadow_source_mapping(
        db,
        user_id=user.id,
        source="yazio_export_v1",
    )
    assert mapping is not None
    canonical = read_canonical_micronutrient_period(
        db,
        user_id=user.id,
        provider_key=mapping.provider_key,
        source_instance_id=mapping.source_instance_id,
        start=day,
        end=day,
    )
    parity = micronutrient_shadow.compare_micronutrient_periods(legacy, canonical)

    assert parity.classification is MicronutrientParityClassification.MATCH
    assert next(item for item in canonical.nutrients if item.metric_type == "iron_mg").total == Decimal("7")


def test_micronutrient_parity_detects_value_and_missing_side_differences() -> None:
    legacy_item = MicronutrientMetricResult(
        metric_type="iron_mg",
        total=Decimal("21"),
        average_daily=Decimal("10.5"),
        days_with_value=2,
        eu_nrv=Decimal("14"),
        percent_of_nrv=Decimal("75"),
        status="below_orientation",
    )
    changed = MicronutrientMetricResult(
        metric_type="iron_mg",
        total=Decimal("22"),
        average_daily=Decimal("11"),
        days_with_value=2,
        eu_nrv=Decimal("14"),
        percent_of_nrv=Decimal("78.57142857142857142857142857"),
        status="below_orientation",
    )
    legacy = MicronutrientPeriodResult(recorded_days=2, nutrients=(legacy_item,))

    assert (
        micronutrient_shadow.compare_micronutrient_periods(
            legacy,
            MicronutrientPeriodResult(recorded_days=2, nutrients=(changed,)),
        ).classification
        is MicronutrientParityClassification.TOTAL_MISMATCH
    )
    assert (
        micronutrient_shadow.compare_micronutrient_periods(
            legacy,
            MicronutrientPeriodResult(recorded_days=2, nutrients=()),
        ).classification
        is MicronutrientParityClassification.LEGACY_ONLY
    )
    assert (
        micronutrient_shadow.compare_micronutrient_periods(
            MicronutrientPeriodResult(recorded_days=2, nutrients=()),
            MicronutrientPeriodResult(recorded_days=2, nutrients=(legacy_item,)),
        ).classification
        is MicronutrientParityClassification.CANONICAL_ONLY
    )



def test_apple_legacy_and_canonical_micronutrients_have_parity(
    db: Session, user: User
) -> None:
    day = date(2024, 1, 1)
    payload = (
        b"<HealthData>"
        b'<Record type="HKQuantityTypeIdentifierDietaryEnergyConsumed" value="1800" unit="kcal" '
        b'startDate="2024-01-01 10:00:00 +0000" endDate="2024-01-01 10:30:00 +0000" />'
        b'<Correlation type="HKCorrelationTypeIdentifierFood" sourceName="N4" '
        b'startDate="2024-01-01 10:00:00 +0000" endDate="2024-01-01 10:30:00 +0000">'
        b'<MetadataEntry key="HKExternalUUID" value="n4-apple-meal" />'
        b'<Record type="HKQuantityTypeIdentifierDietaryEnergyConsumed" value="1800" unit="kcal" '
        b'startDate="2024-01-01 10:00:00 +0000" endDate="2024-01-01 10:30:00 +0000" />'
        b'<Record type="HKQuantityTypeIdentifierDietaryIron" value="7" unit="mg" '
        b'startDate="2024-01-01 10:00:00 +0000" endDate="2024-01-01 10:30:00 +0000" />'
        b"</Correlation>"
        b"</HealthData>"
    )
    persist_apple_health_stream(
        db,
        user,
        io.BytesIO(payload),
        "application/xml",
        "n4-apple",
        atomic=True,
    )
    _persist(
        db,
        user,
        [
            _sample(1, "dietary_energy_kcal", "1800", "apple_health_xml"),
            _sample(1, "iron_mg", "7", "apple_health_xml"),
        ],
    )

    legacy = read_legacy_micronutrient_period(
        db,
        user_id=user.id,
        start=day,
        end=day,
        source="apple_health_xml",
    )
    mapping = resolve_shadow_source_mapping(
        db,
        user_id=user.id,
        source="apple_health_xml",
    )
    assert mapping is not None
    canonical = read_canonical_micronutrient_period(
        db,
        user_id=user.id,
        provider_key=mapping.provider_key,
        source_instance_id=mapping.source_instance_id,
        start=day,
        end=day,
    )
    parity = micronutrient_shadow.compare_micronutrient_periods(legacy, canonical)

    assert parity.classification is MicronutrientParityClassification.TOTAL_MISMATCH


def test_micronutrient_shadow_settings_default_to_disabled_and_thirty_one_days() -> None:
    settings = Settings(_env_file=None, environment="test")

    assert settings.analytics_micronutrients_shadow_read_enabled is False
    assert settings.analytics_micronutrients_shadow_max_days == 31


def _canonical_with_iron_total(
    period: MicronutrientPeriodResult,
    total: Decimal,
) -> MicronutrientPeriodResult:
    nutrients = tuple(
        replace(
            item,
            total=total if item.metric_type == "iron_mg" else item.total,
            average_daily=total if item.metric_type == "iron_mg" else item.average_daily,
        )
        for item in period.nutrients
    )
    return replace(period, nutrients=nutrients)


def test_canonical_value_scope_replaces_only_values_after_full_match(
    monkeypatch, db: Session, user: User
) -> None:
    _persist(
        db,
        user,
        [_sample(1, "dietary_energy_kcal", "1800"), _sample(1, "iron_mg", "7")],
    )
    start = end = date(2024, 1, 1)
    legacy = read_legacy_micronutrient_period(
        db, user_id=user.id, start=start, end=end, source="test"
    )
    canonical = _canonical_with_iron_total(legacy, Decimal("99"))
    monkeypatch.setattr(
        "app.api.analytics.settings.analytics_micronutrients_canonical_read_enabled", True
    )
    monkeypatch.setattr(
        "app.api.analytics.run_micronutrient_canonical_read",
        lambda *args, **kwargs: MicronutrientEvaluation(
            state=MicronutrientShadowState.MATCH,
            canonical=canonical,
        ),
    )

    result = micronutrients(
        start=start, end=end, source="test", period=None, user=user, db=db
    )

    legacy_public = legacy.to_public(start=start, end=end, source="test")
    assert result["start_date"] == legacy_public["start_date"]
    assert result["end_date"] == legacy_public["end_date"]
    assert result["source"] == legacy_public["source"]
    assert result["last_updated_at"] == legacy_public["last_updated_at"]
    assert result["available_sources"] == legacy_public["available_sources"]
    assert result["definition"] == legacy_public["definition"]
    assert result["recorded_days"] == canonical.recorded_days
    assert next(item for item in result["nutrients"] if item["metric_type"] == "iron_mg")["total"] == 99.0


def test_canonical_mismatch_keeps_complete_legacy_value_scope(
    monkeypatch, db: Session, user: User
) -> None:
    _persist(
        db,
        user,
        [_sample(1, "dietary_energy_kcal", "1800"), _sample(1, "iron_mg", "7")],
    )
    start = end = date(2024, 1, 1)
    legacy = read_legacy_micronutrient_period(
        db, user_id=user.id, start=start, end=end, source="test"
    )
    canonical = _canonical_with_iron_total(legacy, Decimal("99"))
    monkeypatch.setattr(
        "app.api.analytics.settings.analytics_micronutrients_canonical_read_enabled", True
    )
    monkeypatch.setattr(
        "app.api.analytics.run_micronutrient_canonical_read",
        lambda *args, **kwargs: MicronutrientEvaluation(
            state=MicronutrientShadowState.MISMATCH,
            canonical=canonical,
            reason="parity",
        ),
    )

    result = micronutrients(
        start=start, end=end, source="test", period=None, user=user, db=db
    )

    assert result == legacy.to_public(start=start, end=end, source="test")


def test_canonical_error_keeps_complete_legacy_response(
    monkeypatch, db: Session, user: User
) -> None:
    _persist(db, user, [_sample(1, "dietary_energy_kcal", "1800")])
    start = end = date(2024, 1, 1)
    legacy = read_legacy_micronutrient_period(
        db, user_id=user.id, start=start, end=end, source="test"
    )
    monkeypatch.setattr(
        "app.api.analytics.settings.analytics_micronutrients_canonical_read_enabled", True
    )

    def fail_canonical(*args: object, **kwargs: object) -> MicronutrientEvaluation:
        raise RuntimeError("canonical failure")

    monkeypatch.setattr("app.api.analytics.run_micronutrient_canonical_read", fail_canonical)

    result = micronutrients(
        start=start, end=end, source="test", period=None, user=user, db=db
    )

    assert result == legacy.to_public(start=start, end=end, source="test")


def test_canonical_serialization_failure_keeps_complete_legacy_response(
    monkeypatch, db: Session, user: User
) -> None:
    _persist(db, user, [_sample(1, "dietary_energy_kcal", "1800")])
    start = end = date(2024, 1, 1)
    legacy = read_legacy_micronutrient_period(
        db, user_id=user.id, start=start, end=end, source="test"
    )
    malformed = MicronutrientPeriodResult(
        recorded_days=legacy.recorded_days,
        nutrients=legacy.nutrients[:-1],
    )
    monkeypatch.setattr(
        "app.api.analytics.settings.analytics_micronutrients_canonical_read_enabled", True
    )
    monkeypatch.setattr(
        "app.api.analytics.run_micronutrient_canonical_read",
        lambda *args, **kwargs: MicronutrientEvaluation(
            state=MicronutrientShadowState.MATCH,
            canonical=malformed,
        ),
    )

    result = micronutrients(
        start=start, end=end, source="test", period=None, user=user, db=db
    )

    assert result == legacy.to_public(start=start, end=end, source="test")


def test_canonical_value_scope_serializes_all_catalog_items() -> None:
    period = MicronutrientPeriodResult(
        recorded_days=1,
        nutrients=tuple(
            MicronutrientMetricResult(
                metric_type=definition.metric_type,
                total=Decimal("1"),
                average_daily=Decimal("1"),
                days_with_value=1,
                eu_nrv=definition.eu_nrv,
                percent_of_nrv=Decimal("7"),
                status="covered",
                coverage_ratio=1.0,
            )
            for definition in MICRONUTRIENTS
        ),
    )

    values = period.to_public_value_scope()

    assert values["recorded_days"] == 1
    assert len(values["nutrients"]) == 26
    assert [item["metric_type"] for item in values["nutrients"]] == [
        definition.metric_type for definition in MICRONUTRIENTS
    ]
    assert all(
        set(item)
        == {
            "id",
            "metric_type",
            "label",
            "category",
            "unit",
            "eu_nrv",
            "total",
            "average_daily",
            "days_with_value",
            "coverage_ratio",
            "percent_of_nrv",
            "status",
        }
        for item in values["nutrients"]
    )


def test_canonical_flag_uses_one_evaluation_without_shadow_double_read(
    monkeypatch, db: Session, user: User
) -> None:
    _persist(db, user, [_sample(1, "dietary_energy_kcal", "1800")])
    calls = {"canonical": 0, "shadow": 0}
    monkeypatch.setattr(
        "app.api.analytics.settings.analytics_micronutrients_canonical_read_enabled", True
    )
    monkeypatch.setattr(
        "app.api.analytics.settings.analytics_micronutrients_shadow_read_enabled", True
    )

    def canonical(*args: object, **kwargs: object) -> MicronutrientEvaluation:
        calls["canonical"] += 1
        return MicronutrientEvaluation(state=MicronutrientShadowState.NOT_COMPARABLE)

    def shadow(*args: object, **kwargs: object) -> None:
        calls["shadow"] += 1
        raise AssertionError("N4 shadow must not run with canonical serving enabled")

    monkeypatch.setattr("app.api.analytics.run_micronutrient_canonical_read", canonical)
    monkeypatch.setattr("app.api.analytics.run_micronutrient_shadow", shadow)

    micronutrients(
        start=date(2024, 1, 1),
        end=date(2024, 1, 1),
        source="test",
        period=None,
        user=user,
        db=db,
    )

    assert calls == {"canonical": 1, "shadow": 0}


def test_canonical_disabled_preserves_shadow_route(
    monkeypatch, db: Session, user: User
) -> None:
    _persist(db, user, [_sample(1, "dietary_energy_kcal", "1800")])
    calls = {"canonical": 0, "shadow": 0}
    monkeypatch.setattr(
        "app.api.analytics.settings.analytics_micronutrients_canonical_read_enabled", False
    )
    monkeypatch.setattr(
        "app.api.analytics.settings.analytics_micronutrients_shadow_read_enabled", True
    )

    def canonical(*args: object, **kwargs: object) -> None:
        calls["canonical"] += 1

    def shadow(*args: object, **kwargs: object) -> None:
        calls["shadow"] += 1

    monkeypatch.setattr("app.api.analytics.run_micronutrient_canonical_read", canonical)
    monkeypatch.setattr("app.api.analytics.run_micronutrient_shadow", shadow)

    micronutrients(
        start=date(2024, 1, 1),
        end=date(2024, 1, 1),
        source="test",
        period=None,
        user=user,
        db=db,
    )

    assert calls == {"canonical": 0, "shadow": 1}


def test_canonical_eligibility_never_opens_session_for_ineligible_request(
    monkeypatch, user: User
) -> None:
    opened = False

    def fail_session() -> None:
        nonlocal opened
        opened = True
        raise AssertionError("ineligible request must not open a canonical session")

    monkeypatch.setattr(micronutrient_shadow, "SessionLocal", fail_session)
    empty = MicronutrientPeriodResult(recorded_days=0, nutrients=())

    result = run_micronutrient_canonical_read(
        user.id,
        date(2024, 1, 1),
        date(2024, 2, 1),
        "yazio_export_v1",
        None,
        empty,
        enabled=True,
        max_days=31,
    )

    assert result.state is MicronutrientShadowState.RANGE_TOO_LONG
    assert opened is False


def test_micronutrient_canonical_flag_defaults_to_disabled() -> None:
    configured = Settings(_env_file=None, environment="test")

    assert configured.analytics_micronutrients_canonical_read_enabled is False


def test_canonical_read_telemetry_is_bounded_and_separate(monkeypatch, user: User) -> None:
    events: list[dict[str, object]] = []

    class Session:
        def __enter__(self) -> Session:
            return self

        def __exit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr(micronutrient_shadow, "SessionLocal", lambda: Session())
    monkeypatch.setattr(
        micronutrient_shadow,
        "resolve_shadow_source_mapping",
        lambda db, *, user_id, source: SimpleNamespace(
            provider_key="yazio", source_instance_id=user.id
        ),
    )
    empty = MicronutrientPeriodResult(
        recorded_days=0,
        nutrients=tuple(
            MicronutrientMetricResult(
                metric_type=definition.metric_type,
                total=None,
                average_daily=None,
                days_with_value=0,
                eu_nrv=definition.eu_nrv,
                percent_of_nrv=None,
                status="no_data",
                coverage_ratio=0.0,
            )
            for definition in MICRONUTRIENTS
        ),
    )
    monkeypatch.setattr(
        micronutrient_shadow,
        "read_canonical_micronutrient_period",
        lambda *args, **kwargs: empty,
    )
    monkeypatch.setattr(
        micronutrient_shadow.LOGGER,
        "info",
        lambda message, *, extra: events.append({"message": message, **extra}),
    )

    result = run_micronutrient_canonical_read(
        user.id,
        date(2024, 1, 1),
        date(2024, 1, 1),
        "yazio_export_v1",
        None,
        empty,
        enabled=True,
    )

    assert result.state is MicronutrientShadowState.MATCH
    payload = json.loads(str(events[-1]["message"]))
    assert payload["event"] == "analytics.micronutrients.canonical"
    assert payload["version"] == "n5.v1"
    assert payload["outcome"] == "canonical_served"
    assert set(payload) == {
        "event",
        "version",
        "outcome",
        "range_bucket",
        "duration_bucket",
        "classification",
    }
    assert str(user.id) not in str(payload)
    assert "yazio_export_v1" not in str(payload)


def test_canonical_ineligible_inputs_skip_session(monkeypatch, user: User) -> None:
    opened = False

    def fail_session() -> None:
        nonlocal opened
        opened = True
        raise AssertionError("ineligible request must not open a canonical session")

    monkeypatch.setattr(micronutrient_shadow, "SessionLocal", fail_session)
    empty = MicronutrientPeriodResult(recorded_days=0, nutrients=())
    cases = (
        (None, None, date(2024, 1, 1), date(2024, 1, 1), MicronutrientShadowState.NOT_COMPARABLE),
        ("yazio_export_v1", "all", date(2024, 1, 1), date(2024, 1, 1), MicronutrientShadowState.PERIOD_ALL),
        ("google_health", None, date(2024, 1, 1), date(2024, 1, 1), MicronutrientShadowState.NOT_COMPARABLE),
        ("yazio_export_v1", None, date(2024, 1, 1), date(2024, 2, 1), MicronutrientShadowState.RANGE_TOO_LONG),
    )

    for source, period, start, end, expected_state in cases:
        result = run_micronutrient_canonical_read(
            user.id,
            start,
            end,
            source,
            period,
            empty,
            enabled=True,
            max_days=31,
        )
        assert result.state is expected_state

    assert opened is False
