"""Public configuration contracts; stored credentials never enter responses."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    SecretStr,
    UrlConstraints,
    field_validator,
    model_validator,
)

from app.ai.contracts import ProviderKind

ConfigurableProvider = Literal["openai_api", "anthropic", "gemini", "local", "gateway"]
EndpointUrl = Annotated[HttpUrl, UrlConstraints(max_length=2048)]
MAX_CONNECTIONS = 20


class ConnectionContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class ProviderConnectionPut(ConnectionContract):
    label: str = Field(min_length=1, max_length=100)
    provider_kind: ConfigurableProvider
    model_id: str = Field(min_length=1, max_length=200)
    base_url: EndpointUrl | None = Field(
        default=None, description="Required for local/gateway; forbidden for native providers. Saving grants no outbound access.",
    )
    enabled: bool = False
    timeout_seconds: int = Field(default=60, ge=5, le=300)
    api_key: SecretStr | None = Field(
        default=None, max_length=8192, repr=False, exclude=True,
        description="Write only: omit to preserve, null to remove, non-empty string to replace.",
        json_schema_extra={"writeOnly": True},
    )

    @field_validator("label", "model_id")
    @classmethod
    def clean_text(cls, value: str) -> str:
        if not value.strip() or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("configuration text must be non-empty and contain no controls")
        return value.strip()

    @field_validator("api_key")
    @classmethod
    def clean_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            secret = value.get_secret_value()
            if not secret or secret != secret.strip() or any(ord(c) < 32 or ord(c) == 127 for c in secret):
                raise ValueError("credential is invalid")
        return value

    @model_validator(mode="after")
    def endpoint_rules(self) -> ProviderConnectionPut:
        custom = self.provider_kind in {"local", "gateway"}
        if custom != (self.base_url is not None):
            raise ValueError("only local and gateway providers require a custom endpoint")
        if self.base_url is not None and (
            self.base_url.username is not None or self.base_url.password is not None
            or self.base_url.query is not None or self.base_url.fragment is not None
        ):
            raise ValueError("endpoint must not contain credentials, query, or fragment")
        if self.base_url is not None and len(str(self.base_url)) > 2048:
            raise ValueError("normalized endpoint must not exceed 2048 characters")
        return self


class ProviderConnectionView(ConnectionContract):
    id: UUID
    label: str
    provider_kind: ProviderKind
    model_id: str
    base_url: EndpointUrl | None
    enabled: bool
    timeout_seconds: int
    configuration_version: int = Field(ge=1)
    has_credential: bool = Field(description="Stored credential presence, not connectivity or decryptability.")
    created_at: datetime
    updated_at: datetime


class ProviderConnectionListQuery(ConnectionContract):
    limit: int = Field(default=20, ge=1, le=MAX_CONNECTIONS)
    after: UUID | None = Field(default=None, description="Exclusive connection-ID cursor for this account.")


class ProviderConnectionPage(ConnectionContract):
    items: tuple[ProviderConnectionView, ...] = Field(max_length=MAX_CONNECTIONS)
    next_after: UUID | None = Field(description="Null on the last page; pages reflect live configuration.")
