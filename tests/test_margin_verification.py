import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from hltrader.risk.guard import MarginMode
from hltrader.risk.margin_verification import VerificationStatus, load_margin_verification


def write_receipt(
    path: Path,
    observed_at: datetime,
    status: VerificationStatus = VerificationStatus.VERIFIED,
) -> None:
    path.write_text(
        json.dumps(
            {
                "account_address": "0xABC",
                "environment": "testnet",
                "instrument_id": "BTC-USD-PERP.HYPERLIQUID",
                "status": status.value,
                "expected_margin_mode": "isolated",
                "expected_leverage": "3",
                "observed_margin_mode": "isolated",
                "observed_leverage": "3",
                "observed_position_qty": "-0.006",
                "observed_at": observed_at.isoformat(),
                "evidence_source": "hyperliquid_clearinghouseState",
                "reason": "position margin mode and leverage match exactly",
            }
        ),
        encoding="utf-8",
    )


def test_matching_fresh_receipt_is_required(tmp_path: Path) -> None:
    now = datetime(2026, 9, 1, tzinfo=UTC)
    path = tmp_path / "margin.json"
    write_receipt(path, now - timedelta(seconds=30))
    receipt = load_margin_verification(path)

    assert receipt.matches(
        account_address="0xabc",
        environment="testnet",
        instrument_id="BTC-USD-PERP.HYPERLIQUID",
        margin_mode=MarginMode.ISOLATED,
        leverage=Decimal(3),
        now=now,
        max_age_seconds=300,
    )


def test_stale_or_wrong_account_receipt_fails_closed(tmp_path: Path) -> None:
    now = datetime(2026, 9, 1, tzinfo=UTC)
    path = tmp_path / "margin.json"
    write_receipt(path, now - timedelta(seconds=301))
    receipt = load_margin_verification(path)

    common = {
        "environment": "testnet",
        "instrument_id": "BTC-USD-PERP.HYPERLIQUID",
        "margin_mode": MarginMode.ISOLATED,
        "leverage": Decimal(3),
        "now": now,
        "max_age_seconds": 300,
    }
    assert not receipt.matches(account_address="0xabc", **common)
    write_receipt(path, now)
    assert not load_margin_verification(path).matches(account_address="0xdef", **common)


def test_mismatch_and_unverifiable_receipts_both_block_entry(tmp_path: Path) -> None:
    now = datetime(2026, 9, 1, tzinfo=UTC)
    path = tmp_path / "margin.json"
    common = {
        "account_address": "0xabc",
        "environment": "testnet",
        "instrument_id": "BTC-USD-PERP.HYPERLIQUID",
        "margin_mode": MarginMode.ISOLATED,
        "leverage": Decimal(3),
        "now": now,
        "max_age_seconds": 300,
    }
    for status in (VerificationStatus.MISMATCH, VerificationStatus.UNVERIFIABLE):
        write_receipt(path, now, status)
        assert not load_margin_verification(path).matches(**common)
