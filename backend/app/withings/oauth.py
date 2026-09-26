from __future__ import annotations

import hashlib
import hmac
import ipaddress
import secrets
from urllib.parse import urlencode, urlsplit

from app.config import settings
from app.withings.constants import WITHINGS_AUTHORIZE_URL, WITHINGS_SCOPES


def create_oauth_state() -> str:
    return secrets.token_urlsafe(32)


def hash_oauth_state(raw_state: str) -> str:
    if not isinstance(raw_state, str) or not raw_state:
        raise ValueError("OAuth state must be a non-empty string")
    return hmac.new(
        settings.session_secret.encode("utf-8"),
        raw_state.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def validate_redirect_uri(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("Withings redirect URI is not configured")
    parsed = urlsplit(value)
    hostname = parsed.hostname.lower() if parsed.hostname else None
    loopback = hostname == "localhost"
    if hostname is not None and not loopback:
        try:
            loopback = ipaddress.ip_address(hostname).is_loopback
        except ValueError:
            loopback = False
    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.netloc
        or not hostname
        or not parsed.path
        or (parsed.scheme == "http" and not loopback)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Withings redirect URI must be HTTPS or loopback HTTP")
    return value


def build_authorization_url(*, client_id: str, redirect_uri: str, state: str) -> str:
    validate_redirect_uri(redirect_uri)
    query = urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": ",".join(WITHINGS_SCOPES),
            "state": state,
        }
    )
    return f"{WITHINGS_AUTHORIZE_URL}?{query}"
