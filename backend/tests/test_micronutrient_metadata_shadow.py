import json
from datetime import UTC, date, datetime
from types import SimpleNamespace
from uuid import uuid4

from app.analytics import micronutrient_metadata_shadow
from app.analytics.micronutrient_metadata_shadow import (
    MicronutrientMetadataProviderSetClassification,
    MicronutrientMetadataTimestampRelation,
    compare_micronutrient_metadata,
    run_micronutrient_metadata_shadow,
)
from app.analytics.micronutrient_shadow import MicronutrientPeriodResult
from app.nutrition.resolution.discovery import (
    NutritionProviderMetadata,
    NutritionProviderMetadataSet,
)

TIMESTAMP = datetime(2024, 1, 1, 12, tzinfo=UTC)


def _canonical(*providers: tuple[str, datetime | None]) -> NutritionProviderMetadataSet:
    return NutritionProviderMetadataSet(
        tuple(
            NutritionProviderMetadata(provider, observed_at)
            for provider, observed_at in providers
            if observed_at is not None
        )
    )


def _legacy(
    *,
    sources: tuple[str, ...] = (),
    filtered_updated_at: datetime | None = None,
) -> MicronutrientPeriodResult:
    return MicronutrientPeriodResult(
        recorded_days=0,
        nutrients=(),
        filtered_updated_at=filtered_updated_at,
        available_sources=tuple((source, TIMESTAMP) for source in sources),
    )


def test_metadata_provider_sets_classify_exact_and_missing_comparable_providers() -> None:
    exact = compare_micronutrient_metadata(
        _legacy(sources=("yazio_export_v1",)),
        _canonical(("yazio", TIMESTAMP)),
        source="yazio_export_v1",
        legacy_provider_mappings={"yazio_export_v1": "yazio"},
    )
    legacy_only = compare_micronutrient_metadata(
        _legacy(sources=("yazio_export_v1",)),
        _canonical(),
        source="yazio_export_v1",
        legacy_provider_mappings={"yazio_export_v1": "yazio"},
    )
    canonical_only = compare_micronutrient_metadata(
        _legacy(),
        _canonical(("yazio", TIMESTAMP)),
        source="yazio_export_v1",
        legacy_provider_mappings={},
    )

    assert exact.provider_set_classification is MicronutrientMetadataProviderSetClassification.MATCH
    assert legacy_only.provider_set_classification is MicronutrientMetadataProviderSetClassification.LEGACY_ONLY
    assert canonical_only.provider_set_classification is MicronutrientMetadataProviderSetClassification.CANONICAL_ONLY


def test_metadata_provider_sets_distinguish_mismatch_and_no_data() -> None:
    mismatch = compare_micronutrient_metadata(
        _legacy(sources=("yazio_export_v1",)),
        _canonical(("apple_health", TIMESTAMP)),
        source="yazio_export_v1",
        legacy_provider_mappings={"yazio_export_v1": "yazio"},
    )
    no_data = compare_micronutrient_metadata(
        _legacy(),
        _canonical(),
        source="yazio_export_v1",
        legacy_provider_mappings={},
    )

    assert mismatch.provider_set_classification is MicronutrientMetadataProviderSetClassification.PROVIDER_SET_MISMATCH
    assert no_data.provider_set_classification is MicronutrientMetadataProviderSetClassification.NO_DATA


def test_metadata_provider_sets_expose_unmappable_legacy_and_google_as_not_comparable() -> None:
    unmappable = compare_micronutrient_metadata(
        _legacy(sources=("synthetic",)),
        _canonical(),
        source="synthetic",
        legacy_provider_mappings={"synthetic": None},
    )
    google_only = compare_micronutrient_metadata(
        _legacy(),
        _canonical(("google_health", TIMESTAMP)),
        source="yazio_export_v1",
        legacy_provider_mappings={},
    )

    assert unmappable.provider_set_classification is MicronutrientMetadataProviderSetClassification.NOT_COMPARABLE
    assert google_only.provider_set_classification is MicronutrientMetadataProviderSetClassification.NOT_COMPARABLE


