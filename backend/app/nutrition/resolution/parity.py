from dataclasses import dataclass
from decimal import Decimal

_STORAGE_QUANTUM = Decimal("1E-12")
_RELATIVE_TOLERANCE = Decimal("1E-15")


@dataclass(frozen=True, slots=True)
class ParityDiagnostic:
    delta: Decimal
    tolerance: Decimal
    within_tolerance: bool
    contribution_count: int
    absolute_tolerance: Decimal
    relative_tolerance: Decimal

    def __post_init__(self) -> None:
        for name, value in (
            ("delta", self.delta),
            ("tolerance", self.tolerance),
            ("absolute_tolerance", self.absolute_tolerance),
            ("relative_tolerance", self.relative_tolerance),
        ):
            if not isinstance(value, Decimal):
                raise TypeError(f"{name} must be Decimal")
            if not value.is_finite():
                raise ValueError(f"{name} must be finite")
        if not isinstance(self.contribution_count, int) or isinstance(self.contribution_count, bool):
            raise TypeError("contribution_count must be an integer")
        if self.contribution_count < 0:
            raise ValueError("contribution_count must be non-negative")


def compare_decimal_parity(
    event_value: Decimal,
    summary_value: Decimal,
    contribution_count: int,
) -> ParityDiagnostic:
    if not isinstance(event_value, Decimal) or not isinstance(summary_value, Decimal):
        raise TypeError("event_value and summary_value must be Decimal")
    if not event_value.is_finite() or not summary_value.is_finite():
        raise ValueError("parity values must be finite")
    if not isinstance(contribution_count, int) or isinstance(contribution_count, bool):
        raise TypeError("contribution_count must be an integer")
    if contribution_count < 0:
        raise ValueError("contribution_count must be non-negative")

    absolute_tolerance = Decimal(contribution_count + 1) * _STORAGE_QUANTUM / Decimal("2")
    relative_tolerance = _RELATIVE_TOLERANCE
    delta = abs(event_value - summary_value)
    tolerance = max(
        absolute_tolerance,
        relative_tolerance * max(abs(event_value), abs(summary_value), Decimal("1")),
    )
    return ParityDiagnostic(
        delta=delta,
        tolerance=tolerance,
        within_tolerance=delta <= tolerance,
        contribution_count=contribution_count,
        absolute_tolerance=absolute_tolerance,
        relative_tolerance=relative_tolerance,
    )
