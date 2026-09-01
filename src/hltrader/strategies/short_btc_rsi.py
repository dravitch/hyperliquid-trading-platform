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
from hltrader.domain.reconciliation import (
    ExchangeSnapshot,
    OpenExitOrder,
    reconcile,
    reconstruct_exit_outstanding,
)
from hltrader.domain.sizing import PositionSizing
from hltrader.domain.state_machine import InvalidTransition, StrategyState, StrategyStateMachine
from hltrader.indicators.rsi import RsiWarmupPolicy
from hltrader.orchestration.submission import submit_entry_safely, submit_protection_safely
from hltrader.persistence.run_journal import RunJournal, RunRecord
from hltrader.risk.guard import MarginMode
from hltrader.risk.margin_verification import MarginVerificationError, load_margin_verification


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
    account_address: str = ""
    environment: str = "testnet"
    desired_margin_mode: MarginMode = MarginMode.ISOLATED
    desired_leverage: Decimal = Decimal(3)
    margin_verification_path: str | None = None
    margin_verification_max_age_secs: int = 300
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
    EXPOSURE_SYNC_TIMER = "position-cache-synchronization"

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
        self._exit_order_ids: set[ClientOrderId] = set()
        self._exposure_sync_attempts = 0
        self._requested_protective_qty = Decimal(0)
        self._flatten_outstanding: dict[ClientOrderId, Decimal] = {}
        self._restore_completed = False

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
            if self._machine.snapshot.state is StrategyState.EMERGENCY_EXIT:
                self._emergency_flatten()
            elif self._machine.snapshot.state is StrategyState.EXITING:
                self._converge_close()
            else:
                self._converge_protection()
            return
        if event.client_order_id in self._flatten_outstanding:
            remaining = self._flatten_outstanding[event.client_order_id] - event.last_qty.as_decimal()
            if remaining > 0:
                self._flatten_outstanding[event.client_order_id] = remaining
            else:
                self._flatten_outstanding.pop(event.client_order_id, None)
            if self._machine.snapshot.state is StrategyState.EMERGENCY_EXIT:
                self._emergency_flatten()
            elif self._machine.snapshot.state is StrategyState.EXITING:
                self._converge_close()
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
        if self._machine.snapshot.state is not StrategyState.PROTECTING:
            return
        try:
            snapshot = self._machine.protection_failed(
                f"protective trigger rejected: {event.reason}"
            )
        except InvalidTransition:
            return
        if snapshot.state is not StrategyState.EMERGENCY_EXIT:
            return
        self._persist()
        self._emergency_flatten()

    def on_position_closed(self, event: PositionClosed) -> None:
        if event.instrument_id != self.config.instrument_id:
            return
        actual_qty = self._actual_short_qty()
        try:
            self._machine.confirm_closed(actual_qty)
        except InvalidTransition:
            self._machine.mark_conflict("position closed outside an expected exit state")
        except ValueError:
            if self._machine.snapshot.state is StrategyState.EMERGENCY_EXIT:
                self._emergency_flatten()
            else:
                self._machine.mark_conflict("position closed event received while exposure remains")
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
        if not self._margin_is_verified():
            self.log.error("Entry blocked: no matching fresh venue margin verification receipt")
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
        submit_entry_safely(
            self._machine,
            submit=lambda: self.submit_order(order),
            persist=self._persist,
            report_error=self.log.error,
        )

    def _margin_is_verified(self) -> bool:
        if not self.config.margin_verification_path or not self.config.account_address:
            return False
        try:
            receipt = load_margin_verification(Path(self.config.margin_verification_path))
        except MarginVerificationError as exc:
            self.log.error(str(exc))
            return False
        return receipt.matches(
            account_address=self.config.account_address,
            environment=self.config.environment,
            instrument_id=str(self.config.instrument_id),
            margin_mode=self.config.desired_margin_mode,
            leverage=self.config.desired_leverage,
            now=self.clock.utc_now(),
            max_age_seconds=self.config.margin_verification_max_age_secs,
        )

    def _converge_protection(self) -> None:
        actual_qty = self._actual_short_qty()
        if actual_qty <= 0:
            self._schedule_exposure_sync_retry()
            return
        self._exposure_sync_attempts = 0
        if self.clock.next_time_ns(self.EXPOSURE_SYNC_TIMER) > 0:
            self.clock.cancel_timer(self.EXPOSURE_SYNC_TIMER)
        protected_qty = self._accepted_protective_qty()
        self._machine.record_exposure(actual_qty, protected_qty)

        if protected_qty == actual_qty:
            self._requested_protective_qty = actual_qty
            self._persist()
            return
        if not protection_resize_required(
            actual_qty=actual_qty,
            protected_qty=protected_qty,
            requested_qty=self._requested_protective_qty,
        ):
            self._persist()
            return
        quantity = self._instrument.make_qty(actual_qty)
        trigger = None
        order = None
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
        else:
            order = self.cache.order(self._protective_order_id)
            if order is None or order.is_closed:
                self._machine.protection_failed("protective trigger missing during resize")
                self._persist()
                self._emergency_flatten()
                return
        submitted = submit_protection_safely(
            self._machine,
            arm_timer=self._arm_protection_timer,
            cancel_timer=self._cancel_protection_timer,
            submit=(
                (lambda: self.submit_order(trigger))
                if trigger is not None
                else (lambda: self.modify_order(order, quantity=quantity))
            ),
            persist=self._persist,
            emergency_flatten=self._emergency_flatten,
            report_error=self.log.error,
        )
        if not submitted:
            return
        self._requested_protective_qty = actual_qty
        self._persist()

    def _arm_protection_timer(self) -> None:
        self.clock.set_time_alert(
            self.PROTECTION_TIMER,
            self.clock.utc_now() + timedelta(seconds=self.config.protection_timeout_secs),
            callback=self._on_protection_timeout,
            override=True,
            allow_past=False,
        )

    def _cancel_protection_timer(self) -> None:
        if self.clock.next_time_ns(self.PROTECTION_TIMER) > 0:
            self.clock.cancel_timer(self.PROTECTION_TIMER)

    def _schedule_exposure_sync_retry(self) -> None:
        self._exposure_sync_attempts += 1
        if self._exposure_sync_attempts > 3:
            self._machine.recovery_required(
                "entry fill observed but position cache remained empty",
                allowed={
                    StrategyState.ENTERING,
                    StrategyState.PROTECTING,
                    StrategyState.OPEN,
                },
            )
            self._persist()
            return
        self.clock.set_time_alert(
            self.EXPOSURE_SYNC_TIMER,
            self.clock.utc_now() + timedelta(milliseconds=1),
            callback=lambda _: self._converge_protection(),
            override=True,
            allow_past=False,
        )

    def _handle_protection_confirmation(self, client_order_id) -> None:
        if self._protective_order_id is None or client_order_id != self._protective_order_id:
            return
        if self._machine.snapshot.state is StrategyState.EMERGENCY_EXIT:
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
            self._cancel_protection_timer()
            self._persist()

    def _on_protection_timeout(self, _) -> None:
        if self._machine.snapshot.state is not StrategyState.PROTECTING:
            return
        snapshot = self._machine.protection_failed("protective trigger confirmation timed out")
        if snapshot.state is not StrategyState.EMERGENCY_EXIT:
            return
        self._persist()
        self._emergency_flatten()

    def _request_close(self, reason: ExitReason) -> None:
        if not self._machine.request_exit(reason):
            return
        self._persist()
        for position in self._short_positions():
            self._submit_close(position, reason.value)

    def _emergency_flatten(self) -> None:
        self._converge_close()

    def _converge_close(self) -> None:
        actual_qty = self._actual_short_qty()
        self._machine.record_closing_exposure(actual_qty)
        if actual_qty == 0:
            if not self._flatten_outstanding:
                self._machine.confirm_closed(actual_qty)
            self._persist()
            return
        outstanding = sum(self._flatten_outstanding.values(), start=Decimal(0))
        flatten_qty = actual_qty - outstanding
        if flatten_qty <= 0:
            self._persist()
            return
        positions = self._short_positions()
        if not positions:
            self._persist()
            return
        reason = (
            "emergency_exit"
            if self._machine.snapshot.state is StrategyState.EMERGENCY_EXIT
            else self._machine.snapshot.exit_reason or "exit"
        )
        self._submit_close(positions[0], reason, quantity=flatten_qty)

    def _submit_close(self, position, reason: str, *, quantity: Decimal | None = None) -> None:
        order_quantity = (
            position.quantity if quantity is None else self._instrument.make_qty(quantity)
        )
        order = self.order_factory.market(
            instrument_id=position.instrument_id,
            order_side=OrderSide.BUY,
            quantity=order_quantity,
            reduce_only=True,
            tags=[reason],
        )
        self._exit_order_id = order.client_order_id
        self._exit_order_ids.add(order.client_order_id)
        self._flatten_outstanding[order.client_order_id] = order_quantity.as_decimal()
        self._persist()
        try:
            self.submit_order(order, position_id=position.id)
        except Exception as exc:  # noqa: BLE001 - fail closed across the framework boundary
            self._flatten_outstanding.pop(order.client_order_id, None)
            try:
                self._machine.recovery_required(
                    f"exit submission failed: {exc}",
                    allowed={StrategyState.EXITING, StrategyState.EMERGENCY_EXIT},
                )
            except InvalidTransition:
                self._machine.mark_conflict(f"emergency exit submission failed: {exc}")
            self._persist()
            self.log.error("Exit submission failed; manual recovery required")

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
        if self._restore_completed:
            return
        self._restore_completed = True
        journal = self._journal.load()
        if journal is not None:
            self._entry_order_id = _client_order_id(journal.entry_order)
            self._exit_order_id = _client_order_id(journal.exit_order)
            self._exit_order_ids = {
                ClientOrderId(order_id) for order_id in journal.exit_orders
            }
            if self._exit_order_id is not None:
                self._exit_order_ids.add(self._exit_order_id)
            self._protective_order_id = _client_order_id(journal.protective_order)
        protected_qty, conflict = self._venue_protection_snapshot()
        self._flatten_outstanding, exit_conflict = self._venue_exit_snapshot()
        snapshot = reconcile(
            journal,
            ExchangeSnapshot(
                self._actual_short_qty(),
                protected_qty,
                conflict,
                sum(self._flatten_outstanding.values(), start=Decimal(0)),
                exit_conflict,
            ),
        )
        self._machine = StrategyStateMachine(snapshot)
        if snapshot.state in {StrategyState.EXITING, StrategyState.EMERGENCY_EXIT}:
            self._converge_close()
        elif snapshot.state is StrategyState.PROTECTING:
            self._converge_protection()
        elif snapshot.state is StrategyState.CLOSED_FINAL:
            self._persist()

    def _venue_exit_snapshot(self) -> tuple[dict[ClientOrderId, Decimal], str | None]:
        trigger_types = {OrderType.STOP_MARKET, OrderType.MARKET_IF_TOUCHED}
        exits = [
            order
            for order in self.cache.orders_open(instrument_id=self.config.instrument_id)
            if order.order_type not in trigger_types
            and order.side is OrderSide.BUY
            and order.is_reduce_only
        ]
        outstanding, conflict = reconstruct_exit_outstanding(
            {str(order_id) for order_id in self._exit_order_ids},
            tuple(
                OpenExitOrder(str(order.client_order_id), order.leaves_qty.as_decimal())
                for order in exits
            ),
        )
        return {
            ClientOrderId(order_id): quantity for order_id, quantity in outstanding.items()
        }, conflict

    def _venue_protection_snapshot(self) -> tuple[Decimal, str | None]:
        trigger_types = {OrderType.STOP_MARKET, OrderType.MARKET_IF_TOUCHED}
        protectors = [
            order
            for order in self.cache.orders_open(instrument_id=self.config.instrument_id)
            if order.order_type in trigger_types
            and order.side is OrderSide.BUY
            and order.is_reduce_only
        ]
        if self._protective_order_id is None:
            if protectors:
                return Decimal(0), "open protective trigger exists without a journaled identity"
            return Decimal(0), None
        matching = [
            order for order in protectors if order.client_order_id == self._protective_order_id
        ]
        if len(matching) != 1 or len(protectors) != 1:
            return Decimal(0), "journaled protective trigger does not uniquely match venue orders"
        return matching[0].quantity.as_decimal(), None

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
                exit_orders=tuple(sorted(str(order_id) for order_id in self._exit_order_ids)),
            )
        )


def protective_order_type(direction: PriceDirection) -> OrderType:
    """Map short-exit direction to Hyperliquid's native trigger semantics."""
    return (
        OrderType.STOP_MARKET if direction is PriceDirection.ABOVE else OrderType.MARKET_IF_TOUCHED
    )


def protection_resize_required(
    *, actual_qty: Decimal, protected_qty: Decimal, requested_qty: Decimal
) -> bool:
    """Return whether a new venue command is needed for the cumulative exposure."""
    if protected_qty == actual_qty:
        return False
    return requested_qty != actual_qty


def _client_order_id(value: str | None) -> ClientOrderId | None:
    return ClientOrderId(value) if value is not None else None
