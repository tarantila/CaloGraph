from __future__ import annotations


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

    def __init__(
        self,
        message: str,
        *,
        upstream_status_code: int | None = None,
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


class GoogleHealthProviderUnavailableError(GoogleHealthClientError):
    """Google Health is temporarily unavailable."""

    code = "provider_error"


class GoogleHealthInvalidResponseError(GoogleHealthClientError):
    """Google Health returned a response the client cannot safely consume."""

    code = "invalid_response"
