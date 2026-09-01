from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import yaml

from hltrader.domain.exit_rules import PriceDirection
from hltrader.risk.guard import MarginMode


class ConfigError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class StrategyConfig:
    deployment_enabled: bool
    symbol: str
    notional_usdc: Decimal
    desired_margin_mode: MarginMode
    desired_leverage: Decimal
    max_notional_usdc: Decimal
    max_leverage: Decimal
    rsi_period: int
    rsi_warmup_bars: int
    rsi_threshold: Decimal
    price_level: Decimal
    price_direction: PriceDirection


def load_strategy_config(path: Path) -> StrategyConfig:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        desired = raw["desired_margin"]
        position = raw["position"]
        exit_config = raw["exit"]
        rsi = exit_config["rsi"]
        price = exit_config["price_target"]
        risk = raw["risk"]
        config = StrategyConfig(
            deployment_enabled=bool(raw["deployment_enabled"]),
            symbol=str(raw["symbol"]),
            notional_usdc=Decimal(str(position["notional_usdc"])),
            desired_margin_mode=MarginMode(desired["mode"]),
            desired_leverage=Decimal(str(desired["leverage"])),
            max_notional_usdc=Decimal(str(risk["max_notional_usdc"])),
            max_leverage=Decimal(str(risk["max_leverage"])),
            rsi_period=int(rsi["period"]),
            rsi_warmup_bars=int(rsi["warmup_bars"]),
            rsi_threshold=Decimal(str(rsi["threshold"])),
            price_level=Decimal(str(price["level"])),
            price_direction=PriceDirection(price["direction"]),
        )
    except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError) as exc:
        raise ConfigError(f"invalid strategy configuration: {path}") from exc

    if position.get("sizing_mode") != "fixed_notional":
        raise ConfigError("MVP only supports fixed_notional sizing")
    if exit_config.get("logic") != "FIRST_TRIGGER_WINS":
        raise ConfigError("MVP requires FIRST_TRIGGER_WINS exit arbitration")
    if not desired.get("verify_before_entry"):
        raise ConfigError("venue margin verification must be enabled")
    if config.rsi_warmup_bars < config.rsi_period:
        raise ConfigError("RSI warmup must be at least the indicator period")
    return config
