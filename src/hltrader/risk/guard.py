from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class RiskViolation(RuntimeError):
    pass


class MarginMode(StrEnum):
    ISOLATED = "isolated"
    CROSS = "cross"


@dataclass(frozen=True, slots=True)
class VenueMarginState:
    mode: MarginMode
    leverage: Decimal


@dataclass(frozen=True, slots=True)
class RiskGuard:
    max_notional_usdc: Decimal
    max_leverage: Decimal
    desired_margin_mode: MarginMode
    desired_leverage: Decimal

    def __post_init__(self) -> None:
        if min(self.max_notional_usdc, self.max_leverage, self.desired_leverage) <= 0:
            raise ValueError("risk limits and desired leverage must be positive")
        if self.desired_leverage > self.max_leverage:
            raise ValueError("desired leverage exceeds configured maximum")

    def authorize_entry(self, notional_usdc: Decimal, venue: VenueMarginState) -> None:
        """Fail closed: absence of a verified venue state is not accepted."""
        if notional_usdc <= 0:
            raise RiskViolation("entry notional must be positive")
        if notional_usdc > self.max_notional_usdc:
            raise RiskViolation("entry notional exceeds maximum")
        if venue.mode is not self.desired_margin_mode:
            raise RiskViolation("venue margin mode does not match desired mode")
        if venue.leverage != self.desired_leverage:
            raise RiskViolation("venue leverage does not match desired leverage")
        if venue.leverage > self.max_leverage:
            raise RiskViolation("venue leverage exceeds maximum")
