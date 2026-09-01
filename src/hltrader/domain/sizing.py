from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal


@dataclass(frozen=True, slots=True)
class PositionSizing:
    """Converts explicit notional to base quantity; leverage never changes notional."""

    notional_usdc: Decimal

    def __post_init__(self) -> None:
        if self.notional_usdc <= 0:
            raise ValueError("notional_usdc must be positive")

    def base_quantity(self, price: Decimal, quantity_step: Decimal) -> Decimal:
        if price <= 0:
            raise ValueError("price must be positive")
        if quantity_step <= 0:
            raise ValueError("quantity_step must be positive")
        raw = self.notional_usdc / price
        steps = (raw / quantity_step).to_integral_value(rounding=ROUND_DOWN)
        quantity = steps * quantity_step
        if quantity <= 0:
            raise ValueError("notional is too small for the venue quantity step")
        return quantity
