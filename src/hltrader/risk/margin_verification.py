from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from .guard import MarginMode


class MarginVerificationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class MarginVerificationReceipt:
    """Auditable observation produced by a venue-query runner.

    This receipt is not a cryptographic venue signature. It prevents a bare boolean from being
    treated as evidence and binds the observation to account, network, instrument and recency.
    """

    account_address: str
    environment: str
    instrument_id: str
    margin_mode: MarginMode
    leverage: Decimal
    observed_at: datetime
    source: str

    def matches(
        self,
        *,
        account_address: str,
        environment: str,
        instrument_id: str,
        margin_mode: MarginMode,
        leverage: Decimal,
        now: datetime,
        max_age_seconds: int,
    ) -> bool:
        age = now.astimezone(UTC) - self.observed_at.astimezone(UTC)
        return (
            self.source == "hyperliquid_api"
            and self.account_address.lower() == account_address.lower()
            and self.environment == environment
            and self.instrument_id == instrument_id
            and self.margin_mode is margin_mode
            and self.leverage == leverage
            and 0 <= age.total_seconds() <= max_age_seconds
        )


def load_margin_verification(path: Path) -> MarginVerificationReceipt:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        observed_at = datetime.fromisoformat(value["observed_at"])
        if observed_at.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")
        return MarginVerificationReceipt(
            account_address=str(value["account_address"]),
            environment=str(value["environment"]),
            instrument_id=str(value["instrument_id"]),
            margin_mode=MarginMode(value["margin_mode"]),
            leverage=Decimal(str(value["leverage"])),
            observed_at=observed_at,
            source=str(value["source"]),
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise MarginVerificationError(f"invalid margin verification receipt: {path}") from exc
