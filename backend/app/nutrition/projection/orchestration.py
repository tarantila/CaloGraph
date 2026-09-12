from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Final, cast
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.nutrition.enums import ObservationKind
from app.nutrition.models import (
    NutritionConsumptionEvent,
    NutritionExternalIdentityLink,
    NutritionFieldObservation,
    NutritionFoodProfile,
    NutritionFoodSnapshot,
    NutritionSourceObservation,
    NutritionSourceTombstone,
)
from app.nutrition.resolution.contracts import MetricContribution, ProviderCandidate
from app.nutrition.resolution.metrics import CANONICAL_METRICS
from app.nutrition.resolution.providers import (
    PROVIDER_RESOLVERS,
    NutritionProviderResolver,
    collect_provider_candidates,
)
from app.nutrition.resolution.reasons import EvidenceKind
from app.nutrition.resolution.sources import (
    ProviderSourceBindings,
    ProviderSourceInstance,
    normalize_provider_sources,
    resolve_default_provider_sources,
    validate_provider_source_bindings,
)
from app.source_priority.application import get_effective_policy_snapshot
from app.source_priority.contracts import PriorityPolicySnapshot, PrioritySelection
from app.source_priority.selection import select_by_source_priority

from .contracts import (
    METRIC_REGISTRY_VERSION,
    PROJECTION_ALGORITHM_VERSION,
    WATERMARK_FORMAT_VERSION,
    DailyProjectionBuildInput,
    ProjectionInputManifest,
    ProjectionPersistenceResult,
)
from .persistence import persist_daily_projection
from .tokens import (
    ConsumptionEventToken,
    FieldObservationToken,
    FoodSnapshotToken,
    IdentityLinkToken,
    SourceObservationToken,
    TechnicalEvidenceToken,
    TombstoneToken,
)

_PROVIDER_KEY: Final = "yazio"
_PROVIDER_KEYS: Final = tuple(PROVIDER_RESOLVERS)
_MAX_ATTEMPTS: Final = 3
_PROJECTION_VERSION_CONSTRAINT: Final = "uq_nutrition_projections_user_date_version"


class NutritionProjectionTransactionError(RuntimeError):
    """B6 requires a session without a pre-existing transaction."""


class NutritionProjectionConcurrencyError(RuntimeError):
    """A bounded B6 whole-transaction retry budget was exhausted."""


def _normalize_policy_at(policy_at: datetime) -> datetime:
    if policy_at.tzinfo is None or policy_at.utcoffset() is None:
        raise ValueError("policy_at must be timezone-aware")
    return policy_at.astimezone(UTC)


def _utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class _ScopedContribution:
    binding: ProviderSourceInstance
    contribution: MetricContribution


def _resolve_provider_sources(
    db: Session,
    *,
    user_id: UUID,
    explicit_provider_sources: ProviderSourceBindings
    | Sequence[ProviderSourceInstance]
    | None,
    explicit_source_instance_id: UUID | None,
    resolver_registry: Mapping[str, NutritionProviderResolver] | None,
) -> ProviderSourceBindings:
    if explicit_provider_sources is not None and explicit_source_instance_id is not None:
        raise ValueError("provider_sources and source_instance_id are mutually exclusive")
    if explicit_provider_sources is not None:
        bindings = normalize_provider_sources(explicit_provider_sources)
        validate_provider_source_bindings(
            db,
            user_id=user_id,
            bindings=bindings,
            resolver_registry=resolver_registry,
        )
        return bindings
    if explicit_source_instance_id is not None:
        bindings = ProviderSourceBindings(
            (ProviderSourceInstance(_PROVIDER_KEY, explicit_source_instance_id),)
        )
        validate_provider_source_bindings(
            db,
            user_id=user_id,
            bindings=bindings,
            resolver_registry=resolver_registry,
        )
        return bindings
    bindings = resolve_default_provider_sources(
        db,
        user_id=user_id,
        provider_keys=_PROVIDER_KEYS,
        resolver_registry=None,
    )
    if not bindings:
        raise ValueError("YAZIO source instance is not configured")
    return bindings


