from __future__ import annotations

import inspect
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

import pytest

from app.nutrition.projection import (
    CANONICAL_METRIC_KEYS,
    METRIC_REGISTRY_VERSION,
    PROJECTION_ALGORITHM_VERSION,
    WATERMARK_FORMAT_VERSION,
    ConsumptionEventToken,
    DailyProjectionBuildInput,
    FieldObservationToken,
    FoodSnapshotToken,
    IdentityLinkToken,
    ProjectionInputManifest,
    SourceObservationToken,
    TombstoneToken,
    compute_input_watermark,
)
from app.source_priority.contracts import (
    AppliedPriorityScope,
    PriorityPolicySnapshot,
    PriorityReasonCode,
    PrioritySelection,
)

USER_ID = UUID("11111111-1111-1111-1111-111111111111")
OTHER_USER_ID = UUID("22222222-2222-2222-2222-222222222222")
LOCAL_DATE = date(2026, 9, 11)
POLICY_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
OBSERVATION_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
EVENT_ID = UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
FIELD_ID = UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")
SNAPSHOT_ID = UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee")
LINK_ID = UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")
TOMBSTONE_ID = UUID("12121212-1212-1212-1212-121212121212")
FINGERPRINT = "a" * 64
PAYLOAD_HASH = "b" * 64
CONTENT_HASH = "c" * 64


def _policy(*, policy_id: UUID = POLICY_ID, version: int = 1) -> PriorityPolicySnapshot:
    return PriorityPolicySnapshot(
        policy_id=policy_id,
        user_id=USER_ID,
        version=version,
        effective_from=datetime(2026, 1, 1, tzinfo=UTC),
        rules=(),
    )


def _selection(metric_key: str, policy: PriorityPolicySnapshot | None) -> PrioritySelection:
    return PrioritySelection(
        user_id=USER_ID,
        local_date=LOCAL_DATE,
        metric_key=metric_key,
        policy=policy,
        applied_scope=AppliedPriorityScope.NONE,
        selected_candidate=None,
        selected_role=None,
        reason_code=(
            PriorityReasonCode.PRIORITY_POLICY_MISSING
            if policy is None
            else PriorityReasonCode.NO_APPLICABLE_PRIORITY_RULE
        ),
        dispositions=(),
    )


def _manifest(
    *,
    policy: PriorityPolicySnapshot | None = None,
    tokens: tuple[object, ...] = (),
    algorithm: str = PROJECTION_ALGORITHM_VERSION,
    metric_registry: str = METRIC_REGISTRY_VERSION,
    watermark_format: str = WATERMARK_FORMAT_VERSION,
) -> ProjectionInputManifest:
    return ProjectionInputManifest(
        user_id=USER_ID,
        local_date=LOCAL_DATE,
        projection_algorithm_version=algorithm,
        metric_registry_version=metric_registry,
        watermark_format_version=watermark_format,
        policy=policy,
        relevant_rules=(),
        technical_evidence=tokens,
    )


def _build_input(
    *,
    policy: PriorityPolicySnapshot | None = None,
    manifest: ProjectionInputManifest | None = None,
    selections: tuple[PrioritySelection, ...] | None = None,
) -> DailyProjectionBuildInput:
    return DailyProjectionBuildInput(
        user_id=USER_ID,
        local_date=LOCAL_DATE,
        selections=selections
        or tuple(_selection(metric_key, policy) for metric_key in CANONICAL_METRIC_KEYS),
        input_manifest=manifest or _manifest(policy=policy),
    )


def test_b5_versions_and_metric_registry_are_stable_and_exactly_seven_metrics() -> None:
    assert PROJECTION_ALGORITHM_VERSION == "nutrition-daily-v1"
    assert METRIC_REGISTRY_VERSION == "nutrition-metrics-v1"
    assert WATERMARK_FORMAT_VERSION == "nutrition-watermark-v2"
    assert len(CANONICAL_METRIC_KEYS) == 7
    assert "salt" not in CANONICAL_METRIC_KEYS


