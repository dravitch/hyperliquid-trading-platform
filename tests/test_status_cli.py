from __future__ import annotations

import json
from pathlib import Path

from hltrader.domain.state_machine import StrategyState
from hltrader.persistence.run_journal import RunJournal, RunRecord
from hltrader.runners.status import main, read_local_status


def test_status_handles_missing_journal_and_trace(tmp_path: Path) -> None:
    status = read_local_status(tmp_path / "missing.json", tmp_path / "missing.jsonl")

    assert status.state is StrategyState.NEVER_ENTERED
    assert status.position_qty == "-"
    assert status.protected_qty == "-"
    assert status.last_event == "-"
    assert status.recovery_required is False


def test_status_reports_recovery_required(tmp_path: Path) -> None:
    journal = tmp_path / "journal.json"
    audit = tmp_path / "audit.jsonl"
    RunJournal(journal).save(
        RunRecord(
            run_id="run-1",
            state=StrategyState.RECOVERY_REQUIRED,
            exit_reason="startup reconciliation ambiguous",
        )
    )
    audit.write_text(
        json.dumps(
            {
                "event": "reconciliation_failed",
                "state": "RECOVERY_REQUIRED",
                "actual_qty": "0.006",
                "protected_qty": "0.003",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    status = read_local_status(journal, audit)

    assert status.state is StrategyState.RECOVERY_REQUIRED
    assert status.position_qty == "0.006"
    assert status.protected_qty == "0.003"
    assert status.exit_reason == "startup reconciliation ambiguous"
    assert status.last_event == "reconciliation_failed"
    assert status.recovery_required is True


def test_status_read_is_byte_for_byte_read_only(tmp_path: Path) -> None:
    journal = tmp_path / "journal.json"
    audit = tmp_path / "audit.jsonl"
    RunJournal(journal).save(RunRecord(run_id="run-1", state=StrategyState.OPEN))
    audit.write_text(
        '{"event":"open","actual_qty":"0.006","protected_qty":"0.006"}\n',
        encoding="utf-8",
    )
    before = (journal.read_bytes(), audit.read_bytes())

    read_local_status(journal, audit)

    assert (journal.read_bytes(), audit.read_bytes()) == before


def test_status_cli_renders_compact_view(tmp_path: Path, monkeypatch, capsys) -> None:
    journal = tmp_path / "missing.json"
    audit = tmp_path / "missing.jsonl"
    monkeypatch.setattr(
        "sys.argv",
        ["hnt-status", "--journal", str(journal), "--audit", str(audit)],
    )

    main()

    assert capsys.readouterr().out == (
        "state: NEVER_ENTERED\n"
        "position_qty: -\n"
        "protected_qty: -\n"
        "exit_reason: -\n"
        "last_event: -\n"
        "recovery_required: false\n"
    )
