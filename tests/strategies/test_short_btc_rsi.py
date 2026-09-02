from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import nautilus_trader
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import OrderType
from nautilus_trader.model.identifiers import ClientOrderId, InstrumentId
from nautilus_trader.trading.strategy import Strategy

from hltrader.domain.exit_rules import PriceDirection
from hltrader.domain.state_machine import StrategyState
from hltrader.persistence.run_journal import RunRecord
from hltrader.risk.bootstrap_margin import (
    BootstrapExpectation,
    UpdateLeverageCommand,
    classify_bootstrap_response,
    save_bootstrap_receipt,
)
from hltrader.risk.guard import MarginMode
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
    assert config.bootstrap_margin_receipt_path is None


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


def _arrange_restart(
    monkeypatch,
    *,
    state: StrategyState,
    actual: str,
    outstanding: str = "0",
    protected: str = "0",
    protection_conflict: str | None = None,
    exit_conflict: str | None = None,
):
    strategy = ShortBtcRsiStrategy(make_config())
    journal = RunRecord(
        "run-1",
        state,
        exit_order="X-1" if outstanding != "0" else None,
        exit_reason="trigger rejected" if state is StrategyState.EMERGENCY_EXIT else "rsi",
        protective_order="P-1" if protected != "0" else None,
        exit_orders=("X-1",) if outstanding != "0" else (),
    )
    outstanding_map = (
        {ClientOrderId("X-1"): Decimal(outstanding)} if outstanding != "0" else {}
    )
    submissions = []
    persisted = []
    position = SimpleNamespace()
    monkeypatch.setattr(strategy._journal, "load", lambda: journal)
    monkeypatch.setattr(
        strategy,
        "_venue_protection_snapshot",
        lambda: (Decimal(protected), protection_conflict),
    )
    monkeypatch.setattr(
        strategy,
        "_venue_exit_snapshot",
        lambda: (dict(outstanding_map), exit_conflict),
    )
    monkeypatch.setattr(strategy, "_actual_short_qty", lambda: Decimal(actual))
    monkeypatch.setattr(
        strategy,
        "_short_positions",
        lambda: [position] if Decimal(actual) > 0 else [],
    )
    monkeypatch.setattr(strategy, "_persist", lambda: persisted.append(strategy._machine.snapshot))

    def submit(position_arg, reason, *, quantity=None):
        submissions.append((position_arg, reason, quantity))
        strategy._flatten_outstanding[ClientOrderId("X-new")] = quantity

    monkeypatch.setattr(strategy, "_submit_close", submit)
    return strategy, submissions, persisted


def test_restart_emergency_with_pending_flatten_does_not_duplicate(monkeypatch) -> None:
    strategy, submissions, _ = _arrange_restart(
        monkeypatch,
        state=StrategyState.EMERGENCY_EXIT,
        actual="0.006",
        outstanding="0.006",
    )
    strategy._restore_state()
    assert strategy._machine.snapshot.state is StrategyState.EMERGENCY_EXIT
    assert strategy._flatten_outstanding == {ClientOrderId("X-1"): Decimal("0.006")}
    assert submissions == []


def test_restart_partial_flatten_uses_open_leaves_quantity(monkeypatch) -> None:
    strategy, submissions, _ = _arrange_restart(
        monkeypatch,
        state=StrategyState.EMERGENCY_EXIT,
        actual="0.004",
        outstanding="0.004",
    )
    strategy._restore_state()
    assert strategy._machine.snapshot.actual_net_position_qty == Decimal("0.004")
    assert strategy._flatten_outstanding[ClientOrderId("X-1")] == Decimal("0.004")
    assert submissions == []


def test_restart_missing_flatten_submits_exact_shortfall_once(monkeypatch) -> None:
    strategy, submissions, _ = _arrange_restart(
        monkeypatch,
        state=StrategyState.EMERGENCY_EXIT,
        actual="0.004",
    )
    strategy._restore_state()
    strategy._restore_state()
    assert [(reason, qty) for _, reason, qty in submissions] == [
        ("emergency_exit", Decimal("0.004"))
    ]


def test_restart_ambiguous_reduce_only_exit_fails_closed_without_submission(monkeypatch) -> None:
    strategy, submissions, _ = _arrange_restart(
        monkeypatch,
        state=StrategyState.EMERGENCY_EXIT,
        actual="0.004",
        exit_conflict="open reduce-only exit cannot be uniquely attributed to this run",
    )
    strategy._restore_state()
    assert strategy._machine.snapshot.state is StrategyState.STATE_CONFLICT
    assert submissions == []


