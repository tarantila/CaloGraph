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
