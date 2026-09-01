from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from nautilus_trader.indicators import RelativeStrengthIndex
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, OrderStatus, OrderType
from nautilus_trader.model.events import (
    OrderAccepted,
    OrderFilled,
    OrderRejected,
    OrderUpdated,
    PositionClosed,
)
from nautilus_trader.model.identifiers import ClientOrderId, InstrumentId
from nautilus_trader.model.objects import Price
from nautilus_trader.trading.config import StrategyConfig
from nautilus_trader.trading.strategy import Strategy

from hltrader.domain.exit_rules import ExitReason, PriceDirection, RsiExitRule
from hltrader.domain.reconciliation import ExchangeSnapshot, reconcile
from hltrader.domain.sizing import PositionSizing
from hltrader.domain.state_machine import InvalidTransition, StrategyState, StrategyStateMachine
from hltrader.indicators.rsi import RsiWarmupPolicy
from hltrader.persistence.run_journal import RunJournal, RunRecord


class ShortBtcRsiConfig(StrategyConfig, frozen=True):
    """Pinned Nautilus-facing configuration for the single-position MVP."""

    instrument_id: InstrumentId
    bar_type: BarType
    journal_path: str = "var/run/short_btc_rsi.json"
    notional_usdc: Decimal = Decimal(300)
    rsi_period: int = 14
    rsi_warmup_bars: int = 30
    rsi_threshold: Decimal = Decimal(20)
    price_level: Decimal = Decimal(60000)
    price_direction: PriceDirection = PriceDirection.ABOVE
    enable_order_submission: bool = False
    venue_margin_verified: bool = False
    protection_timeout_secs: int = 10


