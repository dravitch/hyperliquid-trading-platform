from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from hltrader.domain.state_machine import StrategyState


class JournalError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RunRecord:
    run_id: str
    state: StrategyState
    entry_order: str | None = None
    exit_order: str | None = None
    exit_reason: str | None = None
    protective_order: str | None = None

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> RunRecord:
        try:
            return cls(
                run_id=str(value["run_id"]),
                state=StrategyState(value["state"]),
                entry_order=value.get("entry_order"),
                exit_order=value.get("exit_order"),
                exit_reason=value.get("exit_reason"),
                protective_order=value.get("protective_order"),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise JournalError("invalid run journal") from exc


class RunJournal:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> RunRecord | None:
        if not self.path.exists():
            return None
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise JournalError("cannot read run journal") from exc
        if not isinstance(value, dict):
            raise JournalError("invalid run journal")
        return RunRecord.from_dict(value)

    def save(self, record: RunRecord) -> None:
        """Atomically replace the journal and fsync both file and parent directory."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = asdict(record)
        payload["state"] = record.state.value
        temporary_path: Path | None = None
        try:
            with NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                json.dump(payload, temporary, indent=2, sort_keys=True)
                temporary.write("\n")
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_path, self.path)
            directory_fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError as exc:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            raise JournalError("cannot persist run journal") from exc
