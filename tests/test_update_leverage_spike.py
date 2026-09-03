from decimal import Decimal

import pytest

from hltrader.risk.bootstrap_margin import BootstrapStatus
from hltrader.risk.guard import MarginMode
from hltrader.runners.update_leverage_spike import (
    MUTATION_GUARD_ENV,
    SpikeConfigurationError,
    build_plan,
    command_from_plan,
    execute_plan,
    expectation_from_plan,
    require_mutation_guard,
)

ACCOUNT = "0x1111111111111111111111111111111111111111"
SIGNER = "0x2222222222222222222222222222222222222222"


def plan(*, execute: bool = False):
    return build_plan(
        account_address=ACCOUNT,
        signer_address=SIGNER,
        asset=0,
        leverage=3,
        execute=execute,
        session_id="session-1",
    )


def test_plan_is_testnet_isolated_btc_and_dry_run_by_default() -> None:
    value = plan()
    assert value.environment == "testnet"
    assert value.instrument_id == "BTC-USD-PERP.HYPERLIQUID"
    assert value.coin == "BTC"
    assert value.margin_mode is MarginMode.ISOLATED
    assert value.leverage == 3
    assert value.execute is False
    assert value.action() == {
        "type": "updateLeverage",
        "asset": 0,
        "isCross": False,
        "leverage": 3,
    }


def test_execute_requires_second_explicit_guard() -> None:
    with pytest.raises(SpikeConfigurationError, match=MUTATION_GUARD_ENV):
        require_mutation_guard(execute=True, guard_value=None)
    with pytest.raises(SpikeConfigurationError, match=MUTATION_GUARD_ENV):
        require_mutation_guard(execute=True, guard_value="false")
    require_mutation_guard(execute=True, guard_value="true")


def test_dry_run_never_requires_mutation_guard() -> None:
    require_mutation_guard(execute=False, guard_value=None)


def test_execute_plan_refuses_dry_run_plan_without_calling_submitter() -> None:
    calls = []

    def submit(*args):
        calls.append(args)
        return {"status": "ok", "response": {"type": "default"}}

    with pytest.raises(SpikeConfigurationError, match="plan.execute is false"):
        execute_plan(
            plan(),
            nonce=123,
            observed_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
            submit=submit,
        )
    assert calls == []


def test_exact_success_becomes_configured_receipt() -> None:
    value = plan(execute=True)
    captured = []

    def submit(action, nonce):
        captured.append((action, nonce))
        return {"status": "ok", "response": {"type": "default"}}

    observed_at = __import__("datetime").datetime.now(__import__("datetime").UTC)
    receipt = execute_plan(value, nonce=456, observed_at=observed_at, submit=submit)

    assert captured == [(value.action(), 456)]
    assert receipt.status is BootstrapStatus.CONFIGURED
    assert receipt.nonce == 456
    assert receipt.response_type == "default"
    assert receipt.expected_leverage == Decimal(3)


def test_ambiguous_transport_failure_is_unverifiable_and_not_retried() -> None:
    value = plan(execute=True)
    calls = []

    def submit(action, nonce):
        calls.append((action, nonce))
        raise TimeoutError("lost response")

    observed_at = __import__("datetime").datetime.now(__import__("datetime").UTC)
    receipt = execute_plan(value, nonce=789, observed_at=observed_at, submit=submit)

    assert len(calls) == 1
    assert receipt.status is BootstrapStatus.UNVERIFIABLE
    assert "ambiguous updateLeverage outcome" in receipt.reason


def test_command_and_expectation_bind_same_exact_target() -> None:
    value = plan(execute=True)
    expectation = expectation_from_plan(value)
    command = command_from_plan(value, nonce=999)

    assert expectation.account_address == command.account_address
    assert expectation.signer_address == command.signer_address
    assert expectation.environment == command.environment == "testnet"
    assert expectation.instrument_id == command.instrument_id
    assert expectation.coin == command.coin == "BTC"
    assert expectation.asset == command.asset == 0
    assert command.is_cross is False
    assert command.leverage == 3
