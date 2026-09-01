from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest

from hltrader.domain.exit_rules import ExitReason
from hltrader.domain.state_machine import InvalidTransition, StrategyState, StrategyStateMachine


def test_partial_fills_keep_state_protecting_until_total_exposure_is_covered() -> None:
    machine = StrategyStateMachine()
    machine.begin_entry()

    first = machine.record_exposure(Decimal("0.003"))
    assert first.state is StrategyState.PROTECTING
    assert machine.confirm_protection(Decimal("0.003")).state is StrategyState.OPEN

    second = machine.record_exposure(Decimal("0.006"), protected_qty=Decimal("0.003"))
    assert second.state is StrategyState.PROTECTING
    assert machine.confirm_protection(Decimal("0.006")).state is StrategyState.OPEN


def test_first_trigger_wins_atomically() -> None:
    machine = StrategyStateMachine()
    machine.begin_entry()
    machine.record_exposure(Decimal("0.006"), protected_qty=Decimal("0.006"))

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(machine.request_exit, [ExitReason.RSI, ExitReason.PRICE]))

    assert sorted(results) == [False, True]
    assert machine.snapshot.state is StrategyState.EXITING
    assert machine.snapshot.exit_reason in {ExitReason.RSI.value, ExitReason.PRICE.value}


def test_protection_failure_requires_emergency_exit_before_close() -> None:
    machine = StrategyStateMachine()
    machine.begin_entry()
    machine.record_exposure(Decimal("0.006"))
    assert machine.protection_failed("trigger rejected").state is StrategyState.EMERGENCY_EXIT
    assert machine.confirm_closed().state is StrategyState.CLOSED_FINAL


def test_closed_strategy_cannot_reenter() -> None:
    machine = StrategyStateMachine()
    machine.begin_entry()
    machine.record_exposure(Decimal("0.006"), protected_qty=Decimal("0.006"))
    machine.request_exit(ExitReason.RSI)
    machine.confirm_closed()
    with pytest.raises(InvalidTransition):
        machine.begin_entry()


def test_failed_entry_submission_requires_manual_recovery() -> None:
    machine = StrategyStateMachine()
    machine.begin_entry()
    snapshot = machine.recovery_required("synchronous submit failure")
    assert snapshot.state is StrategyState.RECOVERY_REQUIRED
    with pytest.raises(InvalidTransition):
        machine.begin_entry()
