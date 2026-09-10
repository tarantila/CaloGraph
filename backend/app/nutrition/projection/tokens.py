from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import ClassVar
from uuid import UUID

from app.nutrition.resolution.metrics import is_known_metric

_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


def _require_uuid(value: UUID, field_name: str) -> UUID:
    if not isinstance(value, UUID):
        raise TypeError(f"{field_name} must be an internal UUID")
    if value.int == 0:
        raise ValueError(f"{field_name} must be non-zero")
    return value


def _require_revision(value: int, field_name: str) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"{field_name} must be a positive revision")
    return value


def _require_hash(value: str, field_name: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a lowercase SHA-256 hash")
    return value


def _require_optional_scope(provider_key: str | None, metric_key: str | None) -> None:
    if provider_key is not None and (not isinstance(provider_key, str) or not provider_key):
        raise ValueError("provider_key must be non-empty when supplied")
    if metric_key is not None and (not isinstance(metric_key, str) or not is_known_metric(metric_key)):
        raise ValueError("metric_key must be a canonical nutrition metric when supplied")


@dataclass(frozen=True, slots=True)
class SourceObservationToken:
    source_observation_id: UUID
    source_revision: int
    observation_fingerprint: str
    payload_hash: str | None = None
    provider_key: str | None = None
    metric_key: str | None = None

    kind: ClassVar[str] = "source_observation"

    def __post_init__(self) -> None:
        _require_uuid(self.source_observation_id, "source_observation_id")
        _require_revision(self.source_revision, "source_revision")
        _require_hash(self.observation_fingerprint, "observation_fingerprint")
        if self.payload_hash is not None:
            _require_hash(self.payload_hash, "payload_hash")
        _require_optional_scope(self.provider_key, self.metric_key)

    @property
    def token_id(self) -> UUID:
        return self.source_observation_id

    def canonical_payload(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "source_observation_id": str(self.source_observation_id),
            "source_revision": self.source_revision,
            "observation_fingerprint": self.observation_fingerprint,
            "payload_hash": self.payload_hash,
            "provider_key": self.provider_key,
            "metric_key": self.metric_key,
        }


@dataclass(frozen=True, slots=True)
class ConsumptionEventToken:
    event_id: UUID
    revision: int
    provider_key: str | None = None
    metric_key: str | None = None

    kind: ClassVar[str] = "consumption_event"

    def __post_init__(self) -> None:
        _require_uuid(self.event_id, "event_id")
        _require_revision(self.revision, "revision")
        _require_optional_scope(self.provider_key, self.metric_key)

    @property
    def token_id(self) -> UUID:
        return self.event_id

    def canonical_payload(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "event_id": str(self.event_id),
            "revision": self.revision,
            "provider_key": self.provider_key,
            "metric_key": self.metric_key,
        }


@dataclass(frozen=True, slots=True)
class FieldObservationToken:
    field_observation_id: UUID
    provider_key: str | None = None
    metric_key: str | None = None

    kind: ClassVar[str] = "field_observation"

    def __post_init__(self) -> None:
        _require_uuid(self.field_observation_id, "field_observation_id")
        _require_optional_scope(self.provider_key, self.metric_key)

    @property
    def token_id(self) -> UUID:
        return self.field_observation_id

    def canonical_payload(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "field_observation_id": str(self.field_observation_id),
            "provider_key": self.provider_key,
            "metric_key": self.metric_key,
        }


@dataclass(frozen=True, slots=True)
class FoodSnapshotToken:
    snapshot_id: UUID
    content_hash: str
    provider_key: str | None = None
    metric_key: str | None = None

    kind: ClassVar[str] = "food_snapshot"

    def __post_init__(self) -> None:
        _require_uuid(self.snapshot_id, "snapshot_id")
        _require_hash(self.content_hash, "content_hash")
        _require_optional_scope(self.provider_key, self.metric_key)

    @property
    def token_id(self) -> UUID:
        return self.snapshot_id

    def canonical_payload(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "snapshot_id": str(self.snapshot_id),
            "content_hash": self.content_hash,
            "provider_key": self.provider_key,
            "metric_key": self.metric_key,
        }


@dataclass(frozen=True, slots=True)
class IdentityLinkToken:
    link_id: UUID
    link_revision: int
    provider_key: str | None = None
    metric_key: str | None = None

    kind: ClassVar[str] = "identity_link"

    def __post_init__(self) -> None:
        _require_uuid(self.link_id, "link_id")
        _require_revision(self.link_revision, "link_revision")
        _require_optional_scope(self.provider_key, self.metric_key)

    @property
    def token_id(self) -> UUID:
        return self.link_id

    def canonical_payload(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "link_id": str(self.link_id),
            "link_revision": self.link_revision,
            "provider_key": self.provider_key,
            "metric_key": self.metric_key,
        }


@dataclass(frozen=True, slots=True)
class TombstoneToken:
    tombstone_id: UUID
    observed_at: datetime
    provider_key: str | None = None
    metric_key: str | None = None

    kind: ClassVar[str] = "tombstone"

    def __post_init__(self) -> None:
        _require_uuid(self.tombstone_id, "tombstone_id")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        object.__setattr__(self, "observed_at", self.observed_at.astimezone(UTC))
        _require_optional_scope(self.provider_key, self.metric_key)

    @property
    def token_id(self) -> UUID:
        return self.tombstone_id

    def canonical_payload(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "tombstone_id": str(self.tombstone_id),
            "observed_at": self.observed_at.isoformat(),
            "provider_key": self.provider_key,
            "metric_key": self.metric_key,
        }


type TechnicalEvidenceToken = (
    SourceObservationToken
    | ConsumptionEventToken
    | FieldObservationToken
    | FoodSnapshotToken
    | IdentityLinkToken
    | TombstoneToken
)


def token_identity(token: TechnicalEvidenceToken) -> tuple[str, UUID]:
    return token.kind, token.token_id

def token_sort_key(token: TechnicalEvidenceToken) -> tuple[str, str, str]:
    return (
        token.kind,
        str(token.token_id),
        json.dumps(
            token.canonical_payload(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ),
    )
