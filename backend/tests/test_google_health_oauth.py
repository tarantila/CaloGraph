import base64
import hashlib
from urllib.parse import parse_qs, urlsplit

import pytest

from app.config import settings
from app.google_health.constants import (
    GOOGLE_HEALTH_AUTH_URI,
    GOOGLE_HEALTH_CALLBACK_PATH,
    GOOGLE_HEALTH_SCOPE,
)
from app.google_health.oauth import (
    build_authorization_url,
    create_oauth_state,
    create_pkce_verifier,
    hash_oauth_state,
    normalize_granted_scopes,
    pkce_challenge,
)

def test_oauth_state_is_random_url_safe_and_hash_is_one_way() -> None:
    first = create_oauth_state()
    second = create_oauth_state()

    assert first != second
    assert first
    assert all(character.isalnum() or character in "-_" for character in first)
    assert hash_oauth_state(first) != first
    assert hash_oauth_state(first) == hash_oauth_state(first)
    assert hash_oauth_state(first) != hash_oauth_state(second)


def test_pkce_verifier_is_random_and_challenge_is_rfc7636_s256() -> None:
    first = create_pkce_verifier()
    second = create_pkce_verifier()
    expected = base64.urlsafe_b64encode(hashlib.sha256(first.encode()).digest()).rstrip(b"=").decode()

    assert first != second
    assert 43 <= len(first) <= 128
    assert all(character.isalnum() or character in "-._~" for character in first)
    assert pkce_challenge(first) == expected
    assert "=" not in pkce_challenge(first)


def test_granted_scopes_are_split_deduplicated_and_sorted() -> None:
    assert normalize_granted_scopes("zeta alpha alpha") == ("alpha", "zeta")
    assert normalize_granted_scopes(["zeta alpha", "beta", "alpha"]) == (
        "alpha",
        "beta",
        "zeta",
    )
    assert normalize_granted_scopes({"scope-b", "scope-a"}) == ("scope-a", "scope-b")
    assert normalize_granted_scopes(None) == ()


def test_authorization_url_contains_exact_read_scope_and_pkce_parameters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "calograph_public_url", "https://nutrition.example.test/")
    state = create_oauth_state()
    verifier = create_pkce_verifier()
    redirect_uri = f"https://nutrition.example.test{GOOGLE_HEALTH_CALLBACK_PATH}"
    url = build_authorization_url(
        client_id="client-id.apps.googleusercontent.com",
        state=state,
        verifier=verifier,
    )
    query = parse_qs(urlsplit(url).query)

    assert url.startswith(GOOGLE_HEALTH_AUTH_URI + "?")
    assert query["client_id"] == ["client-id.apps.googleusercontent.com"]
    assert query["redirect_uri"] == [redirect_uri]
    assert query["response_type"] == ["code"]
    assert query["access_type"] == ["offline"]
    assert query["scope"] == [GOOGLE_HEALTH_SCOPE]
    assert query["state"] == [state]
    assert query["code_challenge_method"] == ["S256"]
    assert query["code_challenge"] == [pkce_challenge(verifier)]
    assert "prompt" not in query


def test_authorization_url_prompts_only_for_consent_intents() -> None:
    kwargs = {
        "client_id": "client-id",
        "state": create_oauth_state(),
        "verifier": create_pkce_verifier(),
    }

    assert parse_qs(urlsplit(build_authorization_url(**kwargs)).query).get("prompt") is None
    reconnect_query = parse_qs(
        urlsplit(build_authorization_url(**kwargs, intent="reconnect")).query
    )
    assert "prompt" not in reconnect_query
    for intent in ("initial", "reauthorize", "missing_refresh", "scope_change"):
        query = parse_qs(urlsplit(build_authorization_url(**kwargs, intent=intent)).query)
        assert query["prompt"] == ["consent"]
    query = parse_qs(urlsplit(build_authorization_url(**kwargs, reauthorize=True)).query)
    assert query["prompt"] == ["consent"]
