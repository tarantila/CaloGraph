from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from math import isfinite
from typing import TYPE_CHECKING, Any

from app.models import WithingsConnection
from app.services.credential_crypto import (
    CredentialEncryptionError,
    decrypt_credential,
    encrypt_credential,
)

if TYPE_CHECKING:
    from app.schemas_withings import WithingsCredentialsInput


MAX_CREDENTIAL_LENGTH = 512


class WithingsCredentialError(ValueError):
    """A bounded, non-sensitive Withings credential configuration error."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class WithingsCredentialUnavailableError(WithingsCredentialError):
    """Raised when the passed connection has no usable credential pair."""

    def __init__(self) -> None:
        super().__init__("credential_unavailable")


def validate_credential_value(value: str | None) -> str:
    if value is None or value == "":
        raise WithingsCredentialError("credential_required")
    if len(value) > MAX_CREDENTIAL_LENGTH:
        raise WithingsCredentialError("credential_too_long")
    if not value.strip() or any(ord(char) < 0x20 for char in value):
        raise WithingsCredentialError("credential_invalid")
    return value


def credential_pair_from_input(payload: WithingsCredentialsInput) -> tuple[str, str]:
    """Return the complete validated credential pair without persisting plaintext."""
    return (
        validate_credential_value(payload.client_id),
        validate_credential_value(payload.client_secret),
    )


def resolve_withings_credentials(
    connection: WithingsConnection | None,
) -> tuple[str, str]:
    """Decrypt only the user-scoped credentials on the supplied connection."""
    if connection is None:
        raise WithingsCredentialUnavailableError()
    try:
        client_id = validate_credential_value(connection.client_id)
        if connection.encrypted_client_secret is None:
            raise WithingsCredentialUnavailableError()
        client_secret = validate_credential_value(
            decrypt_credential(connection.encrypted_client_secret)
        )
    except (CredentialEncryptionError, WithingsCredentialError):
        raise WithingsCredentialUnavailableError() from None
    return client_id, client_secret

MAX_TOKEN_LIFETIME_SECONDS = 10 * 365 * 24 * 60 * 60


def token_value(payload: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = payload.get(key)
        if value is not None:
            return value
    return None


def normalize_scopes(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        values = value.replace(",", " ").split()
    elif isinstance(value, (list, tuple, set, frozenset)):
        values = [item for item in value if isinstance(item, str)]
    else:
        values = []
    return tuple(dict.fromkeys(item.strip() for item in values if item.strip()))


def expiry_from_payload(payload: Mapping[str, Any], now: datetime) -> datetime | None:
    value = token_value(payload, "expires_in", "access_token_expires_in")
    if value is None:
        explicit = token_value(payload, "expires_at", "access_token_expires_at")
        if isinstance(explicit, datetime):
            return explicit if explicit.tzinfo else explicit.replace(tzinfo=UTC)
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("invalid token expiry")
    seconds = float(value)
    if not isfinite(seconds) or seconds < 0 or seconds > MAX_TOKEN_LIFETIME_SECONDS:
        raise ValueError("invalid token expiry")
    return now + timedelta(seconds=seconds)


def encrypt_token(value: str) -> bytes:
    return encrypt_credential(value)


def decrypt_token(value: bytes) -> str:
    return decrypt_credential(value)


__all__ = [
    "MAX_CREDENTIAL_LENGTH",
    "CredentialEncryptionError",
    "WithingsCredentialError",
    "WithingsCredentialUnavailableError",
    "credential_pair_from_input",
    "decrypt_token",
    "encrypt_token",
    "expiry_from_payload",
    "normalize_scopes",
    "resolve_withings_credentials",
    "token_value",
    "validate_credential_value",
]