class ShortBtcRsiStrategy(Strategy):
    """Thin Nautilus orchestration over the framework-independent domain.

    NautilusTrader 1.231.0 calls its Hyperliquid bracket path ``NormalTpsl`` but stages
    child orders locally and submits them only after a parent fill. This strategy therefore
    manages one native reduce-only trigger explicitly and remains ``PROTECTING`` until the
    venue accepts coverage equal to the current net exposure.
    """

    NAUTILUS_VERSION = "1.231.0"
    NORMAL_TPSL_IS_ATOMIC = False
    PROTECTION_TIMER = "protective-trigger-confirmation"

    def __init__(self, config: ShortBtcRsiConfig) -> None:
        super().__init__(config)
        self._instrument = None
        self._rsi = RelativeStrengthIndex(config.rsi_period)
        self._warmup = RsiWarmupPolicy(config.rsi_period, config.rsi_warmup_bars)
        self._rsi_rule = RsiExitRule(config.rsi_threshold)
        self._sizing = PositionSizing(config.notional_usdc)
        self._journal = RunJournal(Path(config.journal_path))
        self._machine = StrategyStateMachine()
        self._historical_bars = 0
        self._entry_order_id = None
        self._protective_order_id = None
        self._exit_order_id = None

    def on_start(self) -> None:
        self._instrument = self.cache.instrument(self.config.instrument_id)
        if self._instrument is None:
            self.log.error(f"Instrument unavailable: {self.config.instrument_id}")
            self.stop()
            return

        if self.config.bar_type.instrument_id != self.config.instrument_id:
            self.log.error("Daily bar type does not match configured instrument")
            self.stop()
            return

        self.subscribe_quote_ticks(self.config.instrument_id)
        self._restore_state()
        if self._machine.snapshot.state in {
            StrategyState.STATE_CONFLICT,
            StrategyState.RECOVERY_REQUIRED,
        }:
            self.log.error(f"Startup reconciliation failed: {self._machine.snapshot.exit_reason}")
            return

        start = self.clock.utc_now() - timedelta(days=self.config.rsi_warmup_bars + 5)
        self.request_bars(
            self.config.bar_type,
            start=start,
            limit=self.config.rsi_warmup_bars,
            callback=self._on_warmup_complete,
        )

    def on_historical_data(self, data) -> None:
        if isinstance(data, Bar) and data.bar_type == self.config.bar_type:
            self._update_rsi(data)

    def on_bar(self, bar: Bar) -> None:
        if bar.bar_type != self.config.bar_type:
            return
        self._update_rsi(bar)
        if self._machine.snapshot.state is not StrategyState.OPEN or not self._rsi.initialized:
            return
        rsi_100 = Decimal(str(self._rsi.value * 100))
        if self._rsi_rule.evaluate(rsi_100).should_exit:
            self._request_close(ExitReason.RSI)

    def on_order_filled(self, event: OrderFilled) -> None:
        if event.instrument_id != self.config.instrument_id:
            return
        if self._entry_order_id is not None and event.client_order_id == self._entry_order_id:
            self._converge_protection()
            return
        if (
            self._protective_order_id is not None
            and event.client_order_id == self._protective_order_id
        ):
            self._machine.request_exit(ExitReason.PRICE)
            self._persist()

    def on_order_accepted(self, event: OrderAccepted) -> None:
        self._handle_protection_confirmation(event.client_order_id)

    def on_order_updated(self, event: OrderUpdated) -> None:
        self._handle_protection_confirmation(event.client_order_id)

    def on_order_rejected(self, event: OrderRejected) -> None:
        if self._protective_order_id is None or event.client_order_id != self._protective_order_id:
            return
        try:
            self._machine.protection_failed(f"protective trigger rejected: {event.reason}")
        except InvalidTransition:
            return
        self._persist()
        self._emergency_flatten()

    def on_position_closed(self, event: PositionClosed) -> None:
        if event.instrument_id != self.config.instrument_id:
            return
        try:
            self._machine.confirm_closed()
        except InvalidTransition:
            self._machine.mark_conflict("position closed outside an expected exit state")
        self._persist()

    def on_stop(self) -> None:
        self.unsubscribe_bars(self.config.bar_type)
        self.unsubscribe_quote_ticks(self.config.instrument_id)

    def _on_warmup_complete(self, _) -> None:
        self.subscribe_bars(self.config.bar_type)
        if not self._warmup.ready(self._historical_bars, self._rsi.initialized):
            self.log.error(
                f"RSI warm-up incomplete: bars={self._historical_bars}, "
                f"initialized={self._rsi.initialized}"
            )
            return
        self._maybe_enter()

    def _update_rsi(self, bar: Bar) -> None:
        self._rsi.handle_bar(bar)
        self._historical_bars += 1

    def _maybe_enter(self) -> None:
        if self._machine.snapshot.state is not StrategyState.NEVER_ENTERED:
            return
        if not self.config.enable_order_submission:
            self.log.warning("Order submission disabled by configuration")
            return
        if not self.config.venue_margin_verified:
            self.log.error("Entry blocked: venue margin mode and leverage are not verified")
            return

        quote = self.cache.quote_tick(self.config.instrument_id)
        if quote is None:
            self.log.error("Entry blocked: no quote available for market-order pricing")
            return
        price = Decimal(str(quote.bid_price.as_double()))
        quantity = self._sizing.base_quantity(
            price,
            self._instrument.size_increment.as_decimal(),
        )
        order = self.order_factory.market(
            instrument_id=self.config.instrument_id,
            order_side=OrderSide.SELL,
            quantity=self._instrument.make_qty(quantity),
        )
        self._machine.begin_entry()
        self._entry_order_id = order.client_order_id
        self._persist()
        self.submit_order(order)

    def _converge_protection(self) -> None:
        actual_qty = self._actual_short_qty()
        if actual_qty <= 0:
            return
        protected_qty = self._accepted_protective_qty()
        self._machine.record_exposure(actual_qty, protected_qty)

        if protected_qty == actual_qty:
            self._persist()
            return
        quantity = self._instrument.make_qty(actual_qty)
        if self._protective_order_id is None:
            factory_method = (
                self.order_factory.stop_market
                if protective_order_type(self.config.price_direction) is OrderType.STOP_MARKET
                else self.order_factory.market_if_touched
            )
            trigger = factory_method(
                instrument_id=self.config.instrument_id,
                order_side=OrderSide.BUY,
                quantity=quantity,
                trigger_price=Price.from_str(str(self.config.price_level)),
                reduce_only=True,
                tags=["native-price-protection"],
            )
            self._protective_order_id = trigger.client_order_id
            self.submit_order(trigger)
        else:
            order = self.cache.order(self._protective_order_id)
            if order is None or order.is_closed:
                self._machine.protection_failed("protective trigger missing during resize")
                self._persist()
                self._emergency_flatten()
                return
            self.modify_order(order, quantity=quantity)
        self.clock.set_time_alert(
            self.PROTECTION_TIMER,
            self.clock.utc_now() + timedelta(seconds=self.config.protection_timeout_secs),
            callback=self._on_protection_timeout,
            override=True,
            allow_past=False,
        )
        self._persist()

    def _handle_protection_confirmation(self, client_order_id) -> None:
        if self._protective_order_id is None or client_order_id != self._protective_order_id:
            return
        actual_qty = self._actual_short_qty()
        protected_qty = self._accepted_protective_qty()
        try:
            self._machine.confirm_protection(protected_qty)
        except (InvalidTransition, ValueError):
            self._machine.mark_conflict("invalid protective trigger confirmation")
        if protected_qty != actual_qty:
            self._converge_protection()
        else:
            if self.clock.next_time_ns(self.PROTECTION_TIMER) > 0:
                self.clock.cancel_timer(self.PROTECTION_TIMER)
            self._persist()

    def _on_protection_timeout(self, _) -> None:
        if self._machine.snapshot.state is not StrategyState.PROTECTING:
            return
        self._machine.protection_failed("protective trigger confirmation timed out")
        self._persist()
        self._emergency_flatten()

    def _request_close(self, reason: ExitReason) -> None:
        if not self._machine.request_exit(reason):
            return
        self._persist()
        for position in self._short_positions():
            self.close_position(position, reduce_only=True, tags=[reason.value])

    def _emergency_flatten(self) -> None:
        for position in self._short_positions():
            self.close_position(position, reduce_only=True, tags=["emergency_exit"])

    def _short_positions(self):
        return [
            position
            for position in self.cache.positions_open(instrument_id=self.config.instrument_id)
            if position.is_short
        ]

    def _actual_short_qty(self) -> Decimal:
        return sum(
            (position.quantity.as_decimal() for position in self._short_positions()),
            start=Decimal(0),
        )

    def _accepted_protective_qty(self) -> Decimal:
        if self._protective_order_id is None:
            return Decimal(0)
        order = self.cache.order(self._protective_order_id)
        if (
            order is None
            or order.is_closed
            or order.order_type not in {OrderType.STOP_MARKET, OrderType.MARKET_IF_TOUCHED}
        ):
            return Decimal(0)
        accepted_statuses = {
            OrderStatus.ACCEPTED,
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.TRIGGERED,
        }
        return order.quantity.as_decimal() if order.status in accepted_statuses else Decimal(0)

    def _restore_state(self) -> None:
        journal = self._journal.load()
        if journal is not None:
            self._entry_order_id = _client_order_id(journal.entry_order)
            self._exit_order_id = _client_order_id(journal.exit_order)
            self._protective_order_id = _client_order_id(journal.protective_order)
        snapshot = reconcile(
            journal,
            ExchangeSnapshot(self._actual_short_qty(), self._accepted_protective_qty()),
        )
        self._machine = StrategyStateMachine(snapshot)

    def _persist(self) -> None:
        snapshot = self._machine.snapshot
        previous = self._journal.load()
        self._journal.save(
            RunRecord(
                run_id=previous.run_id if previous is not None else str(uuid4()),
                state=snapshot.state,
                entry_order=str(self._entry_order_id) if self._entry_order_id else None,
                exit_order=str(self._exit_order_id) if self._exit_order_id else None,
                exit_reason=snapshot.exit_reason,
                protective_order=(
                    str(self._protective_order_id) if self._protective_order_id else None
                ),
            )
        )


def protective_order_type(direction: PriceDirection) -> OrderType:
    """Map short-exit direction to Hyperliquid's native trigger semantics."""
    return (
        OrderType.STOP_MARKET if direction is PriceDirection.ABOVE else OrderType.MARKET_IF_TOUCHED
    )


def _client_order_id(value: str | None) -> ClientOrderId | None:
    return ClientOrderId(value) if value is not None else None
