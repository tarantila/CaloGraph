"""Read-only canonical nutrition provider discovery and range metadata."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from types import MappingProxyType
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import and_, exists, func, not_, or_, select
from sqlalchemy.orm import Session, aliased

from app.micronutrients import MICRONUTRIENT_METRIC_TYPES
from app.models import GoogleHealthConnection, YazioConnection
from app.nutrition.enums import (
    CoverageState,
    LineageState,
    ObservationKind,
    ObservationRole,
    PresenceState,
    ResolutionState,
)
from app.nutrition.models import (
    NutritionConsumptionEvent,
    NutritionExternalIdentityLink,
    NutritionFieldObservation,
    NutritionIngestionRun,
    NutritionSourceObservation,
    NutritionSourceTombstone,
)
from app.nutrition.resolution.metrics import canonical_unit
from app.nutrition.resolution.read_context import NutritionEvidenceIndex


@dataclass(frozen=True, slots=True)
class NutritionProviderIdentity:
    provider_key: str

    def __post_init__(self) -> None:
        if not isinstance(self.provider_key, str):
            raise TypeError("provider_key must be a string")
        normalized = self.provider_key.strip().lower()
        if not normalized or any(character.isspace() for character in normalized):
            raise ValueError("provider_key must be a non-empty token")
        object.__setattr__(self, "provider_key", normalized)


@dataclass(frozen=True, slots=True)
class NutritionProviderDiscovery:
    providers: tuple[NutritionProviderIdentity, ...] = ()

    def __post_init__(self) -> None:
        providers = tuple(self.providers)
        if any(not isinstance(provider, NutritionProviderIdentity) for provider in providers):
            raise TypeError("providers must contain NutritionProviderIdentity values")
        if len({provider.provider_key for provider in providers}) != len(providers):
            raise ValueError("duplicate provider identity")
        object.__setattr__(
            self,
            "providers",
            tuple(sorted(providers, key=lambda provider: provider.provider_key)),
        )


@dataclass(frozen=True, slots=True)
class NutritionProviderMetadata:
    provider_key: str
    latest_evidence_observed_at: datetime

    def __post_init__(self) -> None:
        identity = NutritionProviderIdentity(self.provider_key)
        object.__setattr__(self, "provider_key", identity.provider_key)
        object.__setattr__(
            self,
            "latest_evidence_observed_at",
            _utc_datetime(self.latest_evidence_observed_at),
        )


@dataclass(frozen=True, slots=True)
class NutritionProviderMetadataSet:
    providers: tuple[NutritionProviderMetadata, ...] = ()

    def __post_init__(self) -> None:
        providers = tuple(self.providers)
        if any(not isinstance(provider, NutritionProviderMetadata) for provider in providers):
            raise TypeError("providers must contain NutritionProviderMetadata values")
        if len({provider.provider_key for provider in providers}) != len(providers):
            raise ValueError("duplicate provider metadata")
        object.__setattr__(
            self,
            "providers",
            tuple(sorted(providers, key=lambda provider: provider.provider_key)),
        )


@dataclass(frozen=True, slots=True)
class ProviderDiscoveryEvidence:
    provider_key: str
    latest_evidence_observed_at: datetime | None

    @property
    def available(self) -> bool:
        return self.latest_evidence_observed_at is not None

    def __post_init__(self) -> None:
        identity = NutritionProviderIdentity(self.provider_key)
        object.__setattr__(self, "provider_key", identity.provider_key)
        if self.latest_evidence_observed_at is not None:
            object.__setattr__(
                self,
                "latest_evidence_observed_at",
                _utc_datetime(self.latest_evidence_observed_at),
            )


class ProviderDiscoveryResolver(Protocol):
    provider_key: str

    def discover_global_evidence(
        self,
        db: Session,
        *,
        user_id: UUID,
    ) -> ProviderDiscoveryEvidence: ...

    def discover_range_evidence(
        self,
        db: Session,
        *,
        user_id: UUID,
        start: date,
        end: date,
    ) -> ProviderDiscoveryEvidence: ...


@dataclass(frozen=True, slots=True)
class _ProviderLifecycleRules:
    source_only_kinds: frozenset[str]
    event_namespaces: frozenset[str]
    event_kinds: frozenset[str]
    event_tombstone_namespaces: frozenset[str]
    source_tombstones_supported: bool
    identity_tombstones_supported: bool
    require_complete_run: bool
    event_coverage: frozenset[str]


_YAZIO_RULES = _ProviderLifecycleRules(
    source_only_kinds=frozenset({ObservationKind.PRODUCT_PROFILE.value, ObservationKind.DAILY_SUMMARY.value}),
    event_namespaces=frozenset({"yazio.consumed_item", "yazio.simple_product"}),
    event_kinds=frozenset({"product", "simple_product"}),
    event_tombstone_namespaces=frozenset({"yazio.consumed_item", "yazio.simple_product"}),
    source_tombstones_supported=True,
    identity_tombstones_supported=True,
    require_complete_run=False,
    event_coverage=frozenset({CoverageState.COMPLETE.value, CoverageState.PARTIAL.value}),
)

_GOOGLE_RULES = _ProviderLifecycleRules(
    source_only_kinds=frozenset(),
    event_namespaces=frozenset({"google_health.nutrition_log"}),
    event_kinds=frozenset({"product", "simple_product"}),
    event_tombstone_namespaces=frozenset(),
    source_tombstones_supported=False,
    identity_tombstones_supported=False,
    require_complete_run=True,
    event_coverage=frozenset({CoverageState.COMPLETE.value}),
)

_APPLE_RULES = _ProviderLifecycleRules(
    source_only_kinds=frozenset(),
    event_namespaces=frozenset({"apple_health.food_correlation"}),
    event_kinds=frozenset({"product"}),
    event_tombstone_namespaces=frozenset({"apple_health.food_correlation"}),
    source_tombstones_supported=True,
    identity_tombstones_supported=True,
    require_complete_run=False,
    event_coverage=frozenset({CoverageState.COMPLETE.value}),
)

_RULES: Mapping[str, _ProviderLifecycleRules] = MappingProxyType(
    {
        "yazio": _YAZIO_RULES,
        "google_health": _GOOGLE_RULES,
        "apple_health": _APPLE_RULES,
    }
)


def _utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _provider_registry() -> Mapping[str, object]:
    from .providers import NUTRIENT_READ_PROVIDER_RESOLVERS

    return NUTRIENT_READ_PROVIDER_RESOLVERS

def _validate_range(start: date, end: date) -> None:
    if type(start) is not date or type(end) is not date:
        raise TypeError("start and end must be date values")
    if start > end:
        raise ValueError("start must not be after end")


def _discovery_resolver(resolver: object) -> ProviderDiscoveryResolver | None:
    global_method = getattr(resolver, "discover_global_evidence", None)
    range_method = getattr(resolver, "discover_range_evidence", None)
    if not callable(global_method) or not callable(range_method):
        return None
    return resolver  # type: ignore[return-value]


def discover_nutrition_providers(
    db: Session,
    *,
    user_id: UUID,
    provider_registry: Mapping[str, object] | None = None,
) -> NutritionProviderDiscovery:
    """Discover globally evidenced providers without selecting one for analytics."""
    registry = _provider_registry() if provider_registry is None else provider_registry
    identities: list[NutritionProviderIdentity] = []
    for provider_key in sorted(registry):
        resolver = _discovery_resolver(registry[provider_key])
        if resolver is None:
            continue
        evidence = resolver.discover_global_evidence(db, user_id=user_id)
        if evidence.available:
            identities.append(NutritionProviderIdentity(evidence.provider_key))
    return NutritionProviderDiscovery(tuple(identities))


def discover_nutrition_provider_metadata(
    db: Session,
    *,
    user_id: UUID,
    start: date,
    end: date,
    provider_registry: Mapping[str, object] | None = None,
    provider_key: str | None = None,
    read_context: NutritionEvidenceIndex | None = None,
) -> NutritionProviderMetadataSet:
    """Read range-scoped provider metadata in bounded date windows."""
    _validate_range(start, end)
    normalized_provider_key = (
        NutritionProviderIdentity(provider_key).provider_key if provider_key is not None else None
    )
    registry = _provider_registry() if provider_registry is None else provider_registry
    context_provider_key = None
    if read_context is not None and read_context.matches(
        user_id=user_id,
        provider_key=read_context.provider_key,
        source_instance_id=read_context.source_instance_id,
        start=start,
        end=end,
    ):
        context_provider_key = read_context.provider_key
    provider_keys = (
        (normalized_provider_key,)
        if normalized_provider_key is not None
        else tuple(sorted(registry))
    )
    if context_provider_key not in provider_keys or context_provider_key not in registry:
        context_provider_key = None
    if normalized_provider_key is not None and context_provider_key == normalized_provider_key:
        assert read_context is not None
        latest = read_context.latest_evidence_observed_at
        return NutritionProviderMetadataSet(
            ()
            if latest is None
            else (NutritionProviderMetadata(normalized_provider_key, latest),)
        )
    if context_provider_key is not None:
        provider_keys = tuple(key for key in provider_keys if key != context_provider_key)
    metadata_by_provider: dict[str, NutritionProviderMetadata] = {}
    if context_provider_key is not None:
        assert read_context is not None
        latest = read_context.latest_evidence_observed_at
        if latest is not None:
            metadata_by_provider[context_provider_key] = NutritionProviderMetadata(
                context_provider_key, latest
            )
    if not provider_keys:
        return NutritionProviderMetadataSet(
            tuple(metadata_by_provider[key] for key in sorted(metadata_by_provider))
        )

    chunk_start = start
    while True:
        chunk_end = chunk_start + timedelta(days=min(30, (end - chunk_start).days))
        for current_provider_key in provider_keys:
            chunk_metadata = _discover_nutrition_provider_metadata_once(
                db,
                user_id=user_id,
                start=chunk_start,
                end=chunk_end,
                provider_registry=registry,
                provider_key=current_provider_key,
            )
            for metadata in chunk_metadata.providers:
                previous = metadata_by_provider.get(metadata.provider_key)
                if (
                    previous is None
                    or metadata.latest_evidence_observed_at > previous.latest_evidence_observed_at
                ):
                    metadata_by_provider[metadata.provider_key] = metadata
        if chunk_end == end:
            return NutritionProviderMetadataSet(
                tuple(metadata_by_provider[key] for key in sorted(metadata_by_provider))
            )
        chunk_start = chunk_end + timedelta(days=1)

def _discover_nutrition_provider_metadata_once(
    db: Session,
    *,
    user_id: UUID,
    start: date,
    end: date,
    provider_registry: Mapping[str, object] | None = None,
    provider_key: str | None = None,
) -> NutritionProviderMetadataSet:
    """Read range-scoped provider metadata for canonical micronutrient evidence."""
    _validate_range(start, end)
    registry = _provider_registry() if provider_registry is None else provider_registry
    provider_keys = (
        tuple(sorted(registry))
        if provider_key is None
        else (NutritionProviderIdentity(provider_key).provider_key,)
    )
    metadata: list[NutritionProviderMetadata] = []
    for provider_key in provider_keys:
        resolver = _discovery_resolver(registry.get(provider_key))
        if resolver is None:
            continue
        evidence = resolver.discover_range_evidence(
            db,
            user_id=user_id,
            start=start,
            end=end,
        )
        if evidence.available:
            assert evidence.latest_evidence_observed_at is not None
            metadata.append(
                NutritionProviderMetadata(
                    provider_key=evidence.provider_key,
                    latest_evidence_observed_at=evidence.latest_evidence_observed_at,
                )
            )
    return NutritionProviderMetadataSet(tuple(metadata))


def _owned_source_instance_ids(
    db: Session,
    *,
    user_id: UUID,
    provider_key: str,
) -> tuple[UUID, ...]:
    if provider_key == "yazio":
        return tuple(
            db.scalars(select(YazioConnection.id).where(YazioConnection.user_id == user_id)).all()
        )
    if provider_key == "google_health":
        return tuple(
            db.scalars(
                select(GoogleHealthConnection.id).where(GoogleHealthConnection.user_id == user_id)
            ).all()
        )
    if provider_key == "apple_health":
        from app.services.apple_health_nutrition_ingestion import apple_health_source_instance_id

        return (apple_health_source_instance_id(user_id),)
    return ()


def _same_source_identity(left: Any, right: Any) -> Any:
    return and_(
        left.user_id == right.user_id,
        left.provider_key == right.provider_key,
        left.source_instance_id == right.source_instance_id,
        left.source_namespace == right.source_namespace,
        left.source_record_id == right.source_record_id,
        left.source_record_id.is_not(None),
    )


def _source_current_condition(source: Any) -> Any:
    superseding = aliased(NutritionSourceObservation)
    return not_(
        exists(
            select(1).where(
                _same_source_identity(superseding, source),
                superseding.source_revision > source.source_revision,
            )
        )
    )


def _source_tombstone_condition(
    source: Any,
    *,
    rules: _ProviderLifecycleRules,
) -> Any:
    if not rules.source_tombstones_supported:
        return True
    tombstone = NutritionSourceTombstone
    return not_(
        exists(
            select(1).where(
                tombstone.user_id == source.user_id,
                tombstone.provider_key == source.provider_key,
                tombstone.source_instance_id == source.source_instance_id,
                or_(
                    tombstone.source_observation_id == source.id,
                    and_(
                        tombstone.source_observation_id.is_(None),
                        tombstone.source_namespace == source.source_namespace,
                        tombstone.source_record_id.is_not(None),
                        tombstone.source_record_id == source.source_record_id,
                    ),
                ),
            )
        )
    )


def _valid_field_condition(source: Any, *, provider_key: str) -> Any:
    field = NutritionFieldObservation
    metric_units = tuple(
        (metric_key, canonical_unit(metric_key))
        for metric_key in MICRONUTRIENT_METRIC_TYPES
        if canonical_unit(metric_key) is not None
    )
    unit_matches = or_(
        *(
            and_(field.metric_key == metric_key, field.canonical_unit == unit)
            for metric_key, unit in metric_units
        )
    )
    field_role = (
        ObservationRole.PROVIDER.value
        if provider_key == "yazio"
        else ObservationRole.CANONICAL.value
    )
    return exists(
        select(1).where(
            field.user_id == source.user_id,
            field.source_observation_id == source.id,
            field.metric_key.in_(MICRONUTRIENT_METRIC_TYPES),
            field.canonical_value.is_not(None),
            unit_matches,
            field.observation_role == field_role,
            field.presence_state.in_(
                (PresenceState.SUPPLIED.value, PresenceState.EXPLICIT_ZERO.value)
            ),
            field.coverage_state.in_(
                (CoverageState.COMPLETE.value, CoverageState.PARTIAL.value)
            ),
            field.resolution_state == ResolutionState.RESOLVED.value,
            field.lineage_state.in_((LineageState.CONFIRMED.value, LineageState.UNCERTAIN.value)),
        )
    )


def _source_state_conditions(source: Any) -> tuple[Any, ...]:
    return (
        source.presence_state.in_((PresenceState.SUPPLIED.value, PresenceState.EXPLICIT_ZERO.value)),
        source.coverage_state.in_((CoverageState.COMPLETE.value, CoverageState.PARTIAL.value)),
        source.resolution_state == ResolutionState.RESOLVED.value,
        source.lineage_state.in_((LineageState.CONFIRMED.value, LineageState.UNCERTAIN.value)),
    )


def _event_revision_chain_is_valid(events: Sequence[Any], current: Any) -> bool:
    by_id = {event.id: event for event in events}
    cursor = current
    visited: set[UUID] = set()
    while cursor.revision > 1:
        if cursor.id in visited:
            return False
        visited.add(cursor.id)
        if cursor.supersedes_event_id is None or cursor.supersedes_revision != cursor.revision - 1:
            return False
        previous = by_id.get(cursor.supersedes_event_id)
        if previous is None or previous.revision != cursor.revision - 1:
            return False
        cursor = previous
    return cursor.supersedes_event_id is None and cursor.supersedes_revision is None


def _event_group_key(event: Any) -> tuple[str, object]:
    return (
        ("logical", event.logical_event_key)
        if event.logical_event_key is not None
        else ("id", event.id)
    )
def _event_current_condition(event: Any) -> Any:
    higher = aliased(NutritionConsumptionEvent)
    same_key = and_(
        event.logical_event_key.is_not(None),
        higher.logical_event_key == event.logical_event_key,
    )
    same_scope = and_(
        higher.user_id == event.user_id,
        higher.provider_key == event.provider_key,
        higher.source_instance_id == event.source_instance_id,
    )
    higher_revision = exists(
        select(1).where(
            same_scope,
            same_key,
            higher.revision > event.revision,
        )
    )
    duplicate_revision = exists(
        select(1).where(
            same_scope,
            same_key,
            higher.revision == event.revision,
            higher.id != event.id,
        )
    )
    return or_(event.logical_event_key.is_(None), and_(not_(higher_revision), not_(duplicate_revision)))




def _event_tombstone_condition(
    event: Any,
    source: Any,
    *,
    rules: _ProviderLifecycleRules,
) -> Any:
    if not rules.source_tombstones_supported and not rules.identity_tombstones_supported:
        return True
    conditions: list[Any] = []
    if rules.source_tombstones_supported:
        conditions.append(_source_tombstone_condition(source, rules=rules))
    if rules.identity_tombstones_supported and rules.event_tombstone_namespaces:
        link = aliased(NutritionExternalIdentityLink)
        newer_link = aliased(NutritionExternalIdentityLink)
        current_link = and_(
            link.user_id == event.user_id,
            link.consumption_event_id == event.id,
            link.link_role == "event_identity",
            not_(
                exists(
                    select(1).where(
                        newer_link.user_id == link.user_id,
                        newer_link.external_identity_id == link.external_identity_id,
                        newer_link.link_role == link.link_role,
                        newer_link.link_revision > link.link_revision,
                    )
                )
            ),
        )
        identity_tombstone = not_(
            exists(
                select(1).where(
                    current_link,
                    NutritionSourceTombstone.user_id == event.user_id,
                    NutritionSourceTombstone.provider_key == event.provider_key,
                    NutritionSourceTombstone.source_instance_id == event.source_instance_id,
                    NutritionSourceTombstone.external_identity_id == link.external_identity_id,
                    NutritionSourceTombstone.source_namespace.in_(rules.event_tombstone_namespaces),
                )
            )
        )
        conditions.append(identity_tombstone)
    return and_(*conditions)






def _provider_evidence(
    db: Session,
    *,
    user_id: UUID,
    provider_key: str,
    source_instance_ids: Sequence[UUID],
    rules: _ProviderLifecycleRules,
    start: date | None,
    end: date | None,
) -> ProviderDiscoveryEvidence:
    if not source_instance_ids:
        return ProviderDiscoveryEvidence(provider_key, None)

    source = NutritionSourceObservation
    source_scope: list[Any] = [
        source.user_id == user_id,
        source.provider_key == provider_key,
        source.source_instance_id.in_(tuple(source_instance_ids)),
        source.observation_kind.in_(
            tuple(rules.source_only_kinds | {ObservationKind.CONSUMPTION_EVENT.value})
        ),
        *_source_state_conditions(source),
        _source_current_condition(source),
        _source_tombstone_condition(source, rules=rules),
    ]
    if start is not None and end is not None:
        source_scope += (
            source.local_date.is_not(None),
            source.local_date >= start,
            source.local_date <= end,
        )

    source_only_scope = list(source_scope)
    source_only_scope.append(source.observation_kind.in_(tuple(rules.source_only_kinds)))
    if start is not None and end is not None:
        source_only_scope.append(_valid_field_condition(source, provider_key=provider_key))
    source_only_observed_at = db.scalar(
        select(func.max(source.observed_at)).where(*source_only_scope)
    )

    event = NutritionConsumptionEvent
    run = NutritionIngestionRun
    event_scope: list[Any] = [
        source.user_id == user_id,
        source.provider_key == provider_key,
        source.source_instance_id.in_(tuple(source_instance_ids)),
        source.observation_kind == ObservationKind.CONSUMPTION_EVENT.value,
        source.source_namespace.in_(rules.event_namespaces),
        *_source_state_conditions(source),
        event.user_id == user_id,
        event.provider_key == provider_key,
        event.source_instance_id == source.source_instance_id,
        event.source_observation_id == source.id,
        event.event_kind.in_(rules.event_kinds),
        event.presence_state.in_((PresenceState.SUPPLIED.value, PresenceState.EXPLICIT_ZERO.value)),
        event.coverage_state.in_(tuple(rules.event_coverage)),
        event.resolution_state == ResolutionState.RESOLVED.value,
        event.lineage_state.in_((LineageState.CONFIRMED.value, LineageState.UNCERTAIN.value)),
        event.local_date == source.local_date,
        _event_current_condition(event),
        _event_tombstone_condition(event, source, rules=rules),
        _valid_field_condition(source, provider_key=provider_key),
    ]
    if rules.require_complete_run:
        event_scope.extend(
            (
                run.user_id == user_id,
                run.id == source.ingestion_run_id,
                run.status == "completed",
                run.coverage_state == CoverageState.COMPLETE.value,
            )
        )
    if start is not None and end is not None:
        event_scope.extend(
            (
                source.local_date.is_not(None),
                source.local_date >= start,
                source.local_date <= end,
            )
        )
    event_statement = select(event, source.observed_at).select_from(source).join(
        event,
        and_(
            event.user_id == user_id,
            event.provider_key == provider_key,
            event.source_instance_id == source.source_instance_id,
            event.source_observation_id == source.id,
        ),
    )
    if rules.require_complete_run:
        event_statement = event_statement.join(
            run,
            and_(run.id == source.ingestion_run_id, run.user_id == user_id),
        )
    event_rows = db.execute(event_statement.where(*event_scope)).all()
    candidate_events = tuple(candidate for candidate, _ in event_rows)
    logical_event_keys = tuple(
        {
            candidate.logical_event_key
            for candidate in candidate_events
            if candidate.logical_event_key is not None
        }
    )
    candidate_event_ids = tuple(
        candidate.id for candidate in candidate_events if candidate.logical_event_key is None
    )
    event_group_conditions = []
    if logical_event_keys:
        event_group_conditions.append(event.logical_event_key.in_(logical_event_keys))
    if candidate_event_ids:
        event_group_conditions.append(event.id.in_(candidate_event_ids))
    all_events = (
        tuple(
            db.scalars(
                select(event).where(
                    event.user_id == user_id,
                    event.provider_key == provider_key,
                    event.source_instance_id.in_(tuple(source_instance_ids)),
                    or_(*event_group_conditions),
                )
            ).all()
        )
        if event_group_conditions
        else ()
    )
    event_groups: dict[tuple[str, object], list[Any]] = {}
    for candidate in all_events:
        event_groups.setdefault(_event_group_key(candidate), []).append(candidate)
    event_observed_at = max(
        (
            observed_at
            for candidate, observed_at in event_rows
            if _event_revision_chain_is_valid(
                event_groups[_event_group_key(candidate)],
                candidate,
            )
        ),
        default=None,
    )

    timestamps = tuple(
        timestamp
        for timestamp in (source_only_observed_at, event_observed_at)
        if timestamp is not None
    )
    return ProviderDiscoveryEvidence(
        provider_key,
        max((_utc_datetime(timestamp) for timestamp in timestamps), default=None),
    )


def _discover_provider(
    db: Session,
    *,
    user_id: UUID,
    provider_key: str,
    start: date | None,
    end: date | None,
) -> ProviderDiscoveryEvidence:
    rules = _RULES[provider_key]
    return _provider_evidence(
        db,
        user_id=user_id,
        provider_key=provider_key,
        source_instance_ids=_owned_source_instance_ids(
            db,
            user_id=user_id,
            provider_key=provider_key,
        ),
        rules=rules,
        start=start,
        end=end,
    )


def discover_yazio_provider_evidence(
    db: Session,
    *,
    user_id: UUID,
    start: date | None = None,
    end: date | None = None,
) -> ProviderDiscoveryEvidence:
    return _discover_provider(db, user_id=user_id, provider_key="yazio", start=start, end=end)


def discover_google_health_provider_evidence(
    db: Session,
    *,
    user_id: UUID,
    start: date | None = None,
    end: date | None = None,
) -> ProviderDiscoveryEvidence:
    return _discover_provider(
        db,
        user_id=user_id,
        provider_key="google_health",
        start=start,
        end=end,
    )


def discover_apple_health_provider_evidence(
    db: Session,
    *,
    user_id: UUID,
    start: date | None = None,
    end: date | None = None,
) -> ProviderDiscoveryEvidence:
    return _discover_provider(
        db,
        user_id=user_id,
        provider_key="apple_health",
        start=start,
        end=end,
    )


__all__ = [
    "NutritionProviderDiscovery",
    "NutritionProviderIdentity",
    "NutritionProviderMetadata",
    "NutritionProviderMetadataSet",
    "ProviderDiscoveryEvidence",
    "ProviderDiscoveryResolver",
    "discover_apple_health_provider_evidence",
    "discover_google_health_provider_evidence",
    "discover_nutrition_provider_metadata",
    "discover_nutrition_providers",
    "discover_yazio_provider_evidence",
]
