from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from types import SimpleNamespace

import nautilus_trader
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import OrderType
from nautilus_trader.model.identifiers import ClientOrderId, InstrumentId
from nautilus_trader.trading.strategy import Strategy

from hltrader.domain.exit_rules import PriceDirection
from hltrader.domain.state_machine import StrategyState
from hltrader.strategies.short_btc_rsi import (
    ShortBtcRsiConfig,
    ShortBtcRsiStrategy,
    protection_resize_required,
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


def test_duplicate_fill_does_not_request_duplicate_pending_resize() -> None:
    assert protection_resize_required(
        actual_qty=Decimal("0.006"),
        protected_qty=Decimal("0.003"),
        requested_qty=Decimal("0.006"),
    ) is False
    assert protection_resize_required(
        actual_qty=Decimal("0.006"),
        protected_qty=Decimal("0.003"),
        requested_qty=Decimal("0.003"),
    ) is True


def protecting_strategy(actual: str = "0.006", protected: str = "0") -> ShortBtcRsiStrategy:
    strategy = ShortBtcRsiStrategy(make_config())
    strategy._machine.begin_entry()
    strategy._machine.record_exposure(Decimal(actual), Decimal(protected))
    strategy._protective_order_id = ClientOrderId("P-1")
    return strategy


def test_stale_trigger_acceptance_after_second_fill_stays_protecting(monkeypatch) -> None:
    strategy = protecting_strategy()
    convergences = []
    monkeypatch.setattr(strategy, "_actual_short_qty", lambda: Decimal("0.006"))
    monkeypatch.setattr(strategy, "_accepted_protective_qty", lambda: Decimal("0.003"))
    monkeypatch.setattr(strategy, "_converge_protection", lambda: convergences.append(True))

    strategy._handle_protection_confirmation(ClientOrderId("P-1"))

    snapshot = strategy._machine.snapshot
    assert snapshot.state is StrategyState.PROTECTING
    assert snapshot.actual_net_position_qty == Decimal("0.006")
    assert snapshot.protected_qty == Decimal("0.003")
    assert convergences == [True]


def test_acceptance_timeout_race_never_produces_conflict_or_double_flatten(monkeypatch) -> None:
    for _ in range(25):
        strategy = protecting_strategy()
        flatten_calls = []
        monkeypatch.setattr(strategy, "_actual_short_qty", lambda: Decimal("0.006"))
        monkeypatch.setattr(strategy, "_accepted_protective_qty", lambda: Decimal("0.006"))
        monkeypatch.setattr(strategy, "_cancel_protection_timer", lambda: None)
        monkeypatch.setattr(strategy, "_persist", lambda: None)
        monkeypatch.setattr(strategy, "_converge_protection", lambda: None)
        monkeypatch.setattr(
            strategy,
            "_emergency_flatten",
            lambda calls=flatten_calls: calls.append(True),
        )

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(
                    strategy._handle_protection_confirmation,
                    ClientOrderId("P-1"),
                ),
                executor.submit(strategy._on_protection_timeout, None),
            ]
            for future in futures:
                future.result()

        assert strategy._machine.snapshot.state in {
            StrategyState.OPEN,
            StrategyState.EMERGENCY_EXIT,
        }
        assert len(flatten_calls) <= 1


def test_rejection_during_resize_enters_emergency_once(monkeypatch) -> None:
    strategy = protecting_strategy(protected="0.003")
    flatten_calls = []
    persisted = []
    monkeypatch.setattr(strategy, "_persist", lambda: persisted.append(strategy._machine.snapshot))
    monkeypatch.setattr(strategy, "_emergency_flatten", lambda: flatten_calls.append(True))
    event = SimpleNamespace(client_order_id=ClientOrderId("P-1"), reason="resize rejected")

    strategy.on_order_rejected(event)
    strategy.on_order_rejected(event)

    snapshot = strategy._machine.snapshot
    assert snapshot.state is StrategyState.EMERGENCY_EXIT
    assert snapshot.protected_qty == Decimal("0.003")
    assert snapshot.exit_reason == "protective trigger rejected: resize rejected"
    assert len(flatten_calls) == 1
    assert len(persisted) == 1


def test_late_entry_fill_during_emergency_reinvokes_idempotent_flatten(monkeypatch) -> None:
    strategy = protecting_strategy(actual="0.003")
    strategy._machine.protection_failed("trigger rejected")
    strategy._entry_order_id = ClientOrderId("E-1")
    flatten_calls = []
    monkeypatch.setattr(strategy, "_emergency_flatten", lambda: flatten_calls.append(True))
    event = SimpleNamespace(instrument_id=INSTRUMENT_ID, client_order_id=ClientOrderId("E-1"))

    strategy.on_order_filled(event)
    strategy.on_order_filled(event)

    assert strategy._machine.snapshot.state is StrategyState.EMERGENCY_EXIT
    assert flatten_calls == [True, True]


def test_emergency_flatten_recalculates_only_uncovered_quantity(monkeypatch) -> None:
    strategy = protecting_strategy(actual="0.003")
    strategy._machine.protection_failed("trigger rejected")
    actual = [Decimal("0.003")]
    position = SimpleNamespace()
    submissions = []
    monkeypatch.setattr(strategy, "_actual_short_qty", lambda: actual[0])
    monkeypatch.setattr(strategy, "_short_positions", lambda: [position])

    def submit(position_arg, reason, *, quantity=None):
        assert position_arg is position
        submissions.append((reason, quantity))
        strategy._flatten_outstanding[ClientOrderId(f"F-{len(submissions)}")] = quantity

    monkeypatch.setattr(strategy, "_submit_close", submit)
    monkeypatch.setattr(strategy, "_persist", lambda: None)

    strategy._emergency_flatten()
    strategy._emergency_flatten()
    actual[0] = Decimal("0.006")
    strategy._emergency_flatten()

    assert submissions == [
        ("emergency_exit", Decimal("0.003")),
        ("emergency_exit", Decimal("0.003")),
    ]
    assert strategy._machine.snapshot.actual_net_position_qty == Decimal("0.006")


def test_position_closed_event_cannot_close_while_exposure_remains(monkeypatch) -> None:
    strategy = protecting_strategy(actual="0.003")
    strategy._machine.protection_failed("trigger rejected")
    flatten_calls = []
    monkeypatch.setattr(strategy, "_actual_short_qty", lambda: Decimal("0.002"))
    monkeypatch.setattr(strategy, "_emergency_flatten", lambda: flatten_calls.append(True))
    monkeypatch.setattr(strategy, "_persist", lambda: None)
    event = SimpleNamespace(instrument_id=INSTRUMENT_ID)

    strategy.on_position_closed(event)

    assert strategy._machine.snapshot.state is StrategyState.EMERGENCY_EXIT
    assert flatten_calls == [True]
