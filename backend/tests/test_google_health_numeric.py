from __future__ import annotations

from decimal import Decimal

import pytest

from app.google_health.numeric import (
    GoogleDoubleNormalizationError,
    normalize_google_double,
)


@pytest.mark.parametrize(
    ("raw", "source", "canonical"),
    [
        ("1", "1.000000000000", "1.000000"),
        ("1.5", "1.500000000000", "1.500000"),
        ("0", "0.000000000000", "0.000000"),
        ("1.123456", "1.123456000000", "1.123456"),
        ("1.123456789012", "1.123456789012", "1.123457"),
        ("1.1234567890123", "1.123456789012", "1.123457"),
        ("1.1234567890123456", "1.123456789012", "1.123457"),
    ],
)
def test_google_double_projects_directly_to_source_and_canonical_scales(
    raw: str,
    source: str,
    canonical: str,
) -> None:
    projection = normalize_google_double(Decimal(raw))

    assert projection.source_value == Decimal(source)
    assert projection.canonical_value == Decimal(canonical)


def test_google_double_canonical_projection_avoids_double_rounding() -> None:
    projection = normalize_google_double(Decimal("1.2345674999995"))

    assert projection.source_value == Decimal("1.234567500000")
    assert projection.canonical_value == Decimal("1.234567")


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        (Decimal("-1"), "negative"),
        (Decimal("NaN"), "non_finite"),
        (Decimal("Infinity"), "non_finite"),
        (Decimal("1000000000000"), "exceeds_numeric_range"),
    ],
)
def test_google_double_rejects_invalid_values(raw: Decimal, reason: str) -> None:
    with pytest.raises(GoogleDoubleNormalizationError) as raised:
        normalize_google_double(raw)

    assert raised.value.reason_code == reason
