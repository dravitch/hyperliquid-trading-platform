from pathlib import Path

import pytest

from hltrader.domain.state_machine import StrategyState
from hltrader.persistence.run_journal import JournalError, RunJournal, RunRecord


def test_journal_round_trip(tmp_path: Path) -> None:
    journal = RunJournal(tmp_path / "run.json")
    record = RunRecord(
        "run-1",
        StrategyState.CLOSED_FINAL,
        "entry-1",
        "exit-1",
        "rsi_exit",
        "protection-1",
    )
    journal.save(record)
    assert journal.load() == record


def test_corrupt_journal_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "run.json"
    path.write_text("not-json", encoding="utf-8")
    with pytest.raises(JournalError):
        RunJournal(path).load()