def test_build_input_normalizes_selection_order_and_rejects_metric_shape_errors() -> None:
    policy = _policy()
    ordered = tuple(_selection(metric_key, policy) for metric_key in reversed(CANONICAL_METRIC_KEYS))
    build_input = _build_input(policy=policy, selections=ordered)

    assert tuple(item.metric_key for item in build_input.selections) == CANONICAL_METRIC_KEYS

    with pytest.raises(ValueError, match="exactly seven"):
        _build_input(policy=policy, selections=ordered[:-1])
    with pytest.raises(ValueError, match="duplicate metric"):
        _build_input(
            policy=policy,
            selections=(*ordered[:-1], ordered[0]),
        )
    with pytest.raises(ValueError, match="canonical nutrition metric"):
        _build_input(
            policy=policy,
            selections=tuple(
                _selection(metric_key, policy)
                for metric_key in (*CANONICAL_METRIC_KEYS[:-1], "salt")
            ),
        )


def test_build_input_rejects_mixed_policy_versions_and_scope() -> None:
    first = _policy(version=1)
    second = _policy(version=2)
    selections = tuple(
        _selection(metric_key, first if index else second)
        for index, metric_key in enumerate(CANONICAL_METRIC_KEYS)
    )

    with pytest.raises(ValueError, match="same policy"):
        _build_input(policy=first, selections=selections)

    other_policy = PriorityPolicySnapshot(
        policy_id=POLICY_ID,
        user_id=OTHER_USER_ID,
        version=1,
        effective_from=datetime(2026, 1, 1, tzinfo=UTC),
        rules=(),
    )
    with pytest.raises(ValueError, match="user_id"):
        _build_input(policy=other_policy)


def test_technical_tokens_are_immutable_scoped_and_have_no_raw_value_field() -> None:
    token_types = (
        SourceObservationToken,
        ConsumptionEventToken,
        FieldObservationToken,
        FoodSnapshotToken,
        IdentityLinkToken,
        TombstoneToken,
    )
    for token_type in token_types:
        assert "value" not in inspect.signature(token_type).parameters

    token = SourceObservationToken(
        source_observation_id=OBSERVATION_ID,
        source_revision=3,
        observation_fingerprint=FINGERPRINT,
        payload_hash=PAYLOAD_HASH,
        provider_key="yazio",
        metric_key="protein_g",
    )
    with pytest.raises((AttributeError, TypeError)):
        token.source_revision = 4  # type: ignore[misc]

    with pytest.raises(ValueError, match="non-zero"):
        SourceObservationToken(
            source_observation_id=UUID(int=0),
            source_revision=1,
            observation_fingerprint=FINGERPRINT,
        )
    with pytest.raises(ValueError, match="revision"):
        ConsumptionEventToken(event_id=EVENT_ID, revision=0)
    with pytest.raises(ValueError, match="hash"):
        FoodSnapshotToken(snapshot_id=SNAPSHOT_ID, content_hash="not-a-hash")
    with pytest.raises(ValueError, match="timezone-aware"):
        TombstoneToken(
            tombstone_id=TOMBSTONE_ID,
            observed_at=datetime(2026, 9, 11),
        )


def test_identical_tokens_deduplicate_and_conflicting_duplicate_ids_fail_closed() -> None:
    token = FieldObservationToken(field_observation_id=FIELD_ID, provider_key="yazio")
    manifest = _manifest(tokens=(token, token))
    assert manifest.technical_evidence == (token,)

    with pytest.raises(ValueError, match="duplicate token"):
        _manifest(
            tokens=(
                SourceObservationToken(
                    source_observation_id=OBSERVATION_ID,
                    source_revision=1,
                    observation_fingerprint=FINGERPRINT,
                ),
                SourceObservationToken(
                    source_observation_id=OBSERVATION_ID,
                    source_revision=2,
                    observation_fingerprint=FINGERPRINT,
                ),
            )
        )


def test_watermark_is_sha256_and_independent_of_token_order() -> None:
    tokens = (
        SourceObservationToken(
            source_observation_id=OBSERVATION_ID,
            source_revision=1,
            observation_fingerprint=FINGERPRINT,
            payload_hash=PAYLOAD_HASH,
        ),
        FieldObservationToken(field_observation_id=FIELD_ID),
    )
    first = compute_input_watermark(_manifest(tokens=tokens))
    second = compute_input_watermark(_manifest(tokens=tuple(reversed(tokens))))

    assert first == second
    assert first.startswith("sha256:")
    assert len(first) == len("sha256:") + 64
    assert all(character in "0123456789abcdef" for character in first.removeprefix("sha256:"))


