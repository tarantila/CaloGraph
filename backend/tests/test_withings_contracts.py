from __future__ import annotations

import pytest

from app.withings.constants import (
    WITHINGS_ACTIVITY_URL,
    WITHINGS_API_BASE_URL,
    WITHINGS_AUTHORIZE_URL,
    WITHINGS_MAX_PAGE_SIZE,
    WITHINGS_MAX_PAGES,
    WITHINGS_MEASURE_TYPES,
    WITHINGS_REQUIRED_SCOPES,
    WITHINGS_SCOPES,
    WITHINGS_TOKEN_URL,
)

from app.withings.oauth import validate_redirect_uri


def test_withings_required_scopes_are_both_activity_and_metrics() -> None:
    assert WITHINGS_REQUIRED_SCOPES == frozenset({"user.metrics", "user.activity"})


@pytest.mark.parametrize(
    "redirect_uri",
    (
        "http://localhost:8180/api/v1/withings/oauth/callback",
        "https://calograph.example.com/api/v1/withings/oauth/callback",
    ),
)
def test_validate_redirect_uri_accepts_secure_callback_targets(redirect_uri: str) -> None:
    assert validate_redirect_uri(redirect_uri) == redirect_uri


@pytest.mark.parametrize(
    "redirect_uri",
    (
        "http://calograph.example.com/api/v1/withings/oauth/callback",
        "ftp://calograph.example.com/api/v1/withings/oauth/callback",
        "https://client:secret@example.test/withings/callback",
        "https://example.test/callback?next=elsewhere",
        "https://example.test/callback#fragment",
    ),
)
def test_validate_redirect_uri_rejects_unsafe_callback_targets(redirect_uri: str) -> None:
    with pytest.raises(ValueError):
        validate_redirect_uri(redirect_uri)


def test_withings_uses_fixed_official_urls_and_minimal_scopes() -> None:
    assert WITHINGS_API_BASE_URL == "https://wbsapi.withings.net"
    assert WITHINGS_AUTHORIZE_URL == "https://account.withings.com/oauth2_user/authorize2"
    assert WITHINGS_TOKEN_URL == "https://wbsapi.withings.net/v2/oauth2"
    assert WITHINGS_ACTIVITY_URL == "https://wbsapi.withings.net/v2/measure"
    assert WITHINGS_SCOPES == ("user.metrics", "user.activity")
    assert "user.info" not in WITHINGS_SCOPES


def test_withings_supported_measure_types_are_exact_and_have_canonical_units() -> None:
    assert set(WITHINGS_MEASURE_TYPES) == {1, 5, 6, 8, 76, 77, 88}
    assert WITHINGS_MEASURE_TYPES[1] == ("weight_kg", "kg")
    assert WITHINGS_MEASURE_TYPES[5] == ("fat_free_mass_kg", "kg")
    assert WITHINGS_MEASURE_TYPES[6] == ("fat_ratio_pct", "%")
    assert WITHINGS_MEASURE_TYPES[8] == ("fat_mass_kg", "kg")
    assert WITHINGS_MEASURE_TYPES[76] == ("muscle_mass_kg", "kg")
    assert WITHINGS_MEASURE_TYPES[77] == ("hydration_kg", "kg")
    assert WITHINGS_MEASURE_TYPES[88] == ("bone_mass_kg", "kg")


def test_withings_pagination_limits_are_positive_and_bounded() -> None:
    assert 1 <= WITHINGS_MAX_PAGE_SIZE <= 100
    assert 1 <= WITHINGS_MAX_PAGES <= 31
