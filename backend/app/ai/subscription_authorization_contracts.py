"""Public, token-free REST contracts for subscription device authorization."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

SUBSCRIPTION_DEVICE_PRIVACY_ACKNOWLEDGEMENT = "subscription-device-oauth-2026-11-28"

AuthorizationState = Literal[
    "starting", "pending", "polling", "exchanging", "completed",
    "denied", "failed", "cancelled", "expired", "interrupted",
]
SubscriptionState = Literal[
    "not_connected", "ready", "refresh_claimed", "refresh_unconfirmed",
    "reauth_required", "revocation_pending", "revocation_unconfirmed", "disconnected",
]


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class SubscriptionAuthorizationStart(_Contract):
    privacy_acknowledgement: Literal["subscription-device-oauth-2026-11-28"]
    expected_credential_version: int = Field(strict=True, ge=0)


class SubscriptionAuthorizationView(_Contract):
    id: UUID
    state: AuthorizationState
    safe_error_code: str | None = Field(default=None, max_length=64)
    oauth_status: int | None = Field(default=None, ge=400, le=599)
    created_at: datetime
    expires_at: datetime
    next_poll_at: datetime | None
    poll_interval_seconds: int = Field(ge=1, le=60)
    user_code: str | None = Field(default=None, max_length=64)
    verification_uri: Literal["https://auth.openai.com/codex/device"] | None = None


class SubscriptionStatus(_Contract):
    state: SubscriptionState
    credential_version: int = Field(ge=0)
    expires_at: datetime | None
    has_credential: bool
