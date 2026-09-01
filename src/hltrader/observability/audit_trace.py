from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path

from hltrader.domain.state_machine import StrategySnapshot


@dataclass(frozen=True, slots=True)
class TraceEvent:
    schema_version: int
    sequence: int
    event: str
    state: str
    actual_qty: str
    protected_qty: str


class AuditTrace:
    SCHEMA_VERSION = 1

    def __init__(self) -> None:
        self._events: list[TraceEvent] = []

    @property
    def events(self) -> tuple[TraceEvent, ...]:
        return tuple(self._events)

    def record(
        self,
        event: str,
        snapshot: StrategySnapshot,
        *,
        actual_qty: Decimal | None = None,
        protected_qty: Decimal | None = None,
    ) -> TraceEvent:
        item = TraceEvent(
            schema_version=self.SCHEMA_VERSION,
            sequence=len(self._events) + 1,
            event=event,
            state=snapshot.state.value,
            actual_qty=str(snapshot.actual_net_position_qty if actual_qty is None else actual_qty),
            protected_qty=str(snapshot.protected_qty if protected_qty is None else protected_qty),
        )
        self._events.append(item)
        return item

    def write_jsonl(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = "".join(json.dumps(asdict(item), sort_keys=True) + "\n" for item in self._events)
        path.write_text(payload, encoding="utf-8")
