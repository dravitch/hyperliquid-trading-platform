from __future__ import annotations

import argparse
import os
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import yaml
from nautilus_trader.adapters.hyperliquid import (
    HyperliquidDataClientConfig,
    HyperliquidExecClientConfig,
    HyperliquidLiveDataClientFactory,
)
from nautilus_trader.adapters.hyperliquid.enums import HyperliquidProductType
from nautilus_trader.common.config import InstrumentProviderConfig
from nautilus_trader.config import TradingNodeConfig
from nautilus_trader.core.nautilus_pyo3 import HyperliquidEnvironment
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.data import BarType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.config import ImportableStrategyConfig

from hltrader.config import ConfigError, StrategyConfig, load_strategy_config


class LiveObservationError(RuntimeError):
    """Fail-closed configuration error for the observation-only runner."""


class RuntimeMode(StrEnum):
    DATA_ONLY = "DATA_ONLY"
    EXECUTION_CAPABLE = "EXECUTION_CAPABLE"


@dataclass(frozen=True, slots=True)
class LiveObservationSettings:
    environment: str
    venue: str
    instrument_id: InstrumentId
    bar_type: BarType
    journal_path: Path
    audit_path: Path
    enable_order_submission: bool
    strategy: StrategyConfig
    account_address_env: str
    private_key_env: str
    mode: RuntimeMode


def _mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise LiveObservationError(f"{label} must be a mapping")
    return value


def load_live_observation_settings(
    strategy_path: Path = Path("config/strategies/short_btc_rsi.yaml"),
    venue_path: Path = Path("config/venues/hyperliquid.yaml"),
) -> LiveObservationSettings:
    """Load the existing strategy and venue YAML without duplicating business parameters."""
    strategy = load_strategy_config(strategy_path)
    try:
        strategy_raw = _mapping(yaml.safe_load(strategy_path.read_text()), "strategy config")
        venue_raw = _mapping(yaml.safe_load(venue_path.read_text()), "venue config")
        exit_raw = _mapping(strategy_raw["exit"], "exit config")
        rsi_raw = _mapping(exit_raw["rsi"], "RSI config")
        runtime_raw = _mapping(venue_raw["observation_runtime"], "observation runtime")
        symbol = str(strategy_raw["symbol"])
        venue = str(venue_raw["venue"])
        instrument_id = InstrumentId.from_str(f"{symbol}.{venue}")
        interval = str(rsi_raw["bar_type"])
        bar_type = BarType.from_str(f"{instrument_id}-{interval}-LAST-EXTERNAL")
        settings = LiveObservationSettings(
            environment=str(venue_raw["network"]),
            venue=venue,
            instrument_id=instrument_id,
            bar_type=bar_type,
            journal_path=Path(str(runtime_raw["journal_path"])),
            audit_path=Path(str(runtime_raw["audit_path"])),
            enable_order_submission=bool(runtime_raw["enable_order_submission"]),
            strategy=strategy,
            account_address_env=str(venue_raw["account_address_env"]),
            private_key_env=str(venue_raw["private_key_env"]),
            mode=RuntimeMode(str(runtime_raw["mode"])),
        )
    except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError) as exc:
        raise LiveObservationError("invalid live observation configuration") from exc

    if settings.environment != "testnet":
        raise LiveObservationError("live observation runner is testnet-only; mainnet is unsupported")
    if settings.mode is not RuntimeMode.DATA_ONLY:
        raise LiveObservationError("EXECUTION_CAPABLE mode is blocked in the observation runner")
    if settings.enable_order_submission or strategy.deployment_enabled:
        raise LiveObservationError("order submission and deployment must both remain disabled")
    return settings


def build_strategy_import(settings: LiveObservationSettings) -> ImportableStrategyConfig:
    config = settings.strategy
    return ImportableStrategyConfig(
        strategy_path="hltrader.strategies.short_btc_rsi:ShortBtcRsiStrategy",
        config_path="hltrader.strategies.short_btc_rsi:ShortBtcRsiConfig",
        config={
            "instrument_id": str(settings.instrument_id),
            "bar_type": str(settings.bar_type),
            "journal_path": str(settings.journal_path),
            "notional_usdc": config.notional_usdc,
            "rsi_period": config.rsi_period,
            "rsi_warmup_bars": config.rsi_warmup_bars,
            "rsi_threshold": config.rsi_threshold,
            "price_level": config.price_level,
            "price_direction": config.price_direction,
            "enable_order_submission": False,
            "environment": "testnet",
            "desired_margin_mode": config.desired_margin_mode,
            "desired_leverage": config.desired_leverage,
        },
    )


def build_execution_client_config(
    settings: LiveObservationSettings,
    environ: Mapping[str, str] | None = None,
) -> HyperliquidExecClientConfig:
    """Describe the execution boundary while refusing to make it constructible in this runner."""
    del settings, environ
    raise LiveObservationError(
        "Hyperliquid execution client is blocked: it requires signing identity and can reconcile "
        "into order actions; use a future separately authorized runner"
    )


def build_trading_node_config(
    settings: LiveObservationSettings,
    environ: Mapping[str, str] | None = None,
) -> TradingNodeConfig:
    """Build a public testnet data-only node config with no execution client."""
    environment = os.environ if environ is None else environ
    if settings.environment != "testnet":
        raise LiveObservationError("mainnet is unsupported even when its environment lock is set")
    if settings.enable_order_submission:
        raise LiveObservationError("order submission must remain disabled")
    if environment.get(settings.private_key_env):
        raise LiveObservationError(
            f"{settings.private_key_env} must be absent from the observation-only process"
        )
    if settings.journal_path.exists():
        raise LiveObservationError(
            "data-only mode cannot reconcile an existing run journal against private venue state"
        )

    provider = InstrumentProviderConfig(load_ids=frozenset({settings.instrument_id}))
    data_config = HyperliquidDataClientConfig(
        instrument_provider=provider,
        product_types=(HyperliquidProductType.PERP,),
        environment=HyperliquidEnvironment.TESTNET,
    )
    return TradingNodeConfig(
        data_clients={settings.venue: data_config},
        exec_clients={},
        strategies=[build_strategy_import(settings)],
    )


def build_trading_node(
    settings: LiveObservationSettings,
    environ: Mapping[str, str] | None = None,
) -> TradingNode:
    """Instantiate and build Nautilus locally; no socket connects until ``run``."""
    node = TradingNode(config=build_trading_node_config(settings, environ))
    node.add_data_client_factory(settings.venue, HyperliquidLiveDataClientFactory)
    node.build()
    return node


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Hyperliquid testnet observation-only Nautilus runner (never submits orders)",
    )
    parser.add_argument(
        "--strategy-config",
        type=Path,
        default=Path("config/strategies/short_btc_rsi.yaml"),
    )
    parser.add_argument(
        "--venue-config",
        type=Path,
        default=Path("config/venues/hyperliquid.yaml"),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="build and dispose locally without opening the public testnet connection",
    )
    return parser


def main() -> None:
    args = _parser().parse_args()
    try:
        settings = load_live_observation_settings(args.strategy_config, args.venue_config)
    except ConfigError as exc:
        raise LiveObservationError(str(exc)) from exc
    node = build_trading_node(settings)
    if args.check:
        print("configuration wiring = PROVEN_LOCALLY")
        print("live websocket behavior = TO_PROVE_TESTNET")
        node.dispose()
        return
    try:
        node.run(raise_exception=True)
    finally:
        node.dispose()


if __name__ == "__main__":
    main()
