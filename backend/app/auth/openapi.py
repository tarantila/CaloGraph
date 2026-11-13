"""Shared browser-session declaration; runtime validation stays in dependencies."""

from fastapi.security import APIKeyCookie

from app.auth.security import session_cookie_name

session_scheme = APIKeyCookie(
    name=session_cookie_name(),
    scheme_name="CaloGraphSession",
    description="Authenticated CaloGraph browser session; not a delegated external tool credential.",
    auto_error=False,
)
