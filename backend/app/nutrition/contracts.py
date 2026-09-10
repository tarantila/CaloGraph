from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import UUID

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class SourceObservationIdentity:
    """Technical source identity used for ingestion deduplication."""

    user_id: UUID
    source_instance_id: UUID
    provider_key: str
    source_namespace: str
    source_record_id: str | None
    source_revision: int
    observation_fingerprint: str


@dataclass(frozen=True, slots=True)
class ExternalIdentityKey:
    user_id: UUID
    source_instance_id: UUID
    provider_key: str
    namespace: str
    identity_value: str


def validate_observation_fingerprint(value: str) -> str:
    if not _SHA256_RE.fullmatch(value):
        raise ValueError("observation_fingerprint must be a lowercase SHA-256 value")
    return value


def validate_source_record_id(value: str | None) -> str | None:
    if value is not None and not value:
        raise ValueError("source_record_id must be non-empty when supplied")
    return value


def validate_source_revision(value: int) -> int:
    if value < 1:
        raise ValueError("source_revision must be positive")
    return value


def validate_link_revision(value: int) -> int:
    if value < 1:
        raise ValueError("link_revision must be positive")
    return value
