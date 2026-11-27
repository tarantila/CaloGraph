"""Internal typed contracts for the experimental subscription device flow."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class SubscriptionGrant(_Contract):
    access_token: SecretStr = Field(repr=False)
    refresh_token: SecretStr | None = Field(default=None, repr=False)
    id_token: SecretStr = Field(repr=False)
    token_type: Literal["Bearer"]
    expires_in: int = Field(gt=0, le=31_536_000)
    scope: str = Field(max_length=2048)
    client_id: str = Field(min_length=1, max_length=512)
    subject: str = Field(min_length=1, max_length=512, repr=False)
    workspace_id: str = Field(min_length=1, max_length=512, repr=False)
    received_at: datetime
    expires_at: datetime
    transport: Literal["codex_compat"] = "codex_compat"

    @property
    def granted_scopes(self) -> frozenset[str]:
        return frozenset(self.scope.split())

    @property
    def inference_enabled(self) -> bool:
        return self.client_id == "app_EMoamEEZ73f0CkXaXp7hrann" and bool(self.workspace_id)


class RefreshedSubscriptionGrant(_Contract):
    access_token: SecretStr = Field(repr=False)
    refresh_token: SecretStr = Field(repr=False)
    id_token: SecretStr = Field(repr=False)
    token_type: Literal["Bearer"]
    expires_in: int = Field(gt=0, le=31_536_000)
    scope: str = Field(max_length=2048)
    client_id: str = Field(min_length=1, max_length=512)
    subject: str = Field(min_length=1, max_length=512, repr=False)
    workspace_id: str = Field(min_length=1, max_length=512, repr=False)
    received_at: datetime
    expires_at: datetime
    transport: Literal["codex_compat"] = "codex_compat"

    @property
    def granted_scopes(self) -> frozenset[str]:
        return frozenset(self.scope.split())

    @property
    def inference_enabled(self) -> bool:
        return self.client_id == "app_EMoamEEZ73f0CkXaXp7hrann" and bool(self.workspace_id)


DeviceGrant = SubscriptionGrant | RefreshedSubscriptionGrant
