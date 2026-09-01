from decimal import Decimal

from hltrader.domain.reconciliation import ExchangeSnapshot, reconcile
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