def _collect_selections(
    db: Session,
    *,
    user_id: UUID,
    provider_sources: ProviderSourceBindings,
    local_date: date,
    policy: PriorityPolicySnapshot | None,
    resolver_registry: Mapping[str, NutritionProviderResolver] | None,
) -> tuple[PrioritySelection, ...]:
    selections: list[PrioritySelection] = []
    for metric_key in CANONICAL_METRICS:
        candidates = collect_provider_candidates(
            db,
            provider_sources=provider_sources,
            user_id=user_id,
            local_date=local_date,
            metric_key=metric_key,
            resolver_registry=resolver_registry,
        )
        selections.append(
            select_by_source_priority(
                user_id=user_id,
                local_date=local_date,
                metric_key=metric_key,
                candidates=candidates,
                policy=policy,
            )
        )
    return tuple(selections)


def _path_contributions(
    selections: Iterable[PrioritySelection],
    *,
    provider_sources: ProviderSourceBindings,
) -> tuple[_ScopedContribution, ...]:
    contributions: list[_ScopedContribution] = []
    seen: set[tuple[str, UUID, UUID, UUID, str]] = set()
    for selection in selections:
        for disposition in selection.dispositions:
            if disposition.rule_id is None or disposition.candidate is None:
                continue
            candidate: ProviderCandidate = disposition.candidate
            binding = provider_sources.for_provider(candidate.provider_key)
            if binding is None:
                raise ValueError("candidate provider has no source instance binding")
            candidate_evidence = (*candidate.source_lineage, *candidate.diagnostic_evidence)
            for contribution in candidate_evidence:
                identity = (
                    binding.provider_key,
                    binding.source_instance_id,
                    contribution.evidence_id,
                    contribution.source_observation_id,
                    contribution.metric_key,
                )
                if identity not in seen:
                    seen.add(identity)
                    contributions.append(_ScopedContribution(binding, contribution))
    return tuple(contributions)


