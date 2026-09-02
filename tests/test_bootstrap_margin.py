from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from hltrader.risk.bootstrap_margin import (
    BootstrapExpectation,
    BootstrapStatus,
    UpdateLeverageCommand,
    classify_bootstrap_response,
    consume_bootstrap_receipt,
    load_bootstrap_receipt,
    perform_bootstrap_attempt,
    save_bootstrap_receipt,
)
from hltrader.risk.guard import MarginMode

NOW = datetime(2026, 9, 2, 12, tzinfo=UTC)
ACCOUNT = "0x1111111111111111111111111111111111111111"
SIGNER = "0x2222222222222222222222222222222222222222"


def expectation(**overrides) -> BootstrapExpectation:
    values = {
        "session_id": "session-1",
        "account_address": ACCOUNT,
        "signer_address": SIGNER,
        "environment": "testnet",
        "instrument_id": "BTC-USD-PERP.HYPERLIQUID",
        "coin": "BTC",
        "asset": 0,
        "margin_mode": MarginMode.ISOLATED,
        "leverage": Decimal(3),
    }
    values.update(overrides)
    return BootstrapExpectation(**values)


def command(**overrides) -> UpdateLeverageCommand:
    values = {
        "session_id": "session-1",
        "account_address": ACCOUNT,
        "signer_address": SIGNER,
        "environment": "testnet",
        "instrument_id": "BTC-USD-PERP.HYPERLIQUID",
        "coin": "BTC",
        "asset": 0,
        "is_cross": False,
        "leverage": 3,
        "nonce": 1788346800000,
    }
    values.update(overrides)
    return UpdateLeverageCommand(**values)


def success_receipt(**command_overrides):
    return classify_bootstrap_response(
        expectation(),
        command(**command_overrides),
        {"status": "ok", "response": {"type": "default"}},
        observed_at=NOW,
    )


def test_exact_committed_configuration_is_configured() -> None:
    receipt = success_receipt()
    assert receipt.status is BootstrapStatus.CONFIGURED
    assert receipt.action_is_cross is False
    assert receipt.action_leverage == 3
    assert receipt.response_type == "default"
    assert receipt.matches(expectation(), now=NOW, max_age_seconds=30)


def test_update_leverage_wire_action_matches_official_contract() -> None:
    assert command().action() == {
        "type": "updateLeverage",
        "asset": 0,
        "isCross": False,
        "leverage": 3,
    }


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"leverage": 5}, "leverage"),
        ({"is_cross": True}, "margin mode"),
    ],
)
def test_wrong_leverage_or_margin_mode_is_mismatch(overrides, reason) -> None:
    receipt = success_receipt(**overrides)
    assert receipt.status is BootstrapStatus.MISMATCH
    assert reason in receipt.reason


def test_network_or_ambiguous_mutation_result_is_unverifiable() -> None:
    calls = []

    def timeout(action, nonce):
        calls.append((action, nonce))
        raise TimeoutError("response lost")

    receipt = perform_bootstrap_attempt(
        expectation(),
        command(),
        observed_at=NOW,
        submit=timeout,
    )
    assert receipt.status is BootstrapStatus.UNVERIFIABLE
    assert len(calls) == 1
    assert not receipt.matches(expectation(), now=NOW, max_age_seconds=30)


@pytest.mark.parametrize(
    "response",
    [
        None,
        {},
        {"status": 1},
        {"status": "ok"},
        {"status": "ok", "response": {"type": "unexpected"}},
        {"status": "ok", "response": {"type": "default", "unexpected": True}},
    ],
)
def test_malformed_or_unexpected_response_is_unverifiable(response) -> None:
    receipt = classify_bootstrap_response(
        expectation(), command(), response, observed_at=NOW
    )
    assert receipt.status is BootstrapStatus.UNVERIFIABLE


def test_authoritative_rejection_is_mismatch() -> None:
    receipt = classify_bootstrap_response(
        expectation(), command(), {"status": "err", "response": "bad leverage"}, observed_at=NOW
    )
    assert receipt.status is BootstrapStatus.MISMATCH


@pytest.mark.parametrize("delta", [timedelta(seconds=31), timedelta(seconds=-1)])
def test_stale_or_future_receipt_does_not_match(delta) -> None:
    receipt = replace(success_receipt(), observed_at=NOW - delta)
    assert not receipt.matches(expectation(), now=NOW, max_age_seconds=30)


@pytest.mark.parametrize(
    "overrides",
    [
        {"account_address": "0x3333333333333333333333333333333333333333"},
        {"environment": "mainnet"},
        {"instrument_id": "ETH-USD-PERP.HYPERLIQUID", "coin": "ETH", "asset": 1},
    ],
)
def test_account_environment_or_instrument_mismatch_never_configures(overrides) -> None:
    receipt = classify_bootstrap_response(
        expectation(), command(**overrides), {"status": "ok", "response": {"type": "default"}}, observed_at=NOW
    )
    assert receipt.status is BootstrapStatus.MISMATCH


def test_receipt_is_consumed_exactly_once_under_concurrency(tmp_path) -> None:
    path = tmp_path / "bootstrap.json"
    save_bootstrap_receipt(path, success_receipt())

    def consume(entry_id):
        return consume_bootstrap_receipt(
            path,
            expectation(),
            now=NOW,
            max_age_seconds=30,
            entry_id=entry_id,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(consume, ("entry-1", "entry-2")))
    assert sorted(results) == [False, True]
    consumed = load_bootstrap_receipt(path)
    assert consumed.consumed_at == NOW
    assert consumed.consumed_for_entry_id in {"entry-1", "entry-2"}


def test_restart_session_cannot_consume_prior_receipt(tmp_path) -> None:
    path = tmp_path / "bootstrap.json"
    save_bootstrap_receipt(path, success_receipt())
    restarted = expectation(session_id="session-after-restart")
    assert not consume_bootstrap_receipt(
        path,
        restarted,
        now=NOW,
        max_age_seconds=30,
        entry_id="entry-after-restart",
    )
    assert load_bootstrap_receipt(path).consumed_at is None


def test_duplicate_consumption_is_rejected(tmp_path) -> None:
    path = tmp_path / "bootstrap.json"
    save_bootstrap_receipt(path, success_receipt())
    assert consume_bootstrap_receipt(
        path, expectation(), now=NOW, max_age_seconds=30, entry_id="entry-1"
    )
    assert not consume_bootstrap_receipt(
        path, expectation(), now=NOW, max_age_seconds=30, entry_id="entry-2"
    )
