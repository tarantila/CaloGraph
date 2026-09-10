from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.nutrition.enums import (
    CoverageState,
    LineageState,
    PresenceState,
    ProjectionGranularity,
    ResolutionState,
)
from app.nutrition.resolution import (
    PROVIDER_RESOLVERS,
    EvidenceKind,
    MetricContribution,
    ProviderCandidate,
    ReasonCode,
)
from app.nutrition.resolution.providers import (
    ProviderCandidateContractError,
    ProviderNotAvailableError,
    collect_provider_candidates,
    resolve_provider_metric,
)

USER_ID = UUID("11111111-1111-1111-1111-111111111111")
OTHER_USER_ID = UUID("22222222-2222-2222-2222-222222222222")
SOURCE_A = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
SOURCE_B = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
LOCAL_DATE = date(2026, 9, 10)
METRIC_KEY = "protein_g"


def _candidate(
    *,
    provider_key: str = "yazio",
    user_id: UUID = USER_ID,
    local_date: date = LOCAL_DATE,
    metric_key: str = METRIC_KEY,
    value: Decimal | None = Decimal("10"),
    coverage_state: CoverageState = CoverageState.COMPLETE,
    resolution_state: ResolutionState = ResolutionState.RESOLVED,
    lineage_state: LineageState = LineageState.CONFIRMED,
) -> ProviderCandidate:
    if value is None:
        return ProviderCandidate(
            provider_key=provider_key,
            user_id=user_id,
            local_date=local_date,
            metric_key=metric_key,
            value=None,
            unit=None,
            selected_granularity=None,
            presence_state=PresenceState.MISSING,
            coverage_state=coverage_state,
            resolution_state=resolution_state,
            lineage_state=lineage_state,
            source_lineage=(),
            reason_code=ReasonCode.ALL_SOURCES_MISSING,
        )
    contribution = MetricContribution(
        evidence_id=uuid4(),
        metric_key=metric_key,
        value=value,
        unit="g",
        presence_state=PresenceState.SUPPLIED,
        resolution_state=resolution_state,
        lineage_state=lineage_state,
        evidence_kind=EvidenceKind.EVENT,
    )
    source_lineage = (contribution,)
    candidate_resolution = resolution_state
    if coverage_state is CoverageState.PARTIAL:
        source_lineage = (
            contribution,
            MetricContribution(
                evidence_id=uuid4(),
                metric_key=metric_key,
                value=None,
                unit="g",
                presence_state=PresenceState.MISSING,
                resolution_state=ResolutionState.UNRESOLVED,
                lineage_state=LineageState.UNKNOWN,
                evidence_kind=EvidenceKind.EVENT,
            ),
        )
        candidate_resolution = ResolutionState.UNRESOLVED
    return ProviderCandidate(
        provider_key=provider_key,
        user_id=user_id,
        local_date=local_date,
        metric_key=metric_key,
        value=value,
        unit="g",
        selected_granularity=(
            ProjectionGranularity.PARTIAL_EVENT
            if coverage_state is CoverageState.PARTIAL
            else ProjectionGranularity.EVENT
        ),
        presence_state=PresenceState.SUPPLIED,
        coverage_state=coverage_state,
        resolution_state=candidate_resolution,
        lineage_state=lineage_state,
        source_lineage=source_lineage,
        reason_code=ReasonCode.PARTIAL_EVENTS_NO_SUMMARY
        if coverage_state is CoverageState.PARTIAL
        else ReasonCode.EVENT_COMPLETE_CONFIRMED,
    )


class SpyResolver:
    def __init__(
        self,
        provider_key: str,
        result: ProviderCandidate | BaseException | Callable[..., ProviderCandidate],
    ) -> None:
        self.provider_key = provider_key
        self.result = result
        self.calls: list[tuple[object, UUID, UUID, date, str]] = []

    def resolve_metric(
        self,
        db: object,
        *,
        user_id: UUID,
        source_instance_id: UUID,
        local_date: date,
        metric_key: str,
    ) -> ProviderCandidate:
        self.calls.append((db, user_id, source_instance_id, local_date, metric_key))
        if isinstance(self.result, BaseException):
            raise self.result
        if callable(self.result):
            return self.result(
                db,
                user_id=user_id,
                source_instance_id=source_instance_id,
                local_date=local_date,
                metric_key=metric_key,
            )
        return self.result


def _request(
    *,
    provider_key: str = "yazio",
    user_id: UUID = USER_ID,
    source_instance_id: UUID = SOURCE_A,
    local_date: date = LOCAL_DATE,
    metric_key: str = METRIC_KEY,
    registry: Mapping[str, SpyResolver] | None = None,
) -> ProviderCandidate:
    return resolve_provider_metric(
        object(),
        provider_key=provider_key,
        user_id=user_id,
        source_instance_id=source_instance_id,
        local_date=local_date,
        metric_key=metric_key,
        resolver_registry=registry,
    )


