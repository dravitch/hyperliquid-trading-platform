from __future__ import annotations

from collections.abc import Callable

from hltrader.domain.state_machine import StrategyStateMachine


def submit_entry_safely(
    machine: StrategyStateMachine,
    *,
    submit: Callable[[], None],
    persist: Callable[[], None],
    report_error: Callable[[str], None],
) -> bool:
    """Submit an already-journaled entry or require manual recovery."""
    try:
        submit()
    except Exception as exc:  # noqa: BLE001 - deliberate framework boundary
        machine.recovery_required(f"entry submission failed: {exc}")
        persist()
        report_error("Entry submission failed; manual recovery required")
        return False
    return True


def submit_protection_safely(
    machine: StrategyStateMachine,
    *,
    arm_timer: Callable[[], None],
    cancel_timer: Callable[[], None],
    submit: Callable[[], None],
    persist: Callable[[], None],
    emergency_flatten: Callable[[], None],
    report_error: Callable[[str], None],
) -> bool:
    """Arm the watchdog before crossing the venue submission boundary."""
    arm_timer()
    try:
        submit()
    except Exception as exc:  # noqa: BLE001 - deliberate framework boundary
        cancel_timer()
        machine.protection_failed(f"protective trigger submission failed: {exc}")
        persist()
        emergency_flatten()
        report_error("Protective trigger submission failed; emergency exit requested")
        return False
    return True