def test_watermark_changes_for_each_relevant_input_but_not_policy_at() -> None:
    base = _manifest(
        policy=_policy(),
        tokens=(
            SourceObservationToken(
                source_observation_id=OBSERVATION_ID,
                source_revision=1,
                observation_fingerprint=FINGERPRINT,
                payload_hash=PAYLOAD_HASH,
            ),
            ConsumptionEventToken(event_id=EVENT_ID, revision=1),
            FieldObservationToken(field_observation_id=FIELD_ID),
            FoodSnapshotToken(snapshot_id=SNAPSHOT_ID, content_hash=CONTENT_HASH),
            IdentityLinkToken(link_id=LINK_ID, link_revision=1),
            TombstoneToken(
                tombstone_id=TOMBSTONE_ID,
                observed_at=datetime(2026, 9, 11, 12, tzinfo=UTC),
            ),
        ),
    )
    baseline = compute_input_watermark(base)
    changed = (
        _manifest(policy=_policy(policy_id=UUID("abababab-abab-abab-abab-abababababab")), tokens=base.technical_evidence),
        _manifest(policy=_policy(version=2), tokens=base.technical_evidence),
        _manifest(
            policy=base.policy,
            tokens=(
                SourceObservationToken(
                    source_observation_id=OBSERVATION_ID,
                    source_revision=2,
                    observation_fingerprint=FINGERPRINT,
                    payload_hash=PAYLOAD_HASH,
                ),
            ),
        ),
    )
    assert all(compute_input_watermark(item) != baseline for item in changed)


    assert compute_input_watermark(base) == compute_input_watermark(base)
    assert compute_input_watermark(base) == compute_input_watermark(
        ProjectionInputManifest(
            user_id=base.user_id,
            local_date=base.local_date,
            projection_algorithm_version=base.projection_algorithm_version,
            metric_registry_version=base.metric_registry_version,
            watermark_format_version=base.watermark_format_version,
            policy=PriorityPolicySnapshot(
                policy_id=base.policy.policy_id,
                user_id=base.policy.user_id,
                version=base.policy.version,
                effective_from=base.policy.effective_from,
                rules=(),
            ),
            relevant_rules=(),
            technical_evidence=base.technical_evidence,
        )
    )


def test_manifest_rejects_unsupported_versions() -> None:
    for field_name, value in (
        ("algorithm", "nutrition-daily-v2"),
        ("metric_registry", "nutrition-metrics-v2"),
        ("watermark_format", "nutrition-watermark-v3"),
    ):
        with pytest.raises(ValueError, match="unsupported"):
            _manifest(**{field_name: value})



def test_build_input_requires_manifest_scope_and_coherent_policy() -> None:
    policy = _policy()
    foreign_policy = PriorityPolicySnapshot(
        policy_id=policy.policy_id,
        user_id=OTHER_USER_ID,
        version=policy.version,
        effective_from=policy.effective_from,
        rules=(),
    )
    foreign_manifest = ProjectionInputManifest(
        user_id=OTHER_USER_ID,
        local_date=LOCAL_DATE,
        projection_algorithm_version=PROJECTION_ALGORITHM_VERSION,
        metric_registry_version=METRIC_REGISTRY_VERSION,
        watermark_format_version=WATERMARK_FORMAT_VERSION,
        policy=foreign_policy,
        relevant_rules=(),
        technical_evidence=(),
    )
    with pytest.raises(ValueError, match="manifest user_id"):
        _build_input(policy=policy, manifest=foreign_manifest)

    with pytest.raises(ValueError, match="policy"):
        _build_input(policy=policy, manifest=_manifest(policy=None))

def test_unused_provider_token_is_not_implicitly_added_to_manifest() -> None:
    token = FieldObservationToken(field_observation_id=uuid4(), provider_key="unconfigured")
    manifest = _manifest(tokens=())
    assert token not in manifest.technical_evidence
    assert compute_input_watermark(manifest) == compute_input_watermark(_manifest(tokens=()))