def test_restart_partial_protection_never_reconstructs_open(monkeypatch) -> None:
    strategy, submissions, _ = _arrange_restart(
        monkeypatch,
        state=StrategyState.PROTECTING,
        actual="0.006",
        protected="0.003",
    )
    convergences = []
    monkeypatch.setattr(strategy, "_converge_protection", lambda: convergences.append(True))
    strategy._restore_state()
    assert strategy._machine.snapshot.state is StrategyState.PROTECTING
    assert convergences == [True]
    assert submissions == []


def test_restart_exact_protection_reconstructs_open(monkeypatch) -> None:
    strategy, submissions, _ = _arrange_restart(
        monkeypatch,
        state=StrategyState.PROTECTING,
        actual="0.006",
        protected="0.006",
    )
    strategy._restore_state()
    assert strategy._machine.snapshot.state is StrategyState.OPEN
    assert submissions == []


def test_restart_missing_journaled_protector_fails_closed(monkeypatch) -> None:
    strategy, submissions, _ = _arrange_restart(
        monkeypatch,
        state=StrategyState.PROTECTING,
        actual="0.006",
        protection_conflict="journaled protective trigger does not uniquely match venue orders",
    )
    strategy._restore_state()
    assert strategy._machine.snapshot.state is StrategyState.STATE_CONFLICT
    assert submissions == []


def test_restart_exiting_with_covered_shortfall_does_not_duplicate(monkeypatch) -> None:
    strategy, submissions, _ = _arrange_restart(
        monkeypatch,
        state=StrategyState.EXITING,
        actual="0.004",
        outstanding="0.004",
    )
    strategy._restore_state()
    assert strategy._machine.snapshot.state is StrategyState.EXITING
    assert submissions == []


def test_restart_after_economic_close_persists_closed_final(monkeypatch) -> None:
    strategy, submissions, persisted = _arrange_restart(
        monkeypatch,
        state=StrategyState.EMERGENCY_EXIT,
        actual="0",
    )
    strategy._restore_state()
    assert strategy._machine.snapshot.state is StrategyState.CLOSED_FINAL
    assert persisted[-1].state is StrategyState.CLOSED_FINAL
    assert submissions == []


def test_strategy_consumes_matching_bootstrap_receipt_only_once(tmp_path) -> None:
    path = tmp_path / "bootstrap.json"
    account = "0x1111111111111111111111111111111111111111"
    signer = "0x2222222222222222222222222222222222222222"
    strategy = ShortBtcRsiStrategy(
        make_config(
            account_address=account,
            bootstrap_margin_receipt_path=str(path),
            bootstrap_signer_address=signer,
        )
    )
    now = datetime(2026, 9, 2, 12, tzinfo=UTC)
    expected = BootstrapExpectation(
        strategy._bootstrap_session_id,
        account,
        signer,
        "testnet",
        str(INSTRUMENT_ID),
        "BTC",
        0,
        MarginMode.ISOLATED,
        Decimal(3),
    )
    command = UpdateLeverageCommand(
        strategy._bootstrap_session_id,
        account,
        signer,
        "testnet",
        str(INSTRUMENT_ID),
        "BTC",
        0,
        False,
        3,
        1788346800000,
    )
    receipt = classify_bootstrap_response(
        expected,
        command,
        {"status": "ok", "response": {"type": "default"}},
        observed_at=now,
    )
    save_bootstrap_receipt(path, receipt)

    assert strategy._margin_authorizes_entry("entry-1", now=now)
    assert not strategy._margin_authorizes_entry("entry-2", now=now)


def test_strategy_never_uses_bootstrap_receipt_for_mainnet(tmp_path) -> None:
    strategy = ShortBtcRsiStrategy(
        make_config(
            environment="mainnet",
            account_address="0x1111111111111111111111111111111111111111",
            bootstrap_margin_receipt_path=str(tmp_path / "bootstrap.json"),
            bootstrap_signer_address="0x2222222222222222222222222222222222222222",
        )
    )
    assert not strategy._margin_authorizes_entry(
        "entry-1", now=datetime(2026, 9, 2, 12, tzinfo=UTC)
    )
