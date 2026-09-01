from decimal import Decimal

import nautilus_trader
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import OrderType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.strategy import Strategy

from hltrader.domain.exit_rules import PriceDirection
from hltrader.strategies.short_btc_rsi import (
    ShortBtcRsiConfig,
    ShortBtcRsiStrategy,
    protective_order_type,
)

INSTRUMENT_ID = InstrumentId.from_str("BTC-USD-PERP.HYPERLIQUID")
BAR_TYPE = BarType.from_str("BTC-USD-PERP.HYPERLIQUID-1-DAY-LAST-EXTERNAL")


def make_config(**overrides) -> ShortBtcRsiConfig:
    values = {
        "instrument_id": INSTRUMENT_ID,
        "bar_type": BAR_TYPE,
    }
    values.update(overrides)
    return ShortBtcRsiConfig(**values)


def test_adapter_is_pinned_to_installed_nautilus_version() -> None:
    strategy = ShortBtcRsiStrategy(make_config())
    assert isinstance(strategy, Strategy)
    assert nautilus_trader.__version__ == strategy.NAUTILUS_VERSION == "1.231.0"


def test_adapter_is_fail_closed_by_default() -> None:
    config = make_config()
    assert config.enable_order_submission is False
    assert config.margin_verification_path is None
    assert config.account_address == ""
    assert config.notional_usdc == Decimal(300)


def test_normal_tpsl_is_explicitly_not_treated_as_atomic() -> None:
    assert ShortBtcRsiStrategy.NORMAL_TPSL_IS_ATOMIC is False


def test_above_short_exit_uses_native_stop_market() -> None:
    assert protective_order_type(PriceDirection.ABOVE) is OrderType.STOP_MARKET


def test_below_short_exit_uses_native_market_if_touched() -> None:
    assert protective_order_type(PriceDirection.BELOW) is OrderType.MARKET_IF_TOUCHED
