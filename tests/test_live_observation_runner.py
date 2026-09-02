from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from nautilus_trader.core.nautilus_pyo3 import HyperliquidEnvironment

from hltrader.domain.state_machine import StrategyState
from hltrader.runners.live import (
    LiveObservationError,
    RuntimeMode,
    build_execution_client_config,
    build_strategy_import,
    build_trading_node,
    build_trading_node_config,
    load_live_observation_settings,
)

STRATEGY_CONFIG = Path("config/strategies/short_btc_rsi.yaml")
VENUE_CONFIG = Path("config/venues/hyperliquid.yaml")


def settings():
    return load_live_observation_settings(STRATEGY_CONFIG, VENUE_CONFIG)


def test_configuration_loads_existing_business_and_runtime_values() -> None:
    value = settings()

    assert value.environment == "testnet"
    assert str(value.instrument_id) == "BTC-USD-PERP.HYPERLIQUID"
    assert str(value.bar_type) == "BTC-USD-PERP.HYPERLIQUID-1-DAY-LAST-EXTERNAL"
    assert str(value.strategy.notional_usdc) == "300"
    assert value.strategy.rsi_period == 14
    assert value.strategy.rsi_warmup_bars == 30
    assert str(value.strategy.rsi_threshold) == "20"
    assert str(value.strategy.price_level) == "60000"
    assert value.strategy.desired_margin_mode.value == "isolated"
    assert str(value.strategy.desired_leverage) == "3"
    assert value.journal_path == Path("var/run/short_btc_rsi.json")
    assert value.audit_path == Path("var/log/short_btc_rsi.audit.jsonl")
    assert value.mode is RuntimeMode.DATA_ONLY
    assert value.enable_order_submission is False


def test_node_config_selects_testnet_data_only() -> None:
    config = build_trading_node_config(settings(), {})

    data = config.data_clients["HYPERLIQUID"]
    assert data.environment == HyperliquidEnvironment.TESTNET
    assert config.exec_clients == {}
    assert len(config.strategies) == 1
    assert config.strategies[0].config["enable_order_submission"] is False


def test_mainnet_is_rejected_even_if_mainnet_lock_is_true() -> None:
    value = replace(settings(), environment="mainnet")

    with pytest.raises(LiveObservationError, match="mainnet is unsupported"):
        build_trading_node_config(value, {"HLTRADER_MAINNET_ENABLED": "true"})


def test_order_submission_cannot_be_enabled() -> None:
    value = replace(settings(), enable_order_submission=True)

    with pytest.raises(LiveObservationError, match="order submission"):
        build_trading_node_config(value, {})


def test_secret_presence_cannot_enable_execution() -> None:
    value = settings()

    with pytest.raises(LiveObservationError, match="must be absent"):
        build_trading_node_config(value, {"HYPERLIQUID_TESTNET_PK": "not-a-real-key"})
    with pytest.raises(LiveObservationError, match="execution client is blocked"):
        build_execution_client_config(value, {})


def test_existing_journal_is_blocked_without_private_venue_reconciliation(tmp_path: Path) -> None:
    journal = tmp_path / "journal.json"
    journal.write_text("{}", encoding="utf-8")
    value = replace(settings(), journal_path=journal)

    with pytest.raises(LiveObservationError, match="cannot reconcile"):
        build_trading_node_config(value, {})


def test_strategy_builder_preserves_disabled_submission() -> None:
    strategy = build_strategy_import(settings())

    assert strategy.strategy_path.endswith(":ShortBtcRsiStrategy")
    assert strategy.config_path.endswith(":ShortBtcRsiConfig")
    assert strategy.config["enable_order_submission"] is False
    assert strategy.config["environment"] == "testnet"


def test_trading_node_builds_locally_with_strategy_and_no_exec_client(tmp_path: Path) -> None:
    value = replace(settings(), journal_path=tmp_path / "missing.json")
    node = build_trading_node(value, {})
    try:
        strategies = node.trader.strategies()
        assert node.is_built()
        assert len(strategies) == 1
        strategy = strategies[0]
        assert strategy.config.enable_order_submission is False
        assert strategy._machine.snapshot.state is StrategyState.NEVER_ENTERED
    finally:
        node.dispose()
