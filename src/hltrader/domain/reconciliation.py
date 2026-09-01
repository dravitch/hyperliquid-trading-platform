from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from hltrader.persistence.run_journal import RunRecord

from .state_machine import StrategySnapshot, StrategyState


@dataclass(frozen=True, slots=True)
class ExchangeSnapshot:
    net_short_qty: Decimal
    protected_qty: Decimal

    def __post_init__(self) -> None:
        if self.net_short_qty < 0 or self.protected_qty < 0:
            raise ValueError("quantities cannot be negative")


def reconcile(journal: RunRecord | None, exchange: ExchangeSnapshot) -> StrategySnapshot:
    """Exchange exposure wins; journal supplies run intent. Ambiguity fails closed."""
    if exchange.protected_qty > exchange.net_short_qty:
        return StrategySnapshot(
            StrategyState.STATE_CONFLICT,
            exchange.net_short_qty,
            exchange.protected_qty,
            "venue protection exceeds position",
        )

    if journal is None:
        if exchange.net_short_qty == 0 and exchange.protected_qty == 0:
            return StrategySnapshot(StrategyState.NEVER_ENTERED)
        return StrategySnapshot(
            StrategyState.STATE_CONFLICT,
            exchange.net_short_qty,
            exchange.protected_qty,
            "exchange exposure exists without a local run journal",
        )

    if journal.state is StrategyState.CLOSED_FINAL:
        if exchange.net_short_qty == 0 and exchange.protected_qty == 0:
            return StrategySnapshot(StrategyState.CLOSED_FINAL, exit_reason=journal.exit_reason)
        return StrategySnapshot(
            StrategyState.STATE_CONFLICT,
            exchange.net_short_qty,
            exchange.protected_qty,
            "journal is closed but exchange exposure remains",
        )

    if exchange.net_short_qty == 0:
        return StrategySnapshot(
            StrategyState.RECOVERY_REQUIRED,
            exit_reason="journal expects an active run but exchange has no position",
        )

    state = (
        StrategyState.OPEN
        if exchange.protected_qty == exchange.net_short_qty
        else StrategyState.PROTECTING
    )
    return StrategySnapshot(state, exchange.net_short_qty, exchange.protected_qty)
