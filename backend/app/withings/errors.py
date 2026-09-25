from dataclasses import dataclass
from typing import Final, Literal

WITHINGS_ERROR_DISABLED: Final = "disabled"
WITHINGS_ERROR_NOT_CONFIGURED: Final = "not_configured"
WITHINGS_ERROR_NOT_CONNECTED: Final = "not_connected"
WITHINGS_ERROR_REAUTH_REQUIRED: Final = "reauth_required"
WITHINGS_ERROR_SCOPE_MISSING: Final = "scope_missing"
WITHINGS_ERROR_RATE_LIMITED: Final = "rate_limited"
WITHINGS_ERROR_TRANSIENT: Final = "transient_error"
WITHINGS_ERROR_PROVIDER: Final = "provider_error"
WITHINGS_ERROR_INVALID_RESPONSE: Final = "invalid_response"
WITHINGS_ERROR_INVALID_REQUEST: Final = "invalid_request"
WITHINGS_ERROR_CODES: Final = frozenset(
    {
        WITHINGS_ERROR_DISABLED,
        WITHINGS_ERROR_NOT_CONFIGURED,
        WITHINGS_ERROR_NOT_CONNECTED,
        WITHINGS_ERROR_REAUTH_REQUIRED,
        WITHINGS_ERROR_SCOPE_MISSING,
        WITHINGS_ERROR_RATE_LIMITED,
        WITHINGS_ERROR_TRANSIENT,
        WITHINGS_ERROR_PROVIDER,
        WITHINGS_ERROR_INVALID_RESPONSE,
        WITHINGS_ERROR_INVALID_REQUEST,
    }
)


class WithingsError(RuntimeError):
    """Base class for safe Withings integration failures."""


class WithingsDisabledError(WithingsError):
    code = WITHINGS_ERROR_DISABLED


class WithingsNotConfiguredError(WithingsError):
    code = WITHINGS_ERROR_NOT_CONFIGURED


class WithingsOAuthError(WithingsError):
    def __init__(self, code: str, *, status_code: int = 400) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(code)


class WithingsTokenExchangeError(WithingsOAuthError):
    pass


class WithingsClientError(WithingsError):
    """Base class for safe read-only Withings client failures."""

    code = WITHINGS_ERROR_PROVIDER
    retryable = False

    def __init__(
        self,
        message: str = "Withings client request failed",
        *,
        upstream_status_code: int | None = None,
    ) -> None:
        if isinstance(upstream_status_code, bool) or (
            upstream_status_code is not None
            and (
                not isinstance(upstream_status_code, int) or not 100 <= upstream_status_code <= 599
            )
        ):
            upstream_status_code = None
        self.upstream_status_code = upstream_status_code
        super().__init__(message)


class WithingsAuthenticationError(WithingsClientError):
    code = WITHINGS_ERROR_REAUTH_REQUIRED


class WithingsScopeError(WithingsClientError):
    code = WITHINGS_ERROR_SCOPE_MISSING


class WithingsRateLimitedError(WithingsClientError):
    code = WITHINGS_ERROR_RATE_LIMITED
    retryable = True

    def __init__(self, retry_after: int = 0, *, upstream_status_code: int | None = 429) -> None:
        self.retry_after = max(0, min(retry_after, 300))
        super().__init__(
            "Withings rate limit exceeded",
            upstream_status_code=upstream_status_code,
        )


class WithingsTransientError(WithingsClientError):
    code = WITHINGS_ERROR_TRANSIENT
    retryable = True


class WithingsProviderUnavailableError(WithingsClientError):
    code = WITHINGS_ERROR_PROVIDER
    retryable = True


@dataclass(frozen=True, slots=True)
class WithingsParserDiagnostic:
    """Bounded structural details for an invalid provider response."""

    parser_stage: str
    field_path: str
    validation_rule: str
    structural_reason: str
    presence: Literal["present", "missing"]
    observed_json_type: str | None
    expected_json_type: str | None
    group_index: int | None = None
    measurement_index: int | None = None


class WithingsProviderError(WithingsClientError):
    """A nonzero provider status without its provider-supplied message."""

    code = WITHINGS_ERROR_PROVIDER

    def __init__(self, provider_status_code: int) -> None:
        if isinstance(provider_status_code, bool) or not isinstance(provider_status_code, int):
            raise ValueError("provider status must be an integer")
        self.provider_status_code = provider_status_code
        super().__init__("Withings provider returned an error")


class WithingsInvalidResponseError(WithingsClientError):
    code = WITHINGS_ERROR_INVALID_RESPONSE

    def __init__(
        self,
        message: str = "Withings invalid response",
        *,
        upstream_status_code: int | None = None,
        diagnostic: WithingsParserDiagnostic | None = None,
    ) -> None:
        self.diagnostic = diagnostic
        super().__init__(message, upstream_status_code=upstream_status_code)


__all__ = [
    "WITHINGS_ERROR_CODES",
    "WITHINGS_ERROR_DISABLED",
    "WITHINGS_ERROR_INVALID_REQUEST",
    "WITHINGS_ERROR_INVALID_RESPONSE",
    "WITHINGS_ERROR_NOT_CONFIGURED",
    "WITHINGS_ERROR_NOT_CONNECTED",
    "WITHINGS_ERROR_PROVIDER",
    "WITHINGS_ERROR_RATE_LIMITED",
    "WITHINGS_ERROR_REAUTH_REQUIRED",
    "WITHINGS_ERROR_SCOPE_MISSING",
    "WITHINGS_ERROR_TRANSIENT",
    "WithingsAuthenticationError",
    "WithingsClientError",
    "WithingsDisabledError",
    "WithingsError",
    "WithingsInvalidResponseError",
    "WithingsNotConfiguredError",
    "WithingsOAuthError",
    "WithingsParserDiagnostic",
    "WithingsProviderError",
    "WithingsProviderUnavailableError",
    "WithingsRateLimitedError",
    "WithingsScopeError",
    "WithingsTokenExchangeError",
    "WithingsTransientError",
]
