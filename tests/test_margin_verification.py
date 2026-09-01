import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from hltrader.risk.guard import MarginMode
from hltrader.risk.margin_verification import load_margin_verification


def write_receipt(path: Path, observed_at: datetime) -> None:
    path.write_text(
        json.dumps(
            {
                "account_address": "0xABC",
                "environment": "testnet",
                "instrument_id": "BTC-USD-PERP.HYPERLIQUID",
                "margin_mode": "isolated",
                "leverage": "3",
                "observed_at": observed_at.isoformat(),
                "source": "hyperliquid_api",
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
