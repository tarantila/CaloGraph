"""Deterministic projections for Google Health RPC ``double`` fields."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation, localcontext

from app.importers.common import ORIGINAL_VALUE_LIMIT

GOOGLE_DOUBLE_SOURCE_SCALE = 12
GOOGLE_DOUBLE_CANONICAL_SCALE = 6
_GOOGLE_DOUBLE_SOURCE_QUANTUM = Decimal("1e-12")
_GOOGLE_DOUBLE_CANONICAL_QUANTUM = Decimal("1e-6")


class GoogleDoubleNormalizationError(ValueError):
    """A Google ``double`` cannot be projected into CaloGraph storage scales."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class GoogleDoubleProjection:
    source_value: Decimal
    canonical_value: Decimal


def normalize_google_double(value: Decimal) -> GoogleDoubleProjection:
    """Project one documented Google ``double`` directly to both scales.

    Google Health RPC fields use binary ``double`` values and expose them as
    JSON numbers; their decimal scale is therefore not provider-guaranteed.
    JSON is decoded as Decimal first, then CaloGraph explicitly projects the
    unrounded Decimal onto fixed source and canonical storage scales.
    """
    if not isinstance(value, Decimal):
        raise GoogleDoubleNormalizationError("not_numeric")
    if not value.is_finite():
        raise GoogleDoubleNormalizationError("non_finite")
    if value < 0:
        raise GoogleDoubleNormalizationError("negative")
    if value >= ORIGINAL_VALUE_LIMIT:
        raise GoogleDoubleNormalizationError("exceeds_numeric_range")

    precision = max(38, len(value.as_tuple().digits) + 24)
    with localcontext() as context:
        context.prec = precision
        try:
            source_value = value.quantize(
                _GOOGLE_DOUBLE_SOURCE_QUANTUM,
                rounding=ROUND_HALF_EVEN,
            )
            # Always quantize canonical_value directly from the unrounded input.
            canonical_value = value.quantize(
                _GOOGLE_DOUBLE_CANONICAL_QUANTUM,
                rounding=ROUND_HALF_EVEN,
            )
        except InvalidOperation:
            raise GoogleDoubleNormalizationError("decimal_conversion_failed") from None
    return GoogleDoubleProjection(
        source_value=source_value,
        canonical_value=canonical_value,
    )
