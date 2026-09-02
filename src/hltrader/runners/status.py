from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hltrader.domain.state_machine import StrategyState
from hltrader.persistence.run_journal import JournalError, RunJournal


class StatusReadError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class LocalStatus:
    state: StrategyState
    position_qty: str
    protected_qty: str
    exit_reason: str
    last_event: str

    @property
    def recovery_required(self) -> bool:
        return self.state in {StrategyState.RECOVERY_REQUIRED, StrategyState.STATE_CONFLICT}

    def render(self) -> str:
        return "\n".join(
            (
                f"state: {self.state.value}",
                f"position_qty: {self.position_qty}",
                f"protected_qty: {self.protected_qty}",
                f"exit_reason: {self.exit_reason}",
                f"last_event: {self.last_event}",
                f"recovery_required: {str(self.recovery_required).lower()}",
            )
        )


def _last_trace_event(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if not lines:
            return None
        value = json.loads(lines[-1])
    except (OSError, json.JSONDecodeError) as exc:
        raise StatusReadError("cannot read audit trace") from exc
    if not isinstance(value, dict):
        raise StatusReadError("invalid audit trace event")
    return value


def read_local_status(journal_path: Path, audit_path: Path) -> LocalStatus:
    """Read journal and trace only; this function never creates or modifies either file."""
    try:
        record = RunJournal(journal_path).load()
    except JournalError as exc:
        raise StatusReadError(str(exc)) from exc
    trace = _last_trace_event(audit_path)
    state = record.state if record is not None else StrategyState.NEVER_ENTERED
    return LocalStatus(
        state=state,
        position_qty=str(trace.get("actual_qty", "-")) if trace else "-",
        protected_qty=str(trace.get("protected_qty", "-")) if trace else "-",
        exit_reason=record.exit_reason or "-" if record else "-",
        last_event=str(trace.get("event", "-")) if trace else "-",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only HNT local orchestration status")
    parser.add_argument("--journal", type=Path, default=Path("var/run/short_btc_rsi.json"))
    parser.add_argument("--audit", type=Path, default=Path("var/log/short_btc_rsi.audit.jsonl"))
    return parser


def main() -> None:
    args = _parser().parse_args()
    print(read_local_status(args.journal, args.audit).render())


if __name__ == "__main__":
    main()
