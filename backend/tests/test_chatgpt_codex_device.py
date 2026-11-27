import base64
import json
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from pydantic import SecretStr

from app.ai import chatgpt_codex_device as device
from app.ai.chatgpt_codex_device import ProtocolError
from app.ai.outbound import OutboundError
from app.ai.subscription_device_contracts import SubscriptionGrant

CLIENT = "app_EMoamEEZ73f0CkXaXp7hrann"


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


@pytest.fixture
def signing(monkeypatch):
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    numbers = private.public_key().public_numbers()
    jwks = {"keys": [{
        "kty": "RSA", "kid": "synthetic", "alg": "RS256", "use": "sig",
        "n": _b64(numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")),
        "e": _b64(numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big")),
    }]}
    monkeypatch.setattr(device, "fetch_chatgpt_jwks", lambda: jwks)
    now = datetime.now(UTC).replace(microsecond=0)

    def token(**changes):
        header = _b64(json.dumps({"alg": "RS256", "kid": "synthetic", "typ": "JWT"}).encode())
        claims = {"iss": device.ISSUER, "aud": CLIENT, "sub": "synthetic-subject",
                  "iat": int(now.timestamp()), "exp": int((now + timedelta(hours=1)).timestamp()),
                  "chatgpt_account_id": "workspace-A"}
        claims.update(changes)
        payload = _b64(json.dumps(claims).encode())
        message = f"{header}.{payload}"
        signature = private.sign(message.encode(), padding.PKCS1v15(), hashes.SHA256())
        return f"{message}.{_b64(signature)}"

    return now, token


def test_device_start_posts_fixed_client_and_bounds_returned_data(monkeypatch):
    expiry = (datetime.now(UTC) + timedelta(minutes=10)).isoformat()
    captured = {}

    def fetch(path, body, **kwargs):
        captured.update(path=path, body=body, **kwargs)
        return 200, {"device_auth_id": "synthetic-device", "user_code": "ABCD-EFGH",
                     "interval": "5", "expires_at": expiry}

    monkeypatch.setattr(device, "fetch_chatgpt_auth_json", fetch)
    result = device.begin_device_authorization()
    assert result["device_auth_id"] == "synthetic-device"
    assert result["user_code"] == "ABCD-EFGH"
    assert captured["path"] == device.DEVICE_START
    assert captured["body"] == b'{"client_id":"' + CLIENT.encode() + b'"}'


@pytest.mark.parametrize("interval", [0, -1, 61, True, pytest.param("9" * 5000, id="oversized-decimal-string")])
def test_device_start_rejects_invalid_poll_interval(monkeypatch, interval):
    monkeypatch.setattr(device, "fetch_chatgpt_auth_json", lambda *_a, **_k: (200, {
        "device_auth_id": "synthetic-device", "user_code": "ABCD", "interval": interval,
    }))
    with pytest.raises(ProtocolError, match="invalid_token"):
        device.begin_device_authorization()


@pytest.mark.parametrize("status", [403, 404])
def test_only_device_poll_treats_403_404_as_pending(monkeypatch, status):
    monkeypatch.setattr(device, "fetch_chatgpt_auth_json", lambda *_a, **_k: (status, None))
    assert device.poll_device_authorization("synthetic-device", "ABCD") == ("pending", None)


def test_device_poll_denial_and_success_shapes(monkeypatch):
    monkeypatch.setattr(device, "fetch_chatgpt_auth_json", lambda *_a, **_k: (200, {"error": "access_denied"}))
    assert device.poll_device_authorization("synthetic-device", "ABCD") == ("denied", None)
    payload = {"authorization_code": "synthetic-code", "code_verifier": "v" * 43,
               "code_challenge": "c" * 43}
    monkeypatch.setattr(device, "fetch_chatgpt_auth_json", lambda *_a, **_k: (200, payload))
    assert device.poll_device_authorization("synthetic-device", "ABCD") == ("authorized", payload)


@pytest.mark.parametrize("error", [{"code": "synthetic"}, ["synthetic"], None, "unknown-error"])
def test_device_poll_rejects_malformed_error_with_static_protocol_failure(monkeypatch, error):
    monkeypatch.setattr(device, "fetch_chatgpt_auth_json", lambda *_a, **_k: (200, {"error": error}))
    with pytest.raises(ProtocolError, match=r"^invalid_token$"):
        device.poll_device_authorization("synthetic-device", "ABCD")


def test_exchange_validates_pkce_and_real_signed_identity(monkeypatch, signing):
    now, jwt = signing
    verifier = "a" * 43
    challenge = device._pkce_challenge(verifier)
    seen = {}
    monkeypatch.setattr(device, "fetch_chatgpt_oauth_form", lambda path, fields, **kwargs: (
        seen.update(path=path, fields=fields, **kwargs) or {
            "token_type": "Bearer", "access_token": "synthetic-access", "refresh_token": "synthetic-refresh",
            "id_token": jwt(), "expires_in": 3600,
        }
    ))
    result = device.exchange_device_code("synthetic-code", verifier, challenge, now=now)
    assert result.transport == "codex_compat"
    assert result.subject == "synthetic-subject" and result.workspace_id == "workspace-A"
    assert result.scope == "" and "chatgpt.tokens.use.direct" not in result.granted_scopes
    assert seen["path"] == device.TOKEN_PATH
    assert seen["fields"]["code_verifier"] == verifier
    assert "synthetic-refresh" not in repr(result)
    with pytest.raises(ProtocolError, match="invalid_token"):
        device.exchange_device_code("synthetic-code", verifier, "wrong", now=now)


@pytest.mark.parametrize("claims", [
    {"iss": "https://wrong.invalid"}, {"aud": "another-client"},
    {"azp": "another-client", "aud": [CLIENT, "another-client"]},
    {"exp": 1}, {"chatgpt_account_id": ""},
])
def test_exchange_rejects_invalid_signed_claims(monkeypatch, signing, claims):
    now, jwt = signing
    monkeypatch.setattr(device, "fetch_chatgpt_oauth_form", lambda *_a, **_k: {
        "token_type": "Bearer", "access_token": "synthetic-access", "refresh_token": "synthetic-refresh",
        "id_token": jwt(**claims), "expires_in": 3600,
    })
    verifier = "b" * 43
    with pytest.raises(ProtocolError):
        device.exchange_device_code("synthetic-code", verifier, device._pkce_challenge(verifier), now=now)


def test_exchange_rejects_modified_signature(monkeypatch, signing):
    now, jwt = signing
    token = jwt()
    parts = token.split(".")
    altered = parts[0] + "." + parts[1] + "." + _b64(b"invalid-signature")
    monkeypatch.setattr(device, "fetch_chatgpt_oauth_form", lambda *_a, **_k: {
        "token_type": "Bearer", "access_token": "synthetic-access", "refresh_token": "synthetic-refresh",
        "id_token": altered, "expires_in": 3600,
    })
    verifier = "c" * 43
    with pytest.raises(ProtocolError, match="invalid_token"):
        device.exchange_device_code("synthetic-code", verifier, device._pkce_challenge(verifier), now=now)


def test_identity_key_fetch_timeout_is_a_provider_failure(monkeypatch, signing):
    now, jwt = signing

    def fail_key_fetch():
        raise OutboundError("timeout")

    monkeypatch.setattr(device, "fetch_chatgpt_jwks", fail_key_fetch)
    with pytest.raises(ProtocolError, match=r"^provider_failure$"):
        device.verify_id_token(jwt(), now=now)


def _grant():
    now = datetime.now(UTC)
    return SubscriptionGrant(access_token=SecretStr("old-access"), refresh_token=SecretStr("old-refresh"),
                    id_token=SecretStr("verified-id"), token_type="Bearer", expires_in=1, scope="",
                    client_id=CLIENT, subject="synthetic-subject", workspace_id="workspace-A",
                    transport="codex_compat", received_at=now, expires_at=now + timedelta(seconds=1))


def test_refresh_rotates_credentials_and_keeps_verified_identity_when_id_token_omitted(monkeypatch, signing):
    now, _jwt = signing
    seen = {}
    monkeypatch.setattr(device, "fetch_chatgpt_oauth_json", lambda path, fields, **kwargs: (
        seen.update(path=path, fields=fields) or {"access_token": "new-access", "refresh_token": "new-refresh",
                                                   "expires_in": 3600}
    ))
    result = device.refresh_tokens(_grant(), now=now)
    assert result.access_token.get_secret_value() == "new-access"
    assert result.refresh_token.get_secret_value() == "new-refresh"
    assert result.subject == "synthetic-subject" and result.workspace_id == "workspace-A"
    assert seen["fields"]["grant_type"] == "refresh_token"


def test_refresh_rejects_subject_or_workspace_change(monkeypatch, signing):
    now, jwt = signing
    for claims in ({"sub": "different-subject"}, {"chatgpt_account_id": "workspace-B"}):
        monkeypatch.setattr(device, "fetch_chatgpt_oauth_json", lambda *_a, _claims=claims, **_k: {
            "token_type": "Bearer", "access_token": "new-access", "refresh_token": "new-refresh",
            "id_token": jwt(**_claims), "expires_in": 3600,
        })
        with pytest.raises(ProtocolError, match="identity_mismatch"):
            device.refresh_tokens(_grant(), now=now)


@pytest.mark.parametrize("code", ["invalid_grant", "invalid_refresh_token", "refresh_token_reused", "token_expired"])
def test_refresh_exposes_only_allowlisted_oauth_error_and_revoke_is_best_effort(monkeypatch, code):
    def fail(*_args, **_kwargs):
        raise OutboundError("http_error", status=400, oauth_error=code)
    monkeypatch.setattr(device, "fetch_chatgpt_oauth_json", fail)
    with pytest.raises(ProtocolError) as error:
        device.refresh_tokens(_grant())
    assert error.value.oauth_error == code
    assert error.value.code == "refresh_terminal"
    monkeypatch.setattr(device, "fetch_chatgpt_oauth_form", lambda *_a, **_k: (_ for _ in ()).throw(OutboundError("unavailable")))
    assert device.revoke_refresh_token(_grant()) is False