def _load_relevant_tokens(
    db: Session,
    *,
    user_id: UUID,
    local_date: date,
    source_instance_id: UUID | None = None,
    contributions: tuple[_ScopedContribution | MetricContribution, ...],
) -> tuple[TechnicalEvidenceToken, ...]:
    if source_instance_id is None:
        scoped_contributions = tuple(
            contribution
            for contribution in contributions
            if isinstance(contribution, _ScopedContribution)
        )
        if len(scoped_contributions) != len(contributions):
            raise ValueError("manifest contributions must include provider/source scope")
    else:
        if any(isinstance(contribution, _ScopedContribution) for contribution in contributions):
            raise ValueError("manifest contributions cannot mix legacy and scoped values")
        scoped_contributions = tuple(
            _ScopedContribution(
                ProviderSourceInstance(_PROVIDER_KEY, source_instance_id),
                cast(MetricContribution, contribution),
            )
            for contribution in contributions
        )
    source_ids = {item.contribution.source_observation_id for item in scoped_contributions}
    event_ids = {
        item.contribution.evidence_id
        for item in scoped_contributions
        if item.contribution.evidence_kind is EvidenceKind.CONSUMPTION_EVENT
    }
    field_ids = {
        item.contribution.evidence_id
        for item in scoped_contributions
        if item.contribution.evidence_kind is EvidenceKind.FIELD_OBSERVATION
    }
    allowed_scopes = {
        (item.binding.provider_key, item.binding.source_instance_id)
        for item in scoped_contributions
    }

    def scope_allowed(provider_key: str, source_instance_id: UUID) -> bool:
        return (provider_key, source_instance_id) in allowed_scopes

    source_rows = (
        list(
            db.scalars(
                select(NutritionSourceObservation).where(
                    NutritionSourceObservation.user_id == user_id,
                    NutritionSourceObservation.id.in_(source_ids),
                )
            )
        )
        if source_ids
        else []
    )
    source_by_id = {source.id: source for source in source_rows}
    if set(source_by_id) != source_ids:
        raise ValueError("manifest source evidence is outside the user scope")
    if any(
        not scope_allowed(source.provider_key, source.source_instance_id)
        for source in source_by_id.values()
    ):
        raise ValueError("manifest source evidence is outside the date/provider scope")
    for item in scoped_contributions:
        source = source_by_id.get(item.contribution.source_observation_id)
        if source is None:
            continue
        if (
            source.provider_key != item.binding.provider_key
            or source.source_instance_id != item.binding.source_instance_id
        ):
            raise ValueError("manifest source evidence has inconsistent provider/source lineage")
        if (
            item.contribution.evidence_kind is EvidenceKind.SOURCE_OBSERVATION
            and item.contribution.evidence_id != item.contribution.source_observation_id
        ):
            raise ValueError("manifest source evidence has inconsistent evidence identity")

    event_filters = []
    if event_ids:
        event_filters.append(NutritionConsumptionEvent.id.in_(event_ids))
    if source_ids:
        event_filters.append(NutritionConsumptionEvent.source_observation_id.in_(source_ids))
    event_rows = (
        list(
            db.scalars(
                select(NutritionConsumptionEvent).where(
                    NutritionConsumptionEvent.user_id == user_id,
                    or_(*event_filters),
                )
            )
        )
        if event_filters
        else []
    )
    event_keys_by_scope = {
        (event.provider_key, event.source_instance_id, event.logical_event_key)
        for event in event_rows
        if event.logical_event_key is not None
        and scope_allowed(event.provider_key, event.source_instance_id)
    }
    if event_keys_by_scope:
        logical_keys = {key for _, _, key in event_keys_by_scope}
        event_rows.extend(
            db.scalars(
                select(NutritionConsumptionEvent).where(
                    NutritionConsumptionEvent.user_id == user_id,
                    NutritionConsumptionEvent.logical_event_key.in_(logical_keys),
                )
            )
        )
    events_by_id = {
        event.id: event
        for event in event_rows
        if scope_allowed(event.provider_key, event.source_instance_id)
    }
    current_event_ids = set(event_ids)
    current_event_ids.update(
        event.id
        for event in events_by_id.values()
        if event.source_observation_id in source_ids and event.local_date == local_date
    )
    if any(
        events_by_id[event_id].local_date != local_date
        for event_id in current_event_ids
        if event_id in events_by_id
    ):
        raise ValueError("manifest current event evidence is outside the date scope")
    for item in scoped_contributions:
        contribution = item.contribution
        if contribution.evidence_kind is not EvidenceKind.CONSUMPTION_EVENT:
            continue
        event = events_by_id.get(contribution.evidence_id)
        if event is None:
            continue
        if (
            event.source_observation_id != contribution.source_observation_id
            or event.provider_key != item.binding.provider_key
            or event.source_instance_id != item.binding.source_instance_id
        ):
            raise ValueError("manifest event evidence has inconsistent source lineage")
    source_ids.update(event.source_observation_id for event in events_by_id.values())
    historical_event_source_ids: set[UUID] = set()
    for current_event_id in current_event_ids:
        cursor = events_by_id.get(current_event_id)
        visited: set[UUID] = set()
        while cursor is not None and cursor.supersedes_event_id is not None:
            if cursor.id in visited:
                break
            visited.add(cursor.id)
            previous = events_by_id.get(cursor.supersedes_event_id)
            if previous is None:
                break
            historical_event_source_ids.add(previous.source_observation_id)
            cursor = previous

    fields = (
        list(
            db.scalars(
                select(NutritionFieldObservation).where(
                    NutritionFieldObservation.user_id == user_id,
                    NutritionFieldObservation.id.in_(field_ids),
                )
            )
        )
        if field_ids
        else []
    )
    parent_ids = {
        field.derived_from_field_observation_id
        for field in fields
        if field.derived_from_field_observation_id is not None
    }
    parents = (
        list(
            db.scalars(
                select(NutritionFieldObservation).where(
                    NutritionFieldObservation.user_id == user_id,
                    NutritionFieldObservation.id.in_(parent_ids),
                )
            )
        )
        if parent_ids
        else []
    )
    fields_by_id = {field.id: field for field in (*fields, *parents)}
    if not field_ids.issubset(fields_by_id):
        raise ValueError("manifest field evidence is outside the user scope")
    for item in scoped_contributions:
        contribution = item.contribution
        if contribution.evidence_kind is not EvidenceKind.FIELD_OBSERVATION:
            continue
        field = fields_by_id.get(contribution.evidence_id)
        if field is None:
            continue
        if field.source_observation_id != contribution.source_observation_id:
            raise ValueError("manifest field evidence has inconsistent source lineage")
    source_ids.update(field.source_observation_id for field in fields_by_id.values())

    snapshot_ids = {
        event.food_snapshot_id
        for event in events_by_id.values()
        if event.food_snapshot_id is not None
    }
    snapshots = (
        list(
            db.scalars(
                select(NutritionFoodSnapshot).where(
                    NutritionFoodSnapshot.user_id == user_id,
                    NutritionFoodSnapshot.id.in_(snapshot_ids),
                )
            )
        )
        if snapshot_ids
        else []
    )
    snapshots_by_id = {snapshot.id: snapshot for snapshot in snapshots}
    if not snapshot_ids.issubset(snapshots_by_id):
        raise ValueError("manifest snapshot evidence is outside the user scope")
    profile_ids = {snapshot.food_profile_id for snapshot in snapshots_by_id.values()}
    profiles = (
        list(
            db.scalars(
                select(NutritionFoodProfile).where(
                    NutritionFoodProfile.user_id == user_id,
                    NutritionFoodProfile.id.in_(profile_ids),
                )
            )
        )
        if profile_ids
        else []
    )
    profiles_by_id = {profile.id: profile for profile in profiles}
    if set(profiles_by_id) != profile_ids:
        raise ValueError("manifest snapshot profile is outside the user scope")
    if any(
        not scope_allowed(profile.provider_key, profile.source_instance_id)
        for profile in profiles_by_id.values()
    ):
        raise ValueError("manifest snapshot profile is outside the date/provider scope")
    source_ids.update(snapshot.source_observation_id for snapshot in snapshots)
    global_reference_source_ids = {
        snapshot.source_observation_id for snapshot in snapshots_by_id.values()
    }

    if source_ids != set(source_by_id):
        extra_sources = (
            list(
                db.scalars(
                    select(NutritionSourceObservation).where(
                        NutritionSourceObservation.user_id == user_id,
                        NutritionSourceObservation.id.in_(source_ids - set(source_by_id)),
                    )
                )
            )
            if source_ids - set(source_by_id)
            else []
        )
        source_rows.extend(extra_sources)
        source_by_id = {source.id: source for source in source_rows}
    if set(source_by_id) != source_ids:
        raise ValueError("manifest derived source evidence is outside the user scope")
    if any(
        not scope_allowed(source.provider_key, source.source_instance_id)
        or (
            source.id in global_reference_source_ids
            and (
                source.observation_kind != ObservationKind.PRODUCT_PROFILE.value
                or source.local_date is not None
            )
        )
        or (
            source.id not in global_reference_source_ids
            and source.id not in historical_event_source_ids
            and source.local_date != local_date
        )
        for source in source_by_id.values()
    ):
        raise ValueError("manifest derived source evidence is outside the date/provider scope")

    links = (
        list(
            db.scalars(
                select(NutritionExternalIdentityLink).where(
                    NutritionExternalIdentityLink.user_id == user_id,
                    NutritionExternalIdentityLink.consumption_event_id.in_(set(events_by_id)),
                )
            )
        )
        if events_by_id
        else []
    )
    identity_ids = {link.external_identity_id for link in links}
    tombstones = list(
        db.scalars(
            select(NutritionSourceTombstone).where(
                NutritionSourceTombstone.user_id == user_id,
            )
        )
    )
    relevant_tombstones = [
        tombstone
        for tombstone in tombstones
        if scope_allowed(tombstone.provider_key, tombstone.source_instance_id)
        and (
            tombstone.source_observation_id in source_ids
            or tombstone.external_identity_id in identity_ids
            or (
                tombstone.source_observation_id is None
                and any(
                    source.source_namespace == tombstone.source_namespace
                    and source.source_record_id is not None
                    and source.source_record_id == tombstone.source_record_id
                    for source in source_by_id.values()
                )
            )
        )
    ]

    tokens: list[TechnicalEvidenceToken] = [
        SourceObservationToken(
            source_observation_id=source.id,
            source_revision=source.source_revision,
            observation_fingerprint=source.observation_fingerprint,
            payload_hash=source.payload_hash,
            provider_key=source.provider_key,
        )
        for source in source_by_id.values()
    ]
    tokens.extend(
        ConsumptionEventToken(
            event_id=event.id,
            revision=event.revision,
            provider_key=event.provider_key,
        )
        for event in events_by_id.values()
    )
    tokens.extend(
        FieldObservationToken(
            field_observation_id=field.id,
            provider_key=source_by_id[field.source_observation_id].provider_key,
        )
        for field in fields_by_id.values()
    )
    tokens.extend(
        FoodSnapshotToken(
            snapshot_id=snapshot.id,
            content_hash=snapshot.content_hash,
            provider_key=profiles_by_id[snapshot.food_profile_id].provider_key,
        )
        for snapshot in snapshots_by_id.values()
    )
    tokens.extend(
        IdentityLinkToken(
            link_id=link.id,
            link_revision=link.link_revision,
            provider_key=events_by_id[link.consumption_event_id].provider_key,
        )
        for link in links
        if link.consumption_event_id is not None
        and link.consumption_event_id in events_by_id
    )
    tokens.extend(
        TombstoneToken(
            tombstone_id=tombstone.id,
            observed_at=_utc_datetime(tombstone.observed_at),
            provider_key=tombstone.provider_key,
        )
        for tombstone in relevant_tombstones
    )
    return tuple(tokens)


