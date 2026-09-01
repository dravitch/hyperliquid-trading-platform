from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from itertools import permutations

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


def test_pending_first_protection_never_opens_after_second_fill() -> None:
    machine = StrategyStateMachine()
    machine.begin_entry()
    machine.record_exposure(Decimal("0.003"), protected_qty=Decimal(0))

    cumulative = machine.record_exposure(Decimal("0.006"), protected_qty=Decimal(0))
    stale_acceptance = machine.confirm_protection(Decimal("0.003"))

    assert cumulative.state is StrategyState.PROTECTING
    assert stale_acceptance.state is StrategyState.PROTECTING
    assert stale_acceptance.actual_net_position_qty == Decimal("0.006")
    assert stale_acceptance.protected_qty == Decimal("0.003")


def test_acceptance_and_timeout_race_has_one_absorbing_winner() -> None:
    for _ in range(50):
        machine = StrategyStateMachine()
        machine.begin_entry()
        machine.record_exposure(Decimal("0.006"))

        def accept(current=machine) -> None:
            current.confirm_protection(Decimal("0.006"))

        def timeout(current=machine) -> None:
            current.protection_failed("timeout")

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(accept), executor.submit(timeout)]
            for future in futures:
                future.result()

        assert machine.snapshot.state in {StrategyState.OPEN, StrategyState.EMERGENCY_EXIT}
        if machine.snapshot.state is StrategyState.EMERGENCY_EXIT:
            assert machine.confirm_protection(Decimal("0.006")).state is StrategyState.EMERGENCY_EXIT
        else:
            assert machine.protection_failed("late timeout").state is StrategyState.OPEN


def test_trigger_rejection_during_resize_is_idempotent_and_preserves_cause() -> None:
    machine = StrategyStateMachine()
    machine.begin_entry()
    machine.record_exposure(Decimal("0.003"), protected_qty=Decimal("0.003"))
    machine.record_exposure(Decimal("0.006"), protected_qty=Decimal("0.003"))

    first = machine.protection_failed("protective resize rejected")
    duplicate = machine.protection_failed("duplicate rejection")

    assert first.state is StrategyState.EMERGENCY_EXIT
    assert first.actual_net_position_qty == Decimal("0.006")
    assert first.protected_qty == Decimal("0.003")
    assert first.exit_reason == "protective resize rejected"
    assert duplicate == first


def test_late_fill_during_emergency_updates_exposure_and_blocks_false_close() -> None:
    machine = StrategyStateMachine()
    machine.begin_entry()
    machine.record_exposure(Decimal("0.003"))
    machine.protection_failed("trigger rejected")

    late_fill = machine.record_emergency_exposure(Decimal("0.006"))
    assert late_fill.state is StrategyState.EMERGENCY_EXIT
    assert late_fill.actual_net_position_qty == Decimal("0.006")
    with pytest.raises(ValueError, match="economic exposure remains"):
        machine.confirm_closed(Decimal("0.006"))

    machine.record_emergency_exposure(Decimal(0))
    assert machine.confirm_closed(Decimal(0)).state is StrategyState.CLOSED_FINAL
    assert machine.confirm_closed(Decimal(0)).state is StrategyState.CLOSED_FINAL


def test_all_orderings_of_fill_accept_timeout_and_reject_preserve_invariant() -> None:
    event_names = ("fill", "accept", "timeout", "reject")
    for ordering in permutations(event_names):
        machine = StrategyStateMachine()
        machine.begin_entry()
        machine.record_exposure(Decimal("0.003"))

        for event_name in ordering:
            state = machine.snapshot.state
            if event_name == "fill":
                if state is StrategyState.EMERGENCY_EXIT:
                    machine.record_emergency_exposure(Decimal("0.006"))
                else:
                    protected = min(machine.snapshot.protected_qty, Decimal("0.003"))
                    machine.record_exposure(Decimal("0.006"), protected)
            elif event_name == "accept":
                machine.confirm_protection(Decimal("0.003"))
            else:
                machine.protection_failed(f"{event_name} won")

        snapshot = machine.snapshot
        assert snapshot.state in {StrategyState.PROTECTING, StrategyState.EMERGENCY_EXIT}, ordering
        assert snapshot.actual_net_position_qty == Decimal("0.006"), ordering
        assert snapshot.protected_qty <= snapshot.actual_net_position_qty, ordering


def test_duplicate_concurrent_closed_events_are_idempotent() -> None:
    machine = StrategyStateMachine()
    machine.begin_entry()
    machine.record_exposure(Decimal("0.003"), Decimal("0.003"))
    machine.request_exit(ExitReason.RSI)

    with ThreadPoolExecutor(max_workers=2) as executor:
        snapshots = list(executor.map(lambda _: machine.confirm_closed(Decimal(0)), range(2)))

    assert all(snapshot.state is StrategyState.CLOSED_FINAL for snapshot in snapshots)
    assert machine.snapshot.actual_net_position_qty == 0
