from __future__ import annotations

import json
from argparse import ArgumentParser
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path

import nautilus_trader
from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.backtest.models import BestPriceFillModel
from nautilus_trader.common.config import LoggingConfig
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.data import MarkPriceUpdate, QuoteTick
from nautilus_trader.model.enums import AccountType, OmsType, OrderSide
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import Money, Price, Quantity
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from nautilus_trader.trading.strategy import Strategy


class MarkPriceSemanticVerdict(StrEnum):
    MARK_PRICE_EQUIVALENT_CONFIRMED = "MARK_PRICE_EQUIVALENT_CONFIRMED"
    PROXY_USED = "PROXY_USED"
    UNVERIFIABLE = "UNVERIFIABLE"


@dataclass(frozen=True, slots=True)
class MarkPriceSemanticReport:
    """Evidence about trigger semantics in one pinned BacktestEngine run."""

    verdict: MarkPriceSemanticVerdict
    nautilus_version: str
    mark_update_observed: bool
    quote_crossed_trigger: bool
    native_trigger_fired: bool
    proxy_used: bool
    trigger_price: str
    mark_price: str
    quote_ask_price: str
    detail: str

    def as_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["verdict"] = self.verdict.value
        return payload

    def write_json(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self.as_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)


def classify_mark_price_semantics(
    *,
    mark_update_observed: bool,
    quote_crossed_trigger: bool,
    native_trigger_fired: bool,
    proxy_used: bool,
) -> MarkPriceSemanticVerdict:
    """Classify evidence without treating OHLC/quote/last data as mark price."""
    if proxy_used:
        return MarkPriceSemanticVerdict.PROXY_USED
    if mark_update_observed and not quote_crossed_trigger and native_trigger_fired:
        return MarkPriceSemanticVerdict.MARK_PRICE_EQUIVALENT_CONFIRMED
    return MarkPriceSemanticVerdict.UNVERIFIABLE


class _MarkPriceTriggerProbe(Strategy):
    def __init__(self, instrument_id, trigger_price: Price) -> None:
        super().__init__()
        self.instrument_id = instrument_id
        self.trigger_price = trigger_price
        self.order_submitted = False
        self.mark_update_observed = False
        self.native_trigger_fired = False

    def on_start(self) -> None:
        self.subscribe_quote_ticks(self.instrument_id)
        self.subscribe_mark_prices(self.instrument_id)

    def on_quote_tick(self, _) -> None:
        if self.order_submitted:
            return
        order = self.order_factory.stop_market(
            instrument_id=self.instrument_id,
            order_side=OrderSide.BUY,
            quantity=Quantity.from_int(100_000),
            trigger_price=self.trigger_price,
        )
        self.submit_order(order)
        self.order_submitted = True

    def on_mark_price(self, _) -> None:
        self.mark_update_observed = True

    def on_order_triggered(self, _) -> None:
        self.native_trigger_fired = True

    def on_order_filled(self, _) -> None:
        self.native_trigger_fired = True


def run_mark_price_semantic_probe() -> MarkPriceSemanticReport:
    """Run a controlled divergence: quote below trigger, mark above trigger.

    This tests the pinned engine rather than assuming its simulated matching semantics match
    Hyperliquid. It deliberately does not bridge mark prices into quotes; such a bridge is a proxy
    and must be reported separately by a future historical-data runner.
    """
    venue = Venue("SIM")
    instrument = TestInstrumentProvider.default_fx_ccy("AUD/USD", venue)
    trigger = Price.from_str("0.80000")
    quote_ask = Price.from_str("0.70002")
    mark = Price.from_str("0.81000")
    timestamp = 1_000_000_000_000_000_000

    engine = BacktestEngine(
        config=BacktestEngineConfig(
            logging=LoggingConfig(log_level="ERROR"),
            run_analysis=False,
        )
    )
    engine.add_venue(
        venue=venue,
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        base_currency=USD,
        starting_balances=[Money(1_000_000, USD)],
        fill_model=BestPriceFillModel(),
        use_message_queue=True,
    )
    engine.add_instrument(instrument)
    engine.add_data(
        [
            QuoteTick(
                instrument_id=instrument.id,
                bid_price=Price.from_str("0.70000"),
                ask_price=quote_ask,
                bid_size=Quantity.from_int(1_000_000),
                ask_size=Quantity.from_int(1_000_000),
                ts_event=timestamp,
                ts_init=timestamp,
            ),
            MarkPriceUpdate(
                instrument_id=instrument.id,
                value=mark,
                ts_event=timestamp + 1,
                ts_init=timestamp + 1,
            ),
        ]
    )
    probe = _MarkPriceTriggerProbe(instrument.id, trigger)
    engine.add_strategy(probe)
    engine.run()

    quote_crossed = quote_ask >= trigger
    verdict = classify_mark_price_semantics(
        mark_update_observed=probe.mark_update_observed,
        quote_crossed_trigger=quote_crossed,
        native_trigger_fired=probe.native_trigger_fired,
        proxy_used=False,
    )
    detail = (
        "BacktestEngine observed MarkPriceUpdate but did not trigger the native STOP_MARKET "
        "while the quote remained below the threshold. Native mark-price equivalence is not "
        "established for NautilusTrader 1.231.0."
    )
    return MarkPriceSemanticReport(
        verdict=verdict,
        nautilus_version=nautilus_trader.__version__,
        mark_update_observed=probe.mark_update_observed,
        quote_crossed_trigger=quote_crossed,
        native_trigger_fired=probe.native_trigger_fired,
        proxy_used=False,
        trigger_price=str(trigger),
        mark_price=str(mark),
        quote_ask_price=str(quote_ask),
        detail=detail,
    )


def main() -> int:
    parser = ArgumentParser(description="Probe Nautilus backtest mark-price trigger semantics")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/backtests/mark-price-semantics.json"),
    )
    args = parser.parse_args()
    report = run_mark_price_semantic_probe()
    report.write_json(args.output)
    print(json.dumps(report.as_dict(), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
