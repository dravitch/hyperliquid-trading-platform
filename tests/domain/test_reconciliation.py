from decimal import Decimal

from hltrader.domain.reconciliation import (
    ExchangeSnapshot,
    OpenExitOrder,
    reconcile,
    reconstruct_exit_outstanding,
)
from hltrader.domain.state_machine import StrategyState
from hltrader.persistence.run_journal import RunRecord


def test_fresh_empty_account_is_never_entered() -> None:
    result = reconcile(None, ExchangeSnapshot(Decimal(0), Decimal(0)))
    assert result.state is StrategyState.NEVER_ENTERED


def test_exchange_exposure_without_journal_is_conflict() -> None:
    result = reconcile(None, ExchangeSnapshot(Decimal("0.01"), Decimal("0.01")))
    assert result.state is StrategyState.STATE_CONFLICT


def test_closed_journal_never_hides_exchange_exposure() -> None:
    journal = RunRecord("run-1", StrategyState.CLOSED_FINAL)
    result = reconcile(journal, ExchangeSnapshot(Decimal("0.01"), Decimal("0.01")))
    assert result.state is StrategyState.STATE_CONFLICT


def test_restart_reconstructs_protecting_from_exchange_quantities() -> None:
    journal = RunRecord("run-1", StrategyState.PROTECTING)
    result = reconcile(journal, ExchangeSnapshot(Decimal("0.01"), Decimal("0.005")))
    assert result.state is StrategyState.PROTECTING


def test_restart_reconstructs_open_only_when_fully_protected() -> None:
    journal = RunRecord("run-1", StrategyState.OPEN)
    result = reconcile(journal, ExchangeSnapshot(Decimal("0.01"), Decimal("0.01")))
    assert result.state is StrategyState.OPEN


def test_ambiguous_or_replaced_protection_is_never_silently_open() -> None:
    journal = RunRecord("run-1", StrategyState.OPEN, protective_order="old-trigger")
    result = reconcile(
        journal,
        ExchangeSnapshot(
            Decimal("0.01"),
            Decimal(0),
            "journaled protective trigger does not uniquely match venue orders",
        ),
    )
    assert result.state is StrategyState.STATE_CONFLICT


def test_restart_preserves_emergency_intent_with_pending_flatten() -> None:
    journal = RunRecord("run-1", StrategyState.EMERGENCY_EXIT, exit_reason="trigger rejected")
    result = reconcile(
        journal,
        ExchangeSnapshot(
            Decimal("0.006"),
            Decimal(0),
            open_exit_qty=Decimal("0.006"),
        ),
    )
    assert result.state is StrategyState.EMERGENCY_EXIT
    assert result.actual_net_position_qty == Decimal("0.006")


def test_restart_preserves_exiting_intent_after_partial_fill() -> None:
    journal = RunRecord("run-1", StrategyState.EXITING, exit_reason="rsi")
    result = reconcile(
        journal,
        ExchangeSnapshot(
            Decimal("0.004"),
            Decimal(0),
            open_exit_qty=Decimal("0.004"),
        ),
    )
    assert result.state is StrategyState.EXITING
    assert result.actual_net_position_qty == Decimal("0.004")


def test_partial_exit_does_not_let_stale_protector_override_close_intent() -> None:
    journal = RunRecord("run-1", StrategyState.EXITING, exit_reason="rsi")
    result = reconcile(
        journal,
        ExchangeSnapshot(
            Decimal("0.004"),
            Decimal("0.006"),
            open_exit_qty=Decimal("0.004"),
        ),
    )
    assert result.state is StrategyState.EXITING
    assert result.protected_qty == Decimal("0.004")


def test_foreign_exit_order_fails_closed() -> None:
    journal = RunRecord("run-1", StrategyState.EMERGENCY_EXIT)
    result = reconcile(
        journal,
        ExchangeSnapshot(
            Decimal("0.004"),
            Decimal(0),
            exit_conflict="open reduce-only exit cannot be uniquely attributed to this run",
        ),
    )
    assert result.state is StrategyState.STATE_CONFLICT


def test_stale_journal_quantity_cannot_override_exchange_exposure() -> None:
    journal = RunRecord("run-1", StrategyState.EXITING, exit_reason="rsi")
    result = reconcile(journal, ExchangeSnapshot(Decimal("0.004"), Decimal(0)))
    assert result.state is StrategyState.EXITING
    assert result.actual_net_position_qty == Decimal("0.004")


def test_economically_closed_exit_is_reconstructed_final() -> None:
    for state in (StrategyState.EXITING, StrategyState.EMERGENCY_EXIT):
        journal = RunRecord("run-1", state, exit_reason="close requested")
        result = reconcile(journal, ExchangeSnapshot(Decimal(0), Decimal(0)))
        assert result.state is StrategyState.CLOSED_FINAL


def test_recovery_required_is_not_cleared_by_position_snapshot_alone() -> None:
    journal = RunRecord("run-1", StrategyState.RECOVERY_REQUIRED, exit_reason="unknown command")
    result = reconcile(journal, ExchangeSnapshot(Decimal(0), Decimal(0)))
    assert result.state is StrategyState.RECOVERY_REQUIRED


def test_open_exit_reconstruction_uses_remaining_quantity_not_original_quantity() -> None:
    outstanding, conflict = reconstruct_exit_outstanding(
        {"exit-1"},
        (OpenExitOrder("exit-1", Decimal("0.004")),),
    )
    assert conflict is None
    assert outstanding == {"exit-1": Decimal("0.004")}


def test_open_exit_reconstruction_never_sums_foreign_order() -> None:
    outstanding, conflict = reconstruct_exit_outstanding(
        {"exit-1"},
        (
            OpenExitOrder("exit-1", Decimal("0.002")),
            OpenExitOrder("foreign", Decimal("0.002")),
        ),
    )
    assert outstanding == {}
    assert conflict == "open reduce-only exit cannot be uniquely attributed to this run"
