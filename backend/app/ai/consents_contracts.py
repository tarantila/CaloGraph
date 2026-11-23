"""Versioned consent resources for future Nutrition Expert data transfers."""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.ai.connections_contracts import ConnectionContract
from app.ai.contracts import ProviderKind

CONSENT_POLICY_VERSION = "expert-data-2026-11-14"
MAX_CONSENTS_PER_ACCOUNT = 200
ConsentPurpose = Literal["nutrition_chat", "nutrition_check_in"]
ConsentDataDomain = Literal[
    "nutrition", "targets", "weight", "activity", "profile", "health_context",
]
ConsentState = Literal["active", "revoked", "configuration_changed", "connection_disabled"]


class ConsentHistory(ConnectionContract):
    mode: Literal["all_available", "date_range"]
    start: date | None
    end: date | None

    @model_validator(mode="after")
    def valid_range(self) -> ConsentHistory:
        if self.mode == "all_available" and (self.start is not None or self.end is not None):
            raise ValueError("all-available history requires null bounds")
        if self.mode == "date_range" and (
            self.start is None or self.end is None or self.start > self.end
        ):
            raise ValueError("date-range history requires ordered inclusive bounds")
        return self


class AIConsentPut(ConnectionContract):
    connection_id: UUID
    configuration_version: int = Field(ge=1)
    purpose: ConsentPurpose
    data_domains: tuple[ConsentDataDomain, ...] = Field(min_length=1, max_length=6)
    include_meal_names: bool
    history: ConsentHistory
    policy_version: Literal["expert-data-2026-11-14"]

    @field_validator("data_domains")
    @classmethod
    def unique_sorted_domains(
        cls, value: tuple[ConsentDataDomain, ...],
    ) -> tuple[ConsentDataDomain, ...]:
        if len(set(value)) != len(value):
            raise ValueError("data domains must be unique")
        return tuple(sorted(value))

    @model_validator(mode="after")
    def meal_names_need_nutrition(self) -> AIConsentPut:
        if self.include_meal_names and "nutrition" not in self.data_domains:
            raise ValueError("meal names require the nutrition domain")
        return self


class AIConsentView(ConnectionContract):
    id: UUID
    connection_id: UUID
    configuration_version: int = Field(ge=1)
    provider_kind: ProviderKind
    model_id: str = Field(min_length=1, max_length=200)
    base_url: str | None = Field(default=None, max_length=2048)
    purpose: ConsentPurpose
    data_domains: tuple[ConsentDataDomain, ...] = Field(min_length=1, max_length=6)
    include_meal_names: bool
    history: ConsentHistory
    policy_version: str
    state: ConsentState
    created_at: datetime
    revoked_at: datetime | None


class AIConsentListQuery(ConnectionContract):
    limit: int = Field(default=20, ge=1, le=50)
    after: UUID | None = Field(default=None, description="Exclusive consent-ID cursor scoped to the authenticated account.")
    connection_id: UUID | None = None


class AIConsentPage(ConnectionContract):
    items: tuple[AIConsentView, ...] = Field(max_length=50)
    next_after: UUID | None


class ConsentDataUse(ConnectionContract):
    """Internal, server-constructed request for a future bounded Expert read."""

    purpose: ConsentPurpose
    data_domains: tuple[ConsentDataDomain, ...] = Field(min_length=1, max_length=6)
    include_meal_names: bool
    start: date
    end: date

    @field_validator("data_domains")
    @classmethod
    def unique_use_domains(
        cls, value: tuple[ConsentDataDomain, ...],
    ) -> tuple[ConsentDataDomain, ...]:
        if len(set(value)) != len(value):
            raise ValueError("data domains must be unique")
        return tuple(sorted(value))

    @model_validator(mode="after")
    def valid_use_range(self) -> ConsentDataUse:
        if self.start > self.end:
            raise ValueError("data-use dates must be ordered")
        if self.include_meal_names and "nutrition" not in self.data_domains:
            raise ValueError("meal names require the nutrition domain")
        return self