def test_yazio_registry_calls_existing_b2_resolver_once(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import yazio_nutrition_resolution

    calls: list[tuple[object, UUID, UUID, date, str]] = []
    expected = _candidate()

    def fake_resolve(
        db: object,
        *,
        user_id: UUID,
        source_instance_id: UUID,
        local_date: date,
        metric_key: str,
    ) -> ProviderCandidate:
        calls.append((db, user_id, source_instance_id, local_date, metric_key))
        return expected

    monkeypatch.setattr(yazio_nutrition_resolution, "resolve_yazio_metric", fake_resolve)

    result = _request()

    assert result is expected
    assert len(calls) == 1
    assert calls[0][1:] == (USER_ID, SOURCE_A, LOCAL_DATE, METRIC_KEY)


def test_unknown_provider_fails_closed() -> None:
    with pytest.raises(ProviderNotAvailableError):
        _request(provider_key="google_health")


def test_production_registry_contains_only_yazio() -> None:
    assert tuple(PROVIDER_RESOLVERS) == ("yazio",)


def test_provider_scope_mismatch_fails_closed() -> None:
    resolver = SpyResolver("fake", _candidate(provider_key="other"))
    with pytest.raises(ProviderCandidateContractError, match="provider_key"):
        _request(provider_key="fake", registry={"fake": resolver})


def test_user_scope_mismatch_fails_closed() -> None:
    resolver = SpyResolver("fake", _candidate(provider_key="fake", user_id=OTHER_USER_ID))
    with pytest.raises(ProviderCandidateContractError, match="user_id"):
        _request(provider_key="fake", registry={"fake": resolver})


def test_date_scope_mismatch_fails_closed() -> None:
    resolver = SpyResolver("fake", _candidate(provider_key="fake", local_date=date(2026, 9, 11)))
    with pytest.raises(ProviderCandidateContractError, match="local_date"):
        _request(provider_key="fake", registry={"fake": resolver})


def test_metric_scope_mismatch_fails_closed() -> None:
    resolver = SpyResolver("fake", _candidate(provider_key="fake", metric_key="fat_g"))
    with pytest.raises(ProviderCandidateContractError, match="metric_key"):
        _request(provider_key="fake", registry={"fake": resolver})

def test_non_candidate_resolver_output_fails_closed() -> None:
    resolver = SpyResolver("fake", "not-a-provider-candidate")  # type: ignore[arg-type]
    with pytest.raises(ProviderCandidateContractError, match="ProviderCandidate"):
        _request(provider_key="fake", registry={"fake": resolver})


def test_resolver_invariant_error_is_not_converted_to_missing() -> None:
    expected_error = RuntimeError("resolver invariant")
    resolver = SpyResolver("fake", expected_error)
    with pytest.raises(RuntimeError, match="resolver invariant"):
        _request(provider_key="fake", registry={"fake": resolver})


def test_two_providers_are_collected_without_selection() -> None:
    yazio = _candidate(value=Decimal("10"))
    fake = _candidate(provider_key="fake", value=Decimal("20"))
    registry = {
        "fake": SpyResolver("fake", fake),
        "yazio": SpyResolver("yazio", yazio),
    }

    result = collect_provider_candidates(
        object(),
        provider_keys=["fake", "yazio"],
        user_id=USER_ID,
        source_instance_id=SOURCE_A,
        local_date=LOCAL_DATE,
        metric_key=METRIC_KEY,
        resolver_registry=registry,
    )

    assert list(result) == ["fake", "yazio"]
    assert result == {"fake": fake, "yazio": yazio}
    assert result["fake"].value == Decimal("20")
    assert result["yazio"].value == Decimal("10")


def test_partial_and_complete_candidates_are_both_preserved() -> None:
    partial = _candidate(provider_key="fake", coverage_state=CoverageState.PARTIAL)
    complete = _candidate(coverage_state=CoverageState.COMPLETE)
    result = collect_provider_candidates(
        object(),
        provider_keys=["fake", "yazio"],
        user_id=USER_ID,
        source_instance_id=SOURCE_A,
        local_date=LOCAL_DATE,
        metric_key=METRIC_KEY,
        resolver_registry={
            "fake": SpyResolver("fake", partial),
            "yazio": SpyResolver("yazio", complete),
        },
    )

    assert result["fake"] is partial
    assert result["yazio"] is complete
    assert result["fake"].coverage_state is CoverageState.PARTIAL
    assert result["yazio"].coverage_state is CoverageState.COMPLETE


def test_uncertain_and_confirmed_candidates_are_both_preserved() -> None:
    uncertain = _candidate(provider_key="fake", lineage_state=LineageState.UNCERTAIN)
    confirmed = _candidate(lineage_state=LineageState.CONFIRMED)
    result = collect_provider_candidates(
        object(),
        provider_keys=["fake", "yazio"],
        user_id=USER_ID,
        source_instance_id=SOURCE_A,
        local_date=LOCAL_DATE,
        metric_key=METRIC_KEY,
        resolver_registry={
            "fake": SpyResolver("fake", uncertain),
            "yazio": SpyResolver("yazio", confirmed),
        },
    )

    assert result["fake"].lineage_state is LineageState.UNCERTAIN
    assert result["yazio"].lineage_state is LineageState.CONFIRMED


def test_missing_and_value_candidates_remain_distinguishable() -> None:
    missing = _candidate(provider_key="fake", value=None)
    valued = _candidate(value=Decimal("20"))
    result = collect_provider_candidates(
        object(),
        provider_keys=["fake", "yazio"],
        user_id=USER_ID,
        source_instance_id=SOURCE_A,
        local_date=LOCAL_DATE,
        metric_key=METRIC_KEY,
        resolver_registry={
            "fake": SpyResolver("fake", missing),
            "yazio": SpyResolver("yazio", valued),
        },
    )

    assert result["fake"].value is None
    assert result["fake"].reason_code is ReasonCode.ALL_SOURCES_MISSING
    assert result["yazio"].value == Decimal("20")


def test_duplicate_provider_keys_fail_before_resolution() -> None:
    resolver = SpyResolver("yazio", _candidate())
    with pytest.raises(ValueError, match="duplicate provider"):
        collect_provider_candidates(
            object(),
            provider_keys=["yazio", "yazio"],
            user_id=USER_ID,
            source_instance_id=SOURCE_A,
            local_date=LOCAL_DATE,
            metric_key=METRIC_KEY,
            resolver_registry={"yazio": resolver},
        )
    assert resolver.calls == []


def test_salt_uses_existing_unsupported_metric_candidate() -> None:
    unsupported = ProviderCandidate(
        provider_key="fake",
        user_id=USER_ID,
        local_date=LOCAL_DATE,
        metric_key="salt",
        value=None,
        unit=None,
        selected_granularity=None,
        presence_state=PresenceState.UNSUPPORTED,
        coverage_state=CoverageState.UNKNOWN,
        resolution_state=ResolutionState.UNRESOLVED,
        lineage_state=LineageState.UNKNOWN,
        source_lineage=(),
        reason_code=ReasonCode.UNSUPPORTED_METRIC,
    )
    resolver = SpyResolver("fake", unsupported)

    result = _request(
        provider_key="fake",
        metric_key="salt",
        registry={"fake": resolver},
    )

    assert result.reason_code is ReasonCode.UNSUPPORTED_METRIC
    assert resolver.calls[0][-1] == "salt"


def test_provider_specific_metric_names_are_rejected() -> None:
    resolver = SpyResolver("fake", _candidate())
    with pytest.raises(ValueError, match="unsupported metric"):
        _request(provider_key="fake", metric_key="energy", registry={"fake": resolver})
    assert resolver.calls == []


def test_collection_is_deterministic_for_same_inputs() -> None:
    first = _candidate(provider_key="fake")
    second = _candidate(provider_key="yazio")
    registry = {
        "fake": SpyResolver("fake", first),
        "yazio": SpyResolver("yazio", second),
    }

    result_a = collect_provider_candidates(
        object(),
        provider_keys=["fake", "yazio"],
        user_id=USER_ID,
        source_instance_id=SOURCE_A,
        local_date=LOCAL_DATE,
        metric_key=METRIC_KEY,
        resolver_registry=registry,
    )
    result_b = collect_provider_candidates(
        object(),
        provider_keys=["fake", "yazio"],
        user_id=USER_ID,
        source_instance_id=SOURCE_A,
        local_date=LOCAL_DATE,
        metric_key=METRIC_KEY,
        resolver_registry=registry,
    )

    assert list(result_a) == list(result_b) == ["fake", "yazio"]
    assert result_a == result_b


def test_source_instance_is_forwarded_without_cross_source_merge() -> None:
    resolver = SpyResolver("yazio", _candidate())

    _request(source_instance_id=SOURCE_A, registry={"yazio": resolver})
    _request(source_instance_id=SOURCE_B, registry={"yazio": resolver})

    assert [call[2] for call in resolver.calls] == [SOURCE_A, SOURCE_B]
    assert len(resolver.calls) == 2