def _build_manifest(
    db: Session,
    *,
    user_id: UUID,
    local_date: date,
    provider_sources: ProviderSourceBindings,
    policy: PriorityPolicySnapshot | None,
    selections: tuple[PrioritySelection, ...],
) -> ProjectionInputManifest:
    expected_rule_ids = {
        disposition.rule_id
        for selection in selections
        for disposition in selection.dispositions
        if disposition.rule_id is not None
    }
    relevant_rules = (
        tuple(rule for rule in policy.rules if rule.rule_id in expected_rule_ids)
        if policy is not None
        else ()
    )
    relevant_provider_keys = {rule.provider_key for rule in relevant_rules}
    relevant_provider_sources = ProviderSourceBindings(
        tuple(
            binding
            for binding in provider_sources
            if binding.provider_key in relevant_provider_keys
        )
    )
    contributions = _path_contributions(
        selections,
        provider_sources=provider_sources,
    )
    tokens = _load_relevant_tokens(
        db,
        user_id=user_id,
        local_date=local_date,
        contributions=contributions,
    )
    source_token_ids = {
        token.source_observation_id
        for token in tokens
        if isinstance(token, SourceObservationToken)
    }
    required_source_ids = {
        item.contribution.source_observation_id for item in contributions
    }
    if not required_source_ids.issubset(source_token_ids):
        raise ValueError("manifest is missing candidate lineage source evidence")
    return ProjectionInputManifest(
        user_id=user_id,
        local_date=local_date,
        projection_algorithm_version=PROJECTION_ALGORITHM_VERSION,
        metric_registry_version=METRIC_REGISTRY_VERSION,
        watermark_format_version=WATERMARK_FORMAT_VERSION,
        policy=policy,
        relevant_rules=relevant_rules,
        relevant_provider_sources=relevant_provider_sources,
        technical_evidence=tokens,
    )


