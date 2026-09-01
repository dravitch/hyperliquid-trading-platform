from pathlib import Path

from hltrader.config import load_strategy_config
from hltrader.domain.exit_rules import PriceDirection
from hltrader.risk.guard import MarginMode


def test_sample_configuration_is_safe_and_loadable() -> None:
    config = load_strategy_config(Path("config/strategies/short_btc_rsi.yaml"))
    assert config.deployment_enabled is False
    assert config.price_direction is PriceDirection.ABOVE
    assert config.desired_margin_mode is MarginMode.ISOLATED
