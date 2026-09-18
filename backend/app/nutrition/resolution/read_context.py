"""Request-scoped reusable evidence metadata for canonical nutrition reads."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import UUID


@dataclass(slots=True)
class NutritionEvidenceIndex:
    """Read-only request context bound to one provider/source/range."""

    user_id: UUID
    provider_key: str
    source_instance_id: UUID
    start: date
    end: date
    _latest_evidence_observed_at: datetime | None = None
    _evidence_ready: bool = False
    _complete: bool = False

    def owns(
        self,
        *,
        user_id: UUID,
        provider_key: str,
        source_instance_id: UUID,
        start: date,
        end: date,
    ) -> bool:
        return (
            self.user_id == user_id
            and self.provider_key == provider_key
            and self.source_instance_id == source_instance_id
            and self.start == start
            and self.end == end
        )

    def contains(
        self,
        *,
        user_id: UUID,
        provider_key: str,
        source_instance_id: UUID,
        start: date,
        end: date,
    ) -> bool:
        return (
            self.user_id == user_id
            and self.provider_key == provider_key
            and self.source_instance_id == source_instance_id
            and self.start <= start
            and end <= self.end
        )

    def matches(
        self,
        *,
        user_id: UUID,
        provider_key: str,
        source_instance_id: UUID,
        start: date,
        end: date,
    ) -> bool:
        return (
            self.owns(
                user_id=user_id,
                provider_key=provider_key,
                source_instance_id=source_instance_id,
                start=start,
                end=end,
            )
            and self._evidence_ready
            and self._complete
        )

    def record_latest_evidence_observed_at(self, observed_at: datetime | None) -> None:
        if observed_at is None:
            return
        normalized = (
            observed_at.replace(tzinfo=UTC)
            if observed_at.tzinfo is None or observed_at.utcoffset() is None
            else observed_at.astimezone(UTC)
        )
        if (
            self._latest_evidence_observed_at is None
            or normalized > self._latest_evidence_observed_at
        ):
            self._latest_evidence_observed_at = normalized

    def mark_evidence_ready(self) -> None:
        self._evidence_ready = True

    def mark_complete(self) -> None:
        self._complete = True

    @property
    def latest_evidence_observed_at(self) -> datetime | None:
        return self._latest_evidence_observed_at
