"""Experimental fixed-client ChatGPT subscription device compatibility protocol.

This transport is intentionally separate from the SIWC authorization protocol.
Only pinned, reviewed OpenAI hosts and paths are contacted, and response bodies
are converted to static protocol failures before reaching callers.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Literal, cast

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from google.auth import jwt as google_jwt
from pydantic import SecretStr, ValidationError

from app.ai.outbound import (
    OAUTH_TERMINAL_REFRESH_ERRORS,
    OutboundError,
    fetch_chatgpt_auth_json,
    fetch_chatgpt_jwks,
    fetch_chatgpt_oauth_form,
    fetch_chatgpt_oauth_json,
)
from app.ai.subscription_device_contracts import RefreshedSubscriptionGrant, SubscriptionGrant

ISSUER = "https://auth.openai.com"
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
DEVICE_START = "/api/accounts/deviceauth/usercode"
DEVICE_POLL = "/api/accounts/deviceauth/token"
TOKEN_PATH = "/oauth/token"
REVOKE_PATH = "/oauth/revoke"
DEVICE_URI = "https://auth.openai.com/codex/device"
REDIRECT_URI = "https://auth.openai.com/deviceauth/callback"
CODEX_SCOPES = "openid profile email offline_access api.connectors.read api.connectors.invoke"
_SCOPE_SET = frozenset(CODEX_SCOPES.split())
MAX_OAUTH_BODY_BYTES = 64 * 1024


class ProtocolError(Exception):
    """Static, credential-free protocol failure with allowlisted OAuth metadata."""

    def __init__(self, code: str, *, oauth_error: str | None = None, status: int | None = None) -> None:
        self.code = code if code in {"invalid_configuration", "invalid_token", "identity_mismatch", "refresh_terminal", "provider_failure"} else "provider_failure"
        safe_errors = {"invalid_request", "unauthorized_client", "access_denied", "invalid_scope", "invalid_client", "server_error", "temporarily_unavailable", "expired_token"} | OAUTH_TERMINAL_REFRESH_ERRORS
        self.oauth_error = oauth_error if oauth_error in safe_errors else None
        self.status = status if isinstance(status, int) and 400 <= status <= 599 else None
        detail = f": {self.oauth_error}" if self.oauth_error else ""
        detail += f" (HTTP {self.status})" if self.status else ""
        super().__init__(self.code + detail)


def _b64url_decode(value: object, maximum: int) -> bytes:
    if not isinstance(value, str) or not value or len(value) > maximum or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("invalid base64url")
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _strict_json(raw: bytes) -> object:
    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON member")
            result[key] = value
        return result

    return json.loads(raw.decode("utf-8"), object_pairs_hook=unique, parse_constant=lambda _v: (_ for _ in ()).throw(ValueError()))


def _numeric_date(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError("invalid date")
    return float(value)


def _now(value: datetime | None = None) -> datetime:
    current = value or datetime.now(UTC)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ProtocolError("invalid_configuration")
    return current.astimezone(UTC)


def _bounded_string(value: object, maximum: int = 16_384) -> str:
    try:
        valid = isinstance(value, str) and bool(value) and len(value.encode("utf-8")) <= maximum
    except UnicodeError:
        valid = False
    if not valid:
        raise ProtocolError("invalid_token")
    return cast(str, value)


def verify_id_token(token: str, *, now: datetime, expected_subject: str | None = None) -> dict[str, object]:
    try:
        if not 1 <= len(token.encode("utf-8")) <= 32_768:
            raise ValueError
        parts = token.split(".")
        if len(parts) != 3:
            raise ValueError
        header = _strict_json(_b64url_decode(parts[0], 2048))
        claims = _strict_json(_b64url_decode(parts[1], 12_000))
        if not isinstance(header, dict) or set(header) - {"alg", "kid", "typ"}:
            raise ValueError
        if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str) or header.get("typ") not in (None, "JWT"):
            raise ValueError
        jwks = fetch_chatgpt_jwks()
        keys = jwks.get("keys") if isinstance(jwks, dict) else None
        if not isinstance(keys, list) or not 1 <= len(keys) <= 32:
            raise ValueError
        matches = [key for key in keys if isinstance(key, dict) and key.get("kid") == header["kid"]]
        if len(matches) != 1:
            raise ValueError
        jwk = matches[0]
        if jwk.get("kty") != "RSA" or jwk.get("alg") != "RS256" or jwk.get("use") not in (None, "sig"):
            raise ValueError
        modulus = int.from_bytes(_b64url_decode(jwk.get("n"), 2048), "big")
        exponent = int.from_bytes(_b64url_decode(jwk.get("e"), 16), "big")
        if not 2048 <= modulus.bit_length() <= 8192 or exponent < 3 or exponent % 2 == 0:
            raise ValueError
        public = rsa.RSAPublicNumbers(exponent, modulus).public_key()
        pem = public.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode("ascii")
        verified = cast(Callable[..., object], google_jwt.decode)(
            token, {header["kid"]: pem}, clock_skew_in_seconds=5,
        )
        if not isinstance(verified, dict):
            raise ValueError
        claims = verified
        if claims.get("iss") != ISSUER:
            raise ValueError
        audience = claims.get("aud")
        audiences = [audience] if isinstance(audience, str) else audience if isinstance(audience, list) and all(isinstance(item, str) for item in audience) else []
        if CLIENT_ID not in audiences or (len(audiences) > 1 and claims.get("azp") != CLIENT_ID):
            raise ValueError
        if "azp" in claims and claims.get("azp") != CLIENT_ID:
            raise ValueError
        timestamp = _now(now).timestamp()
        if (_numeric_date(claims.get("iat")) > timestamp + 5
                or _numeric_date(claims.get("exp")) < timestamp - 5
                or _numeric_date(claims.get("nbf", claims.get("iat"))) > timestamp + 5):
            raise ValueError
        subject = _bounded_string(claims.get("sub"), 512)
        if expected_subject is not None and not hmac.compare_digest(subject, expected_subject):
            raise ProtocolError("identity_mismatch")
        return claims
    except ProtocolError:
        raise
    except OutboundError:
        raise ProtocolError("provider_failure") from None
    except Exception:
        raise ProtocolError("invalid_token") from None


def _json_body(value: object) -> bytes:
    try:
        result = json.dumps(value, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    except (TypeError, ValueError, RecursionError):
        raise ProtocolError("invalid_token") from None
    if not 1 <= len(result) <= MAX_OAUTH_BODY_BYTES:
        raise ProtocolError("invalid_token")
    return result


def begin_device_authorization() -> dict[str, object]:
    """Return the bounded device code metadata for encrypted server storage."""
    try:
        _, data = fetch_chatgpt_auth_json(
            DEVICE_START, _json_body({"client_id": CLIENT_ID}), timeout_seconds=15,
        )
    except OutboundError:
        raise ProtocolError("provider_failure") from None
    try:
        if not isinstance(data, dict) or len(json.dumps(data).encode("utf-8")) > MAX_OAUTH_BODY_BYTES:
            raise ValueError
    except (ValueError, TypeError, RecursionError, UnicodeError):
        raise ProtocolError("invalid_token") from None
    device_id = _bounded_string(data.get("device_auth_id"), 1024)
    user_code = _bounded_string(data.get("user_code"), 64)
    interval_raw = data.get("interval", 5)
    if isinstance(interval_raw, str):
        if not re.fullmatch(r"[0-9]{1,2}", interval_raw):
            raise ProtocolError("invalid_token")
        interval_raw = int(interval_raw)
    if isinstance(interval_raw, bool) or not isinstance(interval_raw, int):
        raise ProtocolError("invalid_token")
    if not 1 <= interval_raw <= 60:
        raise ProtocolError("invalid_token")
    interval = interval_raw
    expiry_raw = data.get("expires_at")
    if expiry_raw is None:
        expires = _now() + timedelta(minutes=15)
    elif isinstance(expiry_raw, (int, float)) and not isinstance(expiry_raw, bool):
        try:
            expires = datetime.fromtimestamp(float(expiry_raw), UTC)
        except (OverflowError, OSError, ValueError):
            raise ProtocolError("invalid_token") from None
    elif isinstance(expiry_raw, str) and len(expiry_raw) <= 64:
        try:
            expires = datetime.fromisoformat(expiry_raw.replace("Z", "+00:00"))
        except ValueError:
            raise ProtocolError("invalid_token") from None
        if expires.tzinfo is None or expires.utcoffset() is None:
            raise ProtocolError("invalid_token")
        expires = expires.astimezone(UTC)
    else:
        raise ProtocolError("invalid_token")
    if expires <= _now() or expires > _now() + timedelta(hours=1):
        raise ProtocolError("invalid_token")
    return {"device_auth_id": device_id, "user_code": user_code, "interval": interval, "expires_at": expires.isoformat()}


def poll_device_authorization(device_auth_id: str, user_code: str) -> tuple[Literal["pending", "authorized", "denied"], dict[str, object] | None]:
    try:
        status, data = fetch_chatgpt_auth_json(
            DEVICE_POLL, _json_body({"device_auth_id": device_auth_id, "user_code": user_code}),
            timeout_seconds=15, accepted_statuses=(403, 404),
        )
    except OutboundError:
        raise ProtocolError("provider_failure") from None
    if status in (403, 404):
        return "pending", None
    try:
        if not isinstance(data, dict) or len(json.dumps(data).encode("utf-8")) > MAX_OAUTH_BODY_BYTES:
            raise ValueError
    except (ValueError, TypeError, RecursionError, UnicodeError):
        raise ProtocolError("invalid_token") from None
    if "error" in data:
        error = data["error"]
        if isinstance(error, str) and error in {"access_denied", "authorization_declined"}:
            return "denied", None
        raise ProtocolError("invalid_token")
    # The documented success shape is deliberately exact. Unknown JSON never
    # gets surfaced and cannot be mistaken for a usable authorization.
    code = _bounded_string(data.get("authorization_code"), 4096)
    verifier = _bounded_string(data.get("code_verifier"), 256)
    challenge = _bounded_string(data.get("code_challenge"), 256)
    return "authorized", {"authorization_code": code, "code_verifier": verifier, "code_challenge": challenge}


def _expires_in(data: dict[str, object], claims: dict[str, object] | None, access: str, now: datetime) -> int:
    """Read token lifetime; access JWT claims are scheduling metadata only."""
    seconds = data.get("expires_in")
    if "expires_in" not in data:
        expiry = claims.get("exp") if claims is not None else None
        if expiry is None:
            # A refresh need not return another ID token. This access token
            # came directly from the fixed TLS token endpoint; only its expiry
            # is used, never its account, workspace, audience or permissions.
            try:
                parts = access.split(".")
                if len(parts) != 3:
                    raise ValueError
                payload = _strict_json(_b64url_decode(parts[1], 12_000))
                if not isinstance(payload, dict):
                    raise ValueError
                expiry = payload.get("exp")
            except (ValueError, TypeError, UnicodeError, RecursionError):
                raise ProtocolError("invalid_token") from None
        try:
            seconds = int(_numeric_date(expiry) - now.timestamp())
        except (ValueError, TypeError, OverflowError):
            raise ProtocolError("invalid_token") from None
    if isinstance(seconds, bool) or not isinstance(seconds, int) or not 1 <= seconds <= 31_536_000:
        raise ProtocolError("invalid_token")
    return seconds


def _token_set(data: object, *, existing: SubscriptionGrant | RefreshedSubscriptionGrant | None, now: datetime) -> SubscriptionGrant | RefreshedSubscriptionGrant:
    try:
        if not isinstance(data, dict) or len(json.dumps(data).encode("utf-8")) > MAX_OAUTH_BODY_BYTES:
            raise ValueError
    except (ValueError, TypeError, RecursionError, UnicodeError):
        raise ProtocolError("invalid_token") from None
    token_type = data.get("token_type", "Bearer")
    if not isinstance(token_type, str) or token_type.lower() != "bearer":
        raise ProtocolError("invalid_token")
    if data.get("client_id", CLIENT_ID) != CLIENT_ID:
        raise ProtocolError("invalid_token")
    access = _bounded_string(data.get("access_token"))
    refresh_raw = data.get("refresh_token")
    refresh = _bounded_string(refresh_raw) if refresh_raw is not None else (
        existing.refresh_token.get_secret_value() if existing is not None and existing.refresh_token is not None else None
    )
    if refresh is None:
        raise ProtocolError("invalid_token")
    raw_scope = data.get("scope", existing.scope if existing is not None else "")
    if not isinstance(raw_scope, str):
        raise ProtocolError("invalid_token")
    scopes = raw_scope.split()
    if (len(scopes) != len(set(scopes)) or any(scope not in _SCOPE_SET for scope in scopes)
            or (raw_scope and raw_scope != " ".join(scopes))):
        raise ProtocolError("invalid_token")
    claims: dict[str, object] | None = None
    if data.get("id_token") is not None or existing is None:
        token_id = _bounded_string(data.get("id_token"))
        claims = verify_id_token(token_id, now=now, expected_subject=existing.subject if existing else None)
        subject = _bounded_string(claims.get("sub"), 512)
        flat_workspace = claims.get("chatgpt_account_id")
        namespaced = claims.get("https://api.openai.com/auth")
        nested_workspace = namespaced.get("chatgpt_account_id") if isinstance(namespaced, dict) else None
        if flat_workspace is not None and nested_workspace is not None and flat_workspace != nested_workspace:
            raise ProtocolError("identity_mismatch")
        workspace = _bounded_string(flat_workspace if flat_workspace is not None else nested_workspace, 512)
    else:
        token_id = existing.id_token.get_secret_value()
        subject = existing.subject
        workspace = _bounded_string(existing.workspace_id, 512)
    if existing is not None and workspace != existing.workspace_id:
        raise ProtocolError("identity_mismatch")
    expires_in = _expires_in(data, claims, access, now)
    values = dict(
        access_token=SecretStr(access), refresh_token=SecretStr(refresh), id_token=SecretStr(token_id),
        token_type="Bearer", expires_in=expires_in, scope=" ".join(scopes), client_id=CLIENT_ID,
        subject=subject, workspace_id=workspace, transport="codex_compat", received_at=now,
        expires_at=now + timedelta(seconds=expires_in),
    )
    try:
        result = RefreshedSubscriptionGrant.model_validate(values) if existing is not None else SubscriptionGrant.model_validate(values)
    except ValidationError:
        raise ProtocolError("invalid_token") from None
    if not result.inference_enabled:
        raise ProtocolError("invalid_token")
    return result


def exchange_device_code(code: str, verifier: str, challenge: str, *, now: datetime | None = None) -> SubscriptionGrant:
    if (not 43 <= len(verifier) <= 128 or not verifier.isascii()
            or not re.fullmatch(r"[A-Za-z0-9._~-]+", verifier)
            or not hmac.compare_digest(_pkce_challenge(verifier), challenge)):
        raise ProtocolError("invalid_token")
    try:
        data = fetch_chatgpt_oauth_form(
            TOKEN_PATH, {
                "grant_type": "authorization_code", "client_id": CLIENT_ID,
                "code": code, "code_verifier": verifier, "redirect_uri": REDIRECT_URI,
            }, timeout_seconds=20, allow_oauth_error=True,
        )
    except OutboundError as exc:
        raise ProtocolError("provider_failure", oauth_error=exc.oauth_error, status=exc.status) from None
    result = _token_set(data, existing=None, now=_now(now))
    assert isinstance(result, SubscriptionGrant)
    return result


def _pkce_challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")


def refresh_tokens(tokens: SubscriptionGrant | RefreshedSubscriptionGrant, *, now: datetime | None = None) -> RefreshedSubscriptionGrant:
    if tokens.transport != "codex_compat" or tokens.client_id != CLIENT_ID or tokens.refresh_token is None or not tokens.workspace_id:
        raise ProtocolError("refresh_terminal")
    try:
        data = fetch_chatgpt_oauth_json(TOKEN_PATH, {
            "client_id": CLIENT_ID, "grant_type": "refresh_token",
            "refresh_token": tokens.refresh_token.get_secret_value(), "scope": CODEX_SCOPES,
        }, timeout_seconds=20, allow_oauth_error=True)
    except OutboundError as exc:
        if exc.oauth_error in OAUTH_TERMINAL_REFRESH_ERRORS:
            raise ProtocolError("refresh_terminal", oauth_error=exc.oauth_error, status=exc.status) from None
        raise ProtocolError("provider_failure", oauth_error=exc.oauth_error, status=exc.status) from None
    result = _token_set(data, existing=tokens, now=_now(now))
    assert isinstance(result, RefreshedSubscriptionGrant)
    return result


def revoke_refresh_token(tokens: SubscriptionGrant | RefreshedSubscriptionGrant) -> bool:
    if tokens.transport != "codex_compat" or tokens.refresh_token is None:
        return False
    try:
        fetch_chatgpt_oauth_form(REVOKE_PATH, {
            "token": tokens.refresh_token.get_secret_value(), "token_type_hint": "refresh_token",
            "client_id": CLIENT_ID,
        }, timeout_seconds=15, allow_empty_success=True)
        return True
    except OutboundError:
        return False
