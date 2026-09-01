from decimal import Decimal

from hltrader.domain.state_machine import StrategySnapshot, StrategyState, StrategyStateMachine
from hltrader.orchestration.submission import submit_entry_safely, submit_protection_safely


def raising_submit() -> None:
    raise RuntimeError("injected synchronous failure")


def test_entry_submission_exception_persists_recovery_required() -> None:
    machine = StrategyStateMachine()
    machine.begin_entry()
    persisted = []
    errors = []

    result = submit_entry_safely(
        machine,
        submit=raising_submit,
        persist=lambda: persisted.append(machine.snapshot),
        report_error=errors.append,
    )

    assert result is False
    assert machine.snapshot.state is StrategyState.RECOVERY_REQUIRED
    assert persisted[-1].state is StrategyState.RECOVERY_REQUIRED
    assert errors == ["Entry submission failed; manual recovery required"]


def test_protection_timer_is_armed_before_submit_and_failure_flattens() -> None:
    machine = StrategyStateMachine(
        StrategySnapshot(StrategyState.PROTECTING, Decimal("0.01"), Decimal(0))
    )
    actions = []

    def submit_after_asserting_timer() -> None:
        assert actions == ["timer_armed"]
        raising_submit()

    result = submit_protection_safely(
        machine,
        arm_timer=lambda: actions.append("timer_armed"),
        cancel_timer=lambda: actions.append("timer_canceled"),
        submit=submit_after_asserting_timer,
        persist=lambda: actions.append(f"persisted:{machine.snapshot.state.value}"),
        emergency_flatten=lambda: actions.append("flatten"),
        report_error=lambda _: actions.append("reported"),
    )

    assert result is False
    assert machine.snapshot.state is StrategyState.EMERGENCY_EXIT
    assert actions == [
        "timer_armed",
        "timer_canceled",
        "persisted:EMERGENCY_EXIT",
        "flatten",
        "reported",
    ]