def test_metadata_timestamp_relation_is_separate_from_provider_parity() -> None:
    same = compare_micronutrient_metadata(
        _legacy(sources=("yazio_export_v1",), filtered_updated_at=TIMESTAMP),
        _canonical(("yazio", TIMESTAMP)),
        source="yazio_export_v1",
        legacy_provider_mappings={"yazio_export_v1": "yazio"},
    )
    different = compare_micronutrient_metadata(
        _legacy(sources=("yazio_export_v1",), filtered_updated_at=TIMESTAMP),
        _canonical(("yazio", datetime(2024, 1, 2, 12, tzinfo=UTC))),
        source="yazio_export_v1",
        legacy_provider_mappings={"yazio_export_v1": "yazio"},
    )
    both_missing = compare_micronutrient_metadata(
        _legacy(sources=("yazio_export_v1",)),
        _canonical(),
        source="yazio_export_v1",
        legacy_provider_mappings={"yazio_export_v1": "yazio"},
    )
    canonical_only = compare_micronutrient_metadata(
        _legacy(sources=("yazio_export_v1",)),
        _canonical(("yazio", TIMESTAMP)),
        source="yazio_export_v1",
        legacy_provider_mappings={"yazio_export_v1": "yazio"},
    )
    legacy_only = compare_micronutrient_metadata(
        _legacy(sources=("yazio_export_v1",), filtered_updated_at=TIMESTAMP),
        _canonical(),
        source="yazio_export_v1",
        legacy_provider_mappings={"yazio_export_v1": "yazio"},
    )

    assert same.timestamp_relation is MicronutrientMetadataTimestampRelation.BOTH_PRESENT_SAME_VALUE
    assert different.timestamp_relation is MicronutrientMetadataTimestampRelation.BOTH_PRESENT_DIFFERENT_VALUE
    assert both_missing.timestamp_relation is MicronutrientMetadataTimestampRelation.BOTH_MISSING
    assert canonical_only.timestamp_relation is MicronutrientMetadataTimestampRelation.CANONICAL_ONLY
    assert legacy_only.timestamp_relation is MicronutrientMetadataTimestampRelation.LEGACY_ONLY


def test_metadata_shadow_accepts_30_and_31_day_ranges(monkeypatch) -> None:
    class Session:
        def __enter__(self) -> Session:
            return self

        def __exit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr(
        "app.analytics.micronutrient_metadata_shadow.SessionLocal", lambda: Session()
    )
    monkeypatch.setattr(
        "app.analytics.micronutrient_metadata_shadow.discover_nutrition_provider_metadata",
        lambda *args, **kwargs: _canonical(),
    )

    for end in (date(2024, 1, 30), date(2024, 1, 31)):
        result = run_micronutrient_metadata_shadow(
            uuid4(),
            date(2024, 1, 1),
            end,
            None,
            "all",
            _legacy(),
            enabled=True,
            max_days=31,
        )
        assert result.outcome == "evaluated"


def test_metadata_timestamp_for_aggregate_or_unknown_source_is_not_comparable() -> None:
    aggregate = compare_micronutrient_metadata(
        _legacy(sources=("yazio_export_v1",), filtered_updated_at=TIMESTAMP),
        _canonical(("yazio", TIMESTAMP)),
        source=None,
        legacy_provider_mappings={"yazio_export_v1": "yazio"},
    )
    empty = compare_micronutrient_metadata(
        _legacy(sources=("yazio_export_v1",), filtered_updated_at=TIMESTAMP),
        _canonical(("yazio", TIMESTAMP)),
        source="",
        legacy_provider_mappings={"yazio_export_v1": "yazio"},
    )
    unknown = compare_micronutrient_metadata(
        _legacy(sources=("synthetic",), filtered_updated_at=TIMESTAMP),
        _canonical(),
        source="synthetic",
        legacy_provider_mappings={"synthetic": None},
    )

    assert aggregate.timestamp_relation is MicronutrientMetadataTimestampRelation.NOT_COMPARABLE
    assert empty.timestamp_relation is MicronutrientMetadataTimestampRelation.NOT_COMPARABLE
    assert unknown.timestamp_relation is MicronutrientMetadataTimestampRelation.NOT_COMPARABLE


