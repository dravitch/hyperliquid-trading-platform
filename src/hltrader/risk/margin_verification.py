from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path

from .guard import MarginMode


class MarginVerificationError(ValueError):
    pass


class VerificationStatus(StrEnum):
    VERIFIED = "VERIFIED"
    MISMATCH = "MISMATCH"
    UNVERIFIABLE = "UNVERIFIABLE"


@dataclass(frozen=True, slots=True)
class MarginVerificationReceipt:
    """Auditable observation produced by a venue-query runner.

    This receipt is not a cryptographic venue signature. It prevents a bare boolean from being
    treated as evidence and binds the observation to account, network, instrument and recency.
    """

    status: VerificationStatus
    account_address: str
    environment: str
    instrument_id: str
    expected_margin_mode: MarginMode
    expected_leverage: Decimal
    observed_margin_mode: MarginMode | None
    observed_leverage: Decimal | None
    observed_position_qty: Decimal | None
    evidence_source: str
    observed_at: datetime
    reason: str

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")
        decimals = (
            self.expected_leverage,
            self.observed_leverage,
            self.observed_position_qty,
        )
        if any(value is not None and not value.is_finite() for value in decimals):
            raise ValueError("receipt numeric values must be finite")
        if self.expected_leverage <= 0:
            raise ValueError("expected leverage must be positive")
        if self.status is VerificationStatus.VERIFIED and (
            self.observed_margin_mode is None
            or self.observed_leverage is None
            or self.observed_position_qty in {None, Decimal(0)}
        ):
            raise ValueError("VERIFIED receipt requires complete non-zero observed evidence")

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
            self.status is VerificationStatus.VERIFIED
            and self.evidence_source == "hyperliquid_clearinghouseState"
            and self.account_address.lower() == account_address.lower()
            and self.environment == environment
            and self.instrument_id == instrument_id
            and self.expected_margin_mode is margin_mode
            and self.expected_leverage == leverage
            and self.observed_margin_mode is margin_mode
            and self.observed_leverage == leverage
            and self.observed_position_qty is not None
            and self.observed_position_qty != 0
            and 0 <= age.total_seconds() <= max_age_seconds
        )


def load_margin_verification(path: Path) -> MarginVerificationReceipt:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        observed_at = datetime.fromisoformat(value["observed_at"])
        if observed_at.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")
        return MarginVerificationReceipt(
            status=VerificationStatus(value["status"]),
            account_address=str(value["account_address"]),
            environment=str(value["environment"]),
            instrument_id=str(value["instrument_id"]),
            expected_margin_mode=MarginMode(value["expected_margin_mode"]),
            expected_leverage=Decimal(str(value["expected_leverage"])),
            observed_margin_mode=(
                MarginMode(value["observed_margin_mode"])
                if value.get("observed_margin_mode") is not None
                else None
            ),
            observed_leverage=(
                Decimal(str(value["observed_leverage"]))
                if value.get("observed_leverage") is not None
                else None
            ),
            observed_position_qty=(
                Decimal(str(value["observed_position_qty"]))
                if value.get("observed_position_qty") is not None
                else None
            ),
            evidence_source=str(value["evidence_source"]),
            observed_at=observed_at,
            reason=str(value["reason"]),
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise MarginVerificationError(f"invalid margin verification receipt: {path}") from exc


def save_margin_verification(path: Path, receipt: MarginVerificationReceipt) -> None:
    payload = {
        "status": receipt.status.value,
        "account_address": receipt.account_address,
        "environment": receipt.environment,
        "instrument_id": receipt.instrument_id,
        "expected_margin_mode": receipt.expected_margin_mode.value,
        "expected_leverage": str(receipt.expected_leverage),
        "observed_margin_mode": (
            receipt.observed_margin_mode.value if receipt.observed_margin_mode is not None else None
        ),
        "observed_leverage": (
            str(receipt.observed_leverage) if receipt.observed_leverage is not None else None
        ),
        "observed_position_qty": (
            str(receipt.observed_position_qty)
            if receipt.observed_position_qty is not None
            else None
        ),
        "evidence_source": receipt.evidence_source,
        "observed_at": receipt.observed_at.isoformat(),
        "reason": receipt.reason,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
