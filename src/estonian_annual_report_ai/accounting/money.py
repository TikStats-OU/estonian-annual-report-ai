"""Exact EUR cent arithmetic independent of Decimal's ambient context."""

from collections.abc import Iterable
from decimal import Decimal


def to_minor_units(value: Decimal) -> int:
    """Convert a finite amount with at most two decimal places to cents."""
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError("Money values must be finite Decimal instances.")
    sign, digits, exponent = value.as_tuple()
    coefficient = int("".join(map(str, digits)) or "0")
    if exponent < -2:
        divisor = 10 ** (-2 - exponent)
        if coefficient % divisor:
            raise ValueError("EUR amounts may have at most two decimal places.")
        cents = coefficient // divisor
    else:
        cents = coefficient * (10 ** (exponent + 2))
    return -cents if sign else cents


def from_minor_units(cents: int) -> Decimal:
    """Create an exact Decimal EUR amount without context rounding."""
    sign = 1 if cents < 0 else 0
    digits = tuple(int(char) for char in str(abs(cents))) or (0,)
    return Decimal((sign, digits, -2))


def sum_money(values: Iterable[Decimal]) -> Decimal:
    return from_minor_units(sum(to_minor_units(value) for value in values))


def subtract_money(left: Decimal, right: Decimal) -> Decimal:
    return from_minor_units(to_minor_units(left) - to_minor_units(right))
