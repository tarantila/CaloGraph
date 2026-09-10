import base64
import hashlib
import hmac
import secrets
from collections.abc import Iterable, Mapping
from urllib.parse import urlencode, urlsplit

from app.config import settings
from app.google_health.constants import (
    GOOGLE_HEALTH_AUTH_URI,
    GOOGLE_HEALTH_CALLBACK_PATH,
    GOOGLE_HEALTH_SCOPE,
)

_CONSENT_INTENTS = frozenset({"initial", "reauthorize", "missing_refresh", "scope_change"})


def google_health_redirect_uri(public_url: str) -> str:
    """Build the fixed callback URI from a configured public origin."""
    if not isinstance(public_url, str):
        raise ValueError("CALOGRAPH_PUBLIC_URL must be an absolute HTTP(S) origin")
    normalized = public_url.strip()
    try:
        parsed = urlsplit(normalized)
        hostname = parsed.hostname
        parsed.port
    except ValueError:
        raise ValueError("CALOGRAPH_PUBLIC_URL must be an absolute HTTP(S) origin") from None
    if (
        parsed.scheme not in {"http", "https"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("CALOGRAPH_PUBLIC_URL must be an absolute HTTP(S) origin")
    origin = f"{parsed.scheme}://{parsed.netloc}"
    return f"{origin}{GOOGLE_HEALTH_CALLBACK_PATH}"


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


def create_pkce_verifier() -> str:
    return secrets.token_urlsafe(64)


def pkce_challenge(verifier: str) -> str:
    if not isinstance(verifier, str) or not verifier:
        raise ValueError("PKCE verifier must be a non-empty string")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def normalize_granted_scopes(scopes: object) -> tuple[str, ...]:
    if isinstance(scopes, str):
        values: Iterable[object] = (scopes,)
    elif isinstance(scopes, Iterable) and not isinstance(scopes, (bytes, bytearray, Mapping)):
        values = scopes
    else:
        return ()
    normalized = {
        part
        for value in values
        if isinstance(value, str)
        for part in value.split()
        if part
    }
    return tuple(sorted(normalized))


def build_authorization_url(
    *,
    client_id: str,
    state: str,
    verifier: str,
    redirect_uri: str,
    reauthorize: bool = False,
    intent: str | None = None,
    initial: bool = False,
    missing_refresh: bool = False,
    scope_change: bool = False,
) -> str:
    """Build the fixed, read-only Google Health authorization request."""
    query: dict[str, str] = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "access_type": "offline",
        "scope": GOOGLE_HEALTH_SCOPE,
        "state": state,
        "code_challenge": pkce_challenge(verifier),
        "code_challenge_method": "S256",
    }
    should_prompt = (
        reauthorize
        or initial
        or missing_refresh
        or scope_change
        or intent in _CONSENT_INTENTS
    )
    if should_prompt:
        query["prompt"] = "consent"
    return f"{GOOGLE_HEALTH_AUTH_URI}?{urlencode(query)}"