def _run_attempt(
    db: Session,
    *,
    user_id: UUID,
    local_date: date,
    policy_at: datetime,
    source_instance_id: UUID | None,
    provider_sources: ProviderSourceBindings
    | Sequence[ProviderSourceInstance]
    | None,
    resolver_registry: Mapping[str, NutritionProviderResolver] | None,
) -> ProjectionPersistenceResult:
    resolved_provider_sources = _resolve_provider_sources(
        db,
        user_id=user_id,
        explicit_provider_sources=provider_sources,
        explicit_source_instance_id=source_instance_id,
        resolver_registry=resolver_registry,
    )
    policy = get_effective_policy_snapshot(db, user_id, policy_at)
    selections = _collect_selections(
        db,
        user_id=user_id,
        provider_sources=resolved_provider_sources,
        local_date=local_date,
        policy=policy,
        resolver_registry=resolver_registry,
    )
    manifest = _build_manifest(
        db,
        user_id=user_id,
        local_date=local_date,
        provider_sources=resolved_provider_sources,
        policy=policy,
        selections=selections,
    )
    return persist_daily_projection(
        db,
        build_input=DailyProjectionBuildInput(
            user_id=user_id,
            local_date=local_date,
            selections=selections,
            input_manifest=manifest,
        ),
    )