def test_metadata_shadow_skips_32_day_range_without_opening_session(monkeypatch) -> None:
    opened = False

    def fail_session() -> object:
        nonlocal opened
        opened = True
        raise AssertionError("metadata shadow must not open an ineligible session")

    monkeypatch.setattr(
        "app.analytics.micronutrient_metadata_shadow.SessionLocal", fail_session
    )

    result = run_micronutrient_metadata_shadow(
        uuid4(),
        date(2024, 1, 1),
        date(2024, 2, 1),
        "yazio_export_v1",
        "all",
        _legacy(),
        enabled=True,
        max_days=31,
    )

    assert result.outcome == "range_too_long"
    assert opened is False


def test_metadata_shadow_reads_once_and_emits_bounded_telemetry(monkeypatch) -> None:
    events: list[dict[str, object]] = []
    calls: list[dict[str, object]] = []

    class Session:
        def __enter__(self) -> Session:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def rollback(self) -> None:
            calls.append({"rollback": True})

    monkeypatch.setattr(
        "app.analytics.micronutrient_metadata_shadow.SessionLocal", lambda: Session()
    )
    monkeypatch.setattr(
        "app.analytics.micronutrient_metadata_shadow.resolve_shadow_source_mapping",
        lambda db, *, user_id, source: SimpleNamespace(provider_key="yazio"),
    )
    monkeypatch.setattr(
        "app.analytics.micronutrient_metadata_shadow.discover_nutrition_provider_metadata",
        lambda db, **kwargs: calls.append(kwargs) or _canonical(("yazio", TIMESTAMP)),
    )
    monkeypatch.setattr(
        micronutrient_metadata_shadow.LOGGER,
        "info",
        lambda message, *, extra: events.append(json.loads(message)),
    )

    result = run_micronutrient_metadata_shadow(
        uuid4(),
        date(2024, 1, 1),
        date(2024, 1, 1),
        "yazio_export_v1",
        "all",
        _legacy(sources=("yazio_export_v1",), filtered_updated_at=TIMESTAMP),
        enabled=True,
    )

    assert result.provider_set_classification is MicronutrientMetadataProviderSetClassification.MATCH
    assert result.timestamp_relation is MicronutrientMetadataTimestampRelation.BOTH_PRESENT_SAME_VALUE
    assert len([call for call in calls if "start" in call]) == 1
    assert events[-1]["event"] == "analytics.micronutrients.metadata.shadow"
    assert events[-1]["version"] == "source_metadata.v1"
    assert set(events[-1]) <= {
        "event",
        "version",
        "outcome",
        "provider_set_classification",
        "timestamp_relation",
        "range_bucket",
        "duration_bucket",
        "source_context",
        "exception_class",
    }


def test_metadata_shadow_failure_is_redacted_and_fail_open(monkeypatch) -> None:
    events: list[dict[str, object]] = []

    class Session:
        def __enter__(self) -> Session:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def rollback(self) -> None:
            return None

    monkeypatch.setattr(
        "app.analytics.micronutrient_metadata_shadow.SessionLocal", lambda: Session()
    )
    monkeypatch.setattr(
        "app.analytics.micronutrient_metadata_shadow.discover_nutrition_provider_metadata",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("raw payload secret")),
    )
    monkeypatch.setattr(
        micronutrient_metadata_shadow.LOGGER,
        "info",
        lambda message, *, extra: events.append(json.loads(message)),
    )

    result = run_micronutrient_metadata_shadow(
        uuid4(),
        date(2024, 1, 1),
        date(2024, 1, 1),
        "yazio_export_v1",
        None,
        _legacy(),
        enabled=True,
    )

    assert result.outcome == "error"
    assert result.exception_class == "ValueError"
    assert "raw payload secret" not in json.dumps(events)
    assert "yazio_export_v1" not in json.dumps(events)
