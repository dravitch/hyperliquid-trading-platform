from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from threading import Lock

from .exit_rules import ExitReason


class StrategyState(StrEnum):
    NEVER_ENTERED = "NEVER_ENTERED"
    ENTERING = "ENTERING"
    PROTECTING = "PROTECTING"
    OPEN = "OPEN"
    EXITING = "EXITING"
    EMERGENCY_EXIT = "EMERGENCY_EXIT"
    CLOSED_FINAL = "CLOSED_FINAL"
    STATE_CONFLICT = "STATE_CONFLICT"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"


class InvalidTransition(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class StrategySnapshot:
    state: StrategyState
    actual_net_position_qty: Decimal = Decimal(0)
    protected_qty: Decimal = Decimal(0)
    exit_reason: str | None = None


class StrategyStateMachine:
    """Thread-safe lifecycle and FIRST_TRIGGER_WINS arbitration."""

    def __init__(self, snapshot: StrategySnapshot | None = None) -> None:
        self._snapshot = snapshot or StrategySnapshot(StrategyState.NEVER_ENTERED)
        self._lock = Lock()

    @property
    def snapshot(self) -> StrategySnapshot:
        with self._lock:
            return self._snapshot

    def begin_entry(self) -> StrategySnapshot:
        return self._transition({StrategyState.NEVER_ENTERED}, StrategyState.ENTERING)

    def record_exposure(
        self, actual_qty: Decimal, protected_qty: Decimal = Decimal(0)
    ) -> StrategySnapshot:
        if actual_qty <= 0 or protected_qty < 0 or protected_qty > actual_qty:
            raise ValueError("invalid exposure/protection quantities")
        with self._lock:
            if self._snapshot.state not in {
                StrategyState.ENTERING,
                StrategyState.PROTECTING,
                StrategyState.OPEN,
            }:
                raise InvalidTransition(f"cannot record exposure from {self._snapshot.state}")
            if actual_qty < self._snapshot.actual_net_position_qty:
                raise ValueError("entry fills cannot reduce actual exposure")
            state = StrategyState.OPEN if protected_qty == actual_qty else StrategyState.PROTECTING
            self._snapshot = StrategySnapshot(state, actual_qty, protected_qty)
            return self._snapshot

    def confirm_protection(self, protected_qty: Decimal) -> StrategySnapshot:
        with self._lock:
            current = self._snapshot
            if current.state not in {StrategyState.PROTECTING, StrategyState.OPEN}:
                raise InvalidTransition(f"cannot confirm protection from {current.state}")
            if protected_qty < 0 or protected_qty > current.actual_net_position_qty:
                raise ValueError("protected quantity cannot exceed actual exposure")
            state = (
                StrategyState.OPEN
                if protected_qty == current.actual_net_position_qty
                else StrategyState.PROTECTING
            )
            self._snapshot = StrategySnapshot(state, current.actual_net_position_qty, protected_qty)
            return self._snapshot

    def request_exit(self, reason: ExitReason) -> bool:
        """Atomically wins the right to submit one normal close request."""
        with self._lock:
            if self._snapshot.state is not StrategyState.OPEN:
                return False
            self._snapshot = StrategySnapshot(
                StrategyState.EXITING,
                self._snapshot.actual_net_position_qty,
                self._snapshot.protected_qty,
                reason.value,
            )
            return True

    def protection_failed(self, reason: str) -> StrategySnapshot:
        with self._lock:
            if self._snapshot.state is not StrategyState.PROTECTING:
                raise InvalidTransition(f"cannot emergency-exit from {self._snapshot.state}")
            self._snapshot = StrategySnapshot(
                StrategyState.EMERGENCY_EXIT,
                self._snapshot.actual_net_position_qty,
                self._snapshot.protected_qty,
                reason,
            )
            return self._snapshot

    def recovery_required(
        self,
        reason: str,
        *,
        allowed: set[StrategyState] | None = None,
    ) -> StrategySnapshot:
        """Fail closed when an orchestration command has an indeterminate outcome."""
        with self._lock:
            current = self._snapshot
            permitted = allowed or {
                StrategyState.ENTERING,
                StrategyState.PROTECTING,
                StrategyState.EXITING,
            }
            if current.state not in permitted:
                raise InvalidTransition(f"cannot require recovery from {current.state}")
            self._snapshot = StrategySnapshot(
                StrategyState.RECOVERY_REQUIRED,
                current.actual_net_position_qty,
                current.protected_qty,
                reason,
            )
            return self._snapshot

    def confirm_closed(self) -> StrategySnapshot:
        return self._transition(
            {StrategyState.EXITING, StrategyState.EMERGENCY_EXIT},
            StrategyState.CLOSED_FINAL,
            clear_quantities=True,
        )

    def mark_conflict(self, reason: str) -> StrategySnapshot:
        with self._lock:
            current = self._snapshot
            self._snapshot = StrategySnapshot(
                StrategyState.STATE_CONFLICT,
                current.actual_net_position_qty,
                current.protected_qty,
                reason,
            )
            return self._snapshot

    def _transition(
        self,
        allowed: set[StrategyState],
        target: StrategyState,
        *,
        clear_quantities: bool = False,
    ) -> StrategySnapshot:
        with self._lock:
            current = self._snapshot
            if current.state not in allowed:
                raise InvalidTransition(f"cannot transition {current.state} -> {target}")
            self._snapshot = StrategySnapshot(
                target,
                Decimal(0) if clear_quantities else current.actual_net_position_qty,
                Decimal(0) if clear_quantities else current.protected_qty,
                current.exit_reason,
            )
            return self._snapshot
