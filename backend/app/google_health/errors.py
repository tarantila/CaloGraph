from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GoogleHealthParserDiagnostic:
    domain: str
    operation: str
    endpoint_key: str
    parser_stage: str
    field_path: str
    validation_rule: str
    numeric_reason_code: str | None
    observed_json_type: str
    expected_json_type: str
    presence: str
    representation: str
    retryable: bool
    reauth_required: bool
    chunk_index: int | None = None
    page_index: int | None = None
    point_index: int | None = None


class GoogleHealthError(RuntimeError):
    """Base class for safe Google Health integration failures."""


class GoogleHealthDisabledError(GoogleHealthError):
    code = "disabled"


class GoogleHealthOAuthError(GoogleHealthError):
    def __init__(self, code: str, *, status_code: int = 400) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(code)


class GoogleHealthTokenExchangeError(GoogleHealthOAuthError):
    pass


class GoogleHealthClientError(GoogleHealthError):
    """Base class for safe read-only Google Health client failures."""

    retryable = False

    def __init__(
        self,
        message: str = "Google Health client request failed",
        *,
        upstream_status_code: int | None = None,
        parser_stage: str | None = None,
        structural_reason_code: str | None = None,
        diagnostic: GoogleHealthParserDiagnostic | None = None,
    ) -> None:
        if (
            isinstance(upstream_status_code, bool)
            or (
                upstream_status_code is not None
                and (
                    not isinstance(upstream_status_code, int)
                    or not 100 <= upstream_status_code <= 599
                )
            )
        ):
            upstream_status_code = None
        self.upstream_status_code = upstream_status_code
        self.parser_stage = parser_stage
        self.structural_reason_code = structural_reason_code
        self.diagnostic = diagnostic
        self.chunk_index: int | None = None
        self.page_index: int | None = None
        super().__init__(message)

class GoogleHealthAuthenticationError(GoogleHealthClientError):
    """The access credential is missing, invalid, or requires reauthentication."""

    code = "reauth_required"


class GoogleHealthScopeError(GoogleHealthClientError):
    """The credential does not grant the required read-only scope."""

    code = "scope_missing"


class GoogleHealthRateLimitedError(GoogleHealthClientError):
    """Google Health asked the caller to slow down."""

    code = "rate_limited"
    retryable = True

    def __init__(
        self,
        retry_after: int = 0,
        *,
        upstream_status_code: int | None = 429,
    ) -> None:
        self.retry_after = max(0, min(retry_after, 300))
        super().__init__(
            "Google Health rate limit exceeded",
            upstream_status_code=upstream_status_code,
        )


class GoogleHealthTransientError(GoogleHealthClientError):
    """A timeout or network failure may succeed when retried later."""

    code = "transient_error"
    retryable = True


class GoogleHealthProviderUnavailableError(GoogleHealthClientError):
    """Google Health is temporarily unavailable."""

    code = "provider_error"
    retryable = True


class GoogleHealthInvalidResponseError(GoogleHealthClientError):
    """Google Health returned a response the client cannot safely consume."""

    code = "invalid_response"
