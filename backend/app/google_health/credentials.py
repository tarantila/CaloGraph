from __future__ import annotations

from typing import TYPE_CHECKING

from app.models import GoogleHealthConnection
from app.services.credential_crypto import CredentialEncryptionError, decrypt_credential

if TYPE_CHECKING:
    from app.schemas_google_health import GoogleHealthCredentialsInput

MAX_CREDENTIAL_LENGTH = 512


class GoogleHealthCredentialError(ValueError):
    """A bounded, non-sensitive Google Health credential configuration error."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class GoogleHealthCredentialUnavailableError(GoogleHealthCredentialError):
    """Raised when the per-user credential pair cannot be resolved safely."""

    def __init__(self) -> None:
        super().__init__("credential_unavailable")


def validate_credential_value(value: str | None, *, allow_empty: bool = False) -> str | None:
    if value is None:
        if allow_empty:
            return None
        raise GoogleHealthCredentialError("credential_required")
    if value == "":
        if allow_empty:
            return value
        raise GoogleHealthCredentialError("credential_required")
    if len(value) > MAX_CREDENTIAL_LENGTH:
        raise GoogleHealthCredentialError("credential_too_long")
    if not value.strip() or any(ord(char) < 0x20 for char in value):
        raise GoogleHealthCredentialError("credential_invalid")
    return value


def credential_pair_from_input(payload: GoogleHealthCredentialsInput) -> tuple[str, str] | None:
    """Return a replacement pair, or ``None`` when blank values mean preserve."""
    client_id = validate_credential_value(payload.client_id, allow_empty=True)
    client_secret = validate_credential_value(payload.client_secret, allow_empty=True)
    if client_id == "" and client_secret in (None, ""):
        return None
    if client_id == "" or client_secret in (None, ""):
        raise GoogleHealthCredentialError("credential_pair_required")
    assert client_id is not None and client_secret is not None
    return client_id, client_secret


def resolve_google_health_credentials(
    connection: GoogleHealthConnection | None,
) -> tuple[str, str]:
    """Resolve credentials for backend OAuth/client code without exposing them in status APIs."""
    if connection is None or not connection.client_id or not connection.encrypted_client_secret:
        raise GoogleHealthCredentialUnavailableError()
    try:
        client_secret = decrypt_credential(connection.encrypted_client_secret)
    except CredentialEncryptionError:
        raise GoogleHealthCredentialUnavailableError() from None
    if not client_secret:
        raise GoogleHealthCredentialUnavailableError()
    return connection.client_id, client_secret


# Short aliases make the boundary easy to discover for OAuth and provider callers.
resolve_credentials = resolve_google_health_credentials

__all__ = [
    "MAX_CREDENTIAL_LENGTH",
    "GoogleHealthCredentialError",
    "GoogleHealthCredentialUnavailableError",
    "credential_pair_from_input",
    "resolve_credentials",
    "resolve_google_health_credentials",
    "validate_credential_value",
]
