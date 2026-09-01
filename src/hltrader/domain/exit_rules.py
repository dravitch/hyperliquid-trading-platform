from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class ExitReason(StrEnum):
    RSI = "rsi_exit"
    PRICE = "price_exit"


class PriceDirection(StrEnum):
    ABOVE = "above"
    BELOW = "below"


@dataclass(frozen=True, slots=True)
class ExitDecision:
    should_exit: bool
    reason: ExitReason | None = None

    def __post_init__(self) -> None:
        if self.should_exit != (self.reason is not None):
            raise ValueError("an exit decision must have exactly one reason")


@dataclass(frozen=True, slots=True)
class RsiExitRule:
    threshold: Decimal = Decimal(20)

    def evaluate(self, rsi: Decimal | None) -> ExitDecision:
        if rsi is None:
            return ExitDecision(False)
        if not Decimal(0) <= rsi <= Decimal(100):
            raise ValueError("RSI must be between 0 and 100")
        return ExitDecision(
            rsi <= self.threshold, ExitReason.RSI if rsi <= self.threshold else None
        )


@dataclass(frozen=True, slots=True)
class PriceExitRule:
    level: Decimal
    direction: PriceDirection

    def __post_init__(self) -> None:
        if self.level <= 0:
            raise ValueError("price level must be positive")

    def evaluate(self, mark_price: Decimal) -> ExitDecision:
        if mark_price <= 0:
            raise ValueError("mark price must be positive")
        triggered = (
            mark_price >= self.level
            if self.direction is PriceDirection.ABOVE
            else mark_price <= self.level
        )
        return ExitDecision(triggered, ExitReason.PRICE if triggered else None)