def _dbapi_sqlstate(error: BaseException) -> str | None:
    original = getattr(error, "orig", None)
    for attribute in ("sqlstate", "pgcode"):
        value = getattr(original, attribute, None)
        if isinstance(value, str):
            return value
    return None


def _dbapi_constraint_name(error: BaseException) -> str | None:
    original = getattr(error, "orig", None)
    diagnostic = getattr(original, "diag", None)
    value = getattr(diagnostic, "constraint_name", None)
    return value if isinstance(value, str) else None


def _is_retryable_database_error(db: Session, error: BaseException) -> bool:
    if db.get_bind().dialect.name != "postgresql":
        return False
    if not isinstance(error, DBAPIError):
        return False
    return _dbapi_sqlstate(error) in {"40001", "40P01"} or (
        isinstance(error, IntegrityError)
        and _dbapi_constraint_name(error) == _PROJECTION_VERSION_CONSTRAINT
    )


def _begin_attempt(db: Session) -> None:
    if db.get_bind().dialect.name == "postgresql":
        db.connection(execution_options={"isolation_level": "REPEATABLE READ"})
    else:
        db.connection()

def rebuild_nutrition_day(
    db: Session,
    *,
    user_id: UUID,
    local_date: date,
    policy_at: datetime,
    source_instance_id: UUID | None = None,
    provider_sources: ProviderSourceBindings
    | Sequence[ProviderSourceInstance]
    | None = None,
    resolver_registry: Mapping[str, NutritionProviderResolver] | None = None,
) -> ProjectionPersistenceResult:
    """Build and persist exactly one user's canonical nutrition day."""
    if db.in_transaction():
        raise NutritionProjectionTransactionError("B6 requires a clean input session")
    normalized_policy_at = _normalize_policy_at(policy_at)

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            _begin_attempt(db)
            result = _run_attempt(
                db,
                user_id=user_id,
                local_date=local_date,
                policy_at=normalized_policy_at,
                source_instance_id=source_instance_id,
                provider_sources=provider_sources,
                resolver_registry=resolver_registry,
            )
            db.commit()
            return result
        except Exception as error:
            db.rollback()
            if not _is_retryable_database_error(db, error):
                raise
            if attempt == _MAX_ATTEMPTS:
                raise NutritionProjectionConcurrencyError(
                    "nutrition projection concurrency retry budget exhausted"
                ) from error

    raise AssertionError("unreachable")
