import json
from decimal import Decimal
from pathlib import Path

from hltrader.domain.state_machine import StrategySnapshot, StrategyState
from hltrader.observability.audit_trace import AuditTrace


def test_trace_schema_and_sequence_are_deterministic(tmp_path: Path) -> None:
    trace = AuditTrace()
    trace.record("entry_fill", StrategySnapshot(StrategyState.PROTECTING, Decimal("0.1")))
    trace.record(
        "trigger_accepted",
        StrategySnapshot(StrategyState.OPEN, Decimal("0.1"), Decimal("0.1")),
    )
    path = tmp_path / "trace.jsonl"
    trace.write_jsonl(path)

    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [row["sequence"] for row in rows] == [1, 2]
    assert all(row["schema_version"] == 1 for row in rows)
    assert rows[-1] == {
        "actual_qty": "0.1",
        "event": "trigger_accepted",
        "protected_qty": "0.1",
        "schema_version": 1,
        "sequence": 2,
        "state": "OPEN",
    }
