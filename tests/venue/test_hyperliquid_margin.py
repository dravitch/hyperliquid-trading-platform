from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from hltrader.risk.guard import MarginMode
from hltrader.risk.margin_verification import (
    VerificationStatus,
    load_margin_verification,
    save_margin_verification,
)
from hltrader.venue.hyperliquid_margin import (
    ClearinghouseParseError,
    MarginVerificationRequest,
    ObservedMarginState,
    classify_margin_evidence,
    parse_clearinghouse_state,
    verify_margin_state,
)

NOW = datetime(2026, 9, 1, 12, tzinfo=UTC)
ACCOUNT = "0x1111111111111111111111111111111111111111"
INSTRUMENT = "BTC-USD-PERP.HYPERLIQUID"


def request(**overrides) -> MarginVerificationRequest:
    values = {
        "account_address": ACCOUNT,
        "environment": "testnet",
        "instrument_id": INSTRUMENT,
        "coin": "BTC",
        "expected_margin_mode": MarginMode.ISOLATED,
        "expected_leverage": Decimal(3),
    }
    values.update(overrides)
    return MarginVerificationRequest(**values)


def payload(*, mode="isolated", leverage=3, size="-0.006", coin="BTC"):
    return {
        "marginSummary": {"accountValue": "100"},
        "assetPositions": [
            {
                "type": "oneWay",
                "position": {
                    "coin": coin,
                    "szi": size,
                    "leverage": {"type": mode, "value": leverage},
                },
            }
        ],
        "time": 1788264000000,
    }


def observation(body=None, **overrides) -> ObservedMarginState:
    values = {
        "payload": payload() if body is None else body,
        "account_address": ACCOUNT,
        "environment": "testnet",
        "instrument_id": INSTRUMENT,
        "coin": "BTC",
        "observed_at": NOW,
    }
    values.update(overrides)
    return parse_clearinghouse_state(**values)


def test_observable_matching_position_is_verified() -> None:
    receipt = classify_margin_evidence(request(), observation(), now=NOW)
    assert receipt.status is VerificationStatus.VERIFIED
    assert receipt.observed_margin_mode is MarginMode.ISOLATED
    assert receipt.observed_leverage == Decimal(3)
    assert receipt.observed_position_qty == Decimal("-0.006")


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (payload(leverage=5), "leverage"),
        (payload(mode="cross"), "margin mode"),
    ],
)
def test_observable_difference_is_mismatch(body, reason) -> None:
    receipt = classify_margin_evidence(request(), observation(body), now=NOW)
    assert receipt.status is VerificationStatus.MISMATCH
    assert reason in receipt.reason


def test_no_relevant_position_is_unverifiable() -> None:
    receipt = classify_margin_evidence(
        request(), observation({"assetPositions": []}), now=NOW
    )
    assert receipt.status is VerificationStatus.UNVERIFIABLE
    assert receipt.observed_margin_mode is None
    assert receipt.observed_leverage is None


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"assetPositions": [{"position": {"coin": "BTC", "szi": "-0.006"}}]},
        {"assetPositions": "unexpected"},
    ],
)
def test_missing_or_invalid_required_field_is_typed_parse_failure(body) -> None:
    with pytest.raises(ClearinghouseParseError):
        observation(body)


def test_parse_failure_becomes_unverifiable_at_verifier_boundary() -> None:
    receipt = verify_margin_state(request(), now=NOW, fetcher=lambda **_: {})
    assert receipt.status is VerificationStatus.UNVERIFIABLE
    assert "ClearinghouseParseError" in receipt.reason


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"account_address": "0x2222222222222222222222222222222222222222"}, "account"),
        ({"environment": "mainnet"}, "environment"),
        ({"instrument_id": "ETH-USD-PERP.HYPERLIQUID"}, "instrument"),
    ],
)
def test_wrong_bound_context_is_never_verified(overrides, reason) -> None:
    receipt = classify_margin_evidence(request(), observation(**overrides), now=NOW)
    assert receipt.status is VerificationStatus.MISMATCH
    assert reason in receipt.reason


def test_request_rejects_instrument_coin_alias_guessing() -> None:
    with pytest.raises(ValueError, match="map exactly"):
        request(coin="ETH")


def test_stale_observation_is_unverifiable() -> None:
    stale = observation(observed_at=NOW - timedelta(seconds=301))
    receipt = classify_margin_evidence(request(), stale, now=NOW)
    assert receipt.status is VerificationStatus.UNVERIFIABLE


@pytest.mark.parametrize("value", ["NaN", "Infinity", "3.5", "0", -1])
def test_unexpected_numeric_leverage_fails_closed(value) -> None:
    with pytest.raises(ClearinghouseParseError):
        observation(payload(leverage=value))


def test_two_identical_evaluations_are_deterministic() -> None:
    first = classify_margin_evidence(request(), observation(), now=NOW)
    second = classify_margin_evidence(request(), observation(), now=NOW)
    assert first == second


def test_transport_error_becomes_unverifiable() -> None:
    def fail(**_):
        raise OSError("offline")

    receipt = verify_margin_state(request(), now=NOW, fetcher=fail)
    assert receipt.status is VerificationStatus.UNVERIFIABLE
    assert "offline" in receipt.reason


def test_structured_receipt_round_trip_is_strategy_consumable(tmp_path) -> None:
    receipt = classify_margin_evidence(request(), observation(), now=NOW)
    path = tmp_path / "margin-receipt.json"
    save_margin_verification(path, receipt)
    loaded = load_margin_verification(path)
    assert loaded == receipt
    assert loaded.matches(
        account_address=ACCOUNT,
        environment="testnet",
        instrument_id=INSTRUMENT,
        margin_mode=MarginMode.ISOLATED,
        leverage=Decimal(3),
        now=NOW,
        max_age_seconds=300,
    )
