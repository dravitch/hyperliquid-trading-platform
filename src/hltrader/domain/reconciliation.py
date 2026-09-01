from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from hltrader.persistence.run_journal import RunRecord

from .state_machine import StrategySnapshot, StrategyState


@dataclass(frozen=True, slots=True)
class ExchangeSnapshot:
    net_short_qty: Decimal
    protected_qty: Decimal
    protection_conflict: str | None = None
    open_exit_qty: Decimal = Decimal(0)
    exit_conflict: str | None = None

    def __post_init__(self) -> None:
        if self.net_short_qty < 0 or self.protected_qty < 0 or self.open_exit_qty < 0:
            raise ValueError("quantities cannot be negative")


@dataclass(frozen=True, slots=True)
class OpenExitOrder:
    order_id: str
    remaining_qty: Decimal

    def __post_init__(self) -> None:
        if self.remaining_qty < 0:
            raise ValueError("remaining exit quantity cannot be negative")


def reconstruct_exit_outstanding(
    journaled_order_ids: set[str],
    open_orders: tuple[OpenExitOrder, ...],
) -> tuple[dict[str, Decimal], str | None]:
    """Attribute open exits by identity; never infer ownership from side and flags alone."""
    order_ids = [order.order_id for order in open_orders]
    if len(order_ids) != len(set(order_ids)):
        return {}, "duplicate open exit identity in venue snapshot"
    if any(order_id not in journaled_order_ids for order_id in order_ids):
        return {}, "open reduce-only exit cannot be uniquely attributed to this run"
    return {order.order_id: order.remaining_qty for order in open_orders}, None


def reconcile(journal: RunRecord | None, exchange: ExchangeSnapshot) -> StrategySnapshot:
    """Exchange exposure wins; journal supplies run intent. Ambiguity fails closed."""
    conflict = exchange.protection_conflict or exchange.exit_conflict
    if conflict is not None:
        return StrategySnapshot(
            StrategyState.STATE_CONFLICT,
            exchange.net_short_qty,
            exchange.protected_qty,
            conflict,
        )
    if exchange.open_exit_qty > exchange.net_short_qty:
        return StrategySnapshot(
            StrategyState.STATE_CONFLICT,
            exchange.net_short_qty,
            exchange.protected_qty,
            "open exit quantity exceeds position",
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

    if journal.state in {StrategyState.EXITING, StrategyState.EMERGENCY_EXIT}:
        if exchange.net_short_qty == 0 and exchange.open_exit_qty == 0:
            return StrategySnapshot(
                StrategyState.CLOSED_FINAL,
                exit_reason=journal.exit_reason,
            )
        return StrategySnapshot(
            journal.state,
            exchange.net_short_qty,
            min(exchange.protected_qty, exchange.net_short_qty),
            journal.exit_reason,
        )

    if exchange.protected_qty > exchange.net_short_qty:
        return StrategySnapshot(
            StrategyState.STATE_CONFLICT,
            exchange.net_short_qty,
            exchange.protected_qty,
            "venue protection exceeds position",
        )

    if journal.state is StrategyState.RECOVERY_REQUIRED:
        return StrategySnapshot(
            StrategyState.RECOVERY_REQUIRED,
            exchange.net_short_qty,
            min(exchange.protected_qty, exchange.net_short_qty),
            journal.exit_reason,
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
