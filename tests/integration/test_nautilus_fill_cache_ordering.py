from decimal import Decimal

from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.backtest.models import BestPriceFillModel
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.enums import AccountType, OmsType, OrderSide
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import Money, Price, Quantity
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from nautilus_trader.trading.strategy import Strategy

from hltrader.domain.state_machine import StrategySnapshot, StrategyState
from hltrader.observability.audit_trace import AuditTrace

INSTRUMENT = TestInstrumentProvider.default_fx_ccy("AUD/USD", Venue("SIM"))


class CacheOrderingProbe(Strategy):
    def __init__(self) -> None:
        super().__init__()
        self.submitted = False
        self.actual_qty_seen_on_fill = Decimal(0)
        self.trace = AuditTrace()

    def on_start(self) -> None:
        self.subscribe_quote_ticks(INSTRUMENT.id)

    def on_quote_tick(self, tick: QuoteTick) -> None:
        if self.submitted:
            return
        order = self.order_factory.market(
            instrument_id=INSTRUMENT.id,
            order_side=OrderSide.SELL,
            quantity=Quantity.from_int(100_000),
        )
        self.submit_order(order)
        self.submitted = True

    def on_order_filled(self, event) -> None:
        positions = self.cache.positions_open(instrument_id=INSTRUMENT.id)
        self.actual_qty_seen_on_fill = sum(
            (position.quantity.as_decimal() for position in positions if position.is_short),
            start=Decimal(0),
        )
        state = (
            StrategyState.PROTECTING if self.actual_qty_seen_on_fill > 0 else StrategyState.ENTERING
        )
        self.trace.record(
            "on_order_filled_cache_observation",
            StrategySnapshot(state),
            actual_qty=self.actual_qty_seen_on_fill,
        )


def test_backtest_engine_updates_position_cache_before_fill_callback(tmp_path) -> None:
    engine = BacktestEngine(config=BacktestEngineConfig())
    engine.add_venue(
        venue=Venue("SIM"),
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        base_currency=USD,
        starting_balances=[Money(1_000_000, USD)],
        fill_model=BestPriceFillModel(),
        use_message_queue=True,
    )
    engine.add_instrument(INSTRUMENT)
    engine.add_data(
        [
            QuoteTick(
                instrument_id=INSTRUMENT.id,
                bid_price=Price.from_str("0.70000"),
                ask_price=Price.from_str("0.70002"),
                bid_size=Quantity.from_int(1_000_000),
                ask_size=Quantity.from_int(1_000_000),
                ts_event=1_000_000_000_000_000_000,
                ts_init=1_000_000_000_000_000_000,
            )
        ]
    )
    strategy = CacheOrderingProbe()
    engine.add_strategy(strategy)
    engine.run()

    trace_path = tmp_path / "nautilus-cache-ordering.jsonl"
    strategy.trace.write_jsonl(trace_path)
    assert strategy.actual_qty_seen_on_fill == Decimal(100_000)
    assert strategy.trace.events[0].state == StrategyState.PROTECTING.value
    assert trace_path.read_text(encoding="utf-8").count("\n") == 1
