from __future__ import annotations

import asyncio
from collections import Counter
from decimal import Decimal

import pytest
from nautilus_trader.adapters.hyperliquid.config import HyperliquidExecClientConfig
from nautilus_trader.config import (
    LiveExecEngineConfig,
    LiveRiskEngineConfig,
    LoggingConfig,
    TradingNodeConfig,
)
from nautilus_trader.core import nautilus_pyo3
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.execution.reports import OrderStatusReport, PositionStatusReport
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import (
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
    TimeInForce,
    TriggerType,
)
from nautilus_trader.model.identifiers import Venue, VenueOrderId
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from nautilus_trader.test_kit.stubs.execution import TestExecStubs
from nautilus_trader.test_kit.stubs.identifiers import TestIdStubs
from nautilus_trader.trading.strategy import Strategy

from hltrader.domain.state_machine import StrategyState
from hltrader.persistence.run_journal import RunJournal, RunRecord
from hltrader.strategies.short_btc_rsi import ShortBtcRsiConfig, ShortBtcRsiStrategy
from hltrader.venue.read_only_execution import (
    ReadOnlyHyperliquidExecClientFactory,
    ReadOnlyHyperliquidExecutionClient,
)

ACCOUNT_ADDRESS = "0x0000000000000000000000000000000000000001"
INSTRUMENT = TestInstrumentProvider.default_fx_ccy("BTC/USD", Venue("HYPERLIQUID"))


class MutationCanary:
    """Explodes if any adapter mutation reaches either underlying transport."""

    def __init__(self) -> None:
        self.calls: Counter[str] = Counter()

    def __getattr__(self, name: str):
        def reached(*args, **kwargs):
            del args, kwargs
            self.calls[name] += 1
            raise AssertionError(f"MUTATION TRANSPORT REACHED: {name}")

        return reached


class CommandProbeStrategy(Strategy):
    """Minimal real Strategy entry point for the engine-boundary proof."""


def _node(loop: asyncio.AbstractEventLoop) -> tuple[TradingNode, CommandProbeStrategy]:
    config = HyperliquidExecClientConfig(
        environment=nautilus_pyo3.HyperliquidEnvironment.TESTNET,
        account_address=ACCOUNT_ADDRESS,
    )
    node = TradingNode(
        TradingNodeConfig(
            exec_clients={"HYPERLIQUID": config},
            risk_engine=LiveRiskEngineConfig(bypass=True),
            exec_engine=LiveExecEngineConfig(
                reconciliation=False,
                inflight_check_interval_ms=0,
                open_check_interval_secs=None,
                position_check_interval_secs=None,
            ),
            logging=LoggingConfig(log_level="ERROR"),
        ),
        loop=loop,
    )
    node.add_exec_client_factory("HYPERLIQUID", ReadOnlyHyperliquidExecClientFactory)
    node.cache.add_instrument(INSTRUMENT)
    strategy = CommandProbeStrategy()
    node.trader.add_strategy(strategy)
    node.build()
    return node, strategy


async def _settle(node: TradingNode) -> None:
    risk = node.kernel.risk_engine
    execution = node.kernel.exec_engine
    for _ in range(100):
        await asyncio.sleep(0.001)
        if (
            risk.cmd_qsize() == 0
            and risk.evt_qsize() == 0
            and execution.cmd_qsize() == 0
            and execution.evt_qsize() == 0
        ):
            await asyncio.sleep(0)
            return
    raise AssertionError("Nautilus queues did not settle")


async def _exercise_engine_path(
    node: TradingNode,
    strategy: CommandProbeStrategy,
) -> tuple[ReadOnlyHyperliquidExecutionClient, MutationCanary]:
    risk = node.kernel.risk_engine
    execution = node.kernel.exec_engine
    client = next(iter(execution._clients.values()))
    assert isinstance(client, ReadOnlyHyperliquidExecutionClient)

    canary = MutationCanary()
    client._client = canary
    client._ws_client = canary
    execution.start()
    risk.start()

    # This is the real Strategy -> RiskEngine -> ExecutionEngine -> wrapper path.
    submitted = strategy.order_factory.market(
        instrument_id=INSTRUMENT.id,
        order_side=OrderSide.BUY,
        quantity=INSTRUMENT.make_qty(1),
    )
    strategy.submit_order(submitted)
    await _settle(node)
    assert submitted.status is OrderStatus.DENIED

    working = TestExecStubs.make_accepted_order(
        instrument=INSTRUMENT,
        trader_id=node.trader.id,
        strategy_id=strategy.id,
        account_id=client.account_id,
    )
    node.cache.add_order(working)

    strategy.modify_order(working, quantity=INSTRUMENT.make_qty(2))
    await _settle(node)
    assert working.status is OrderStatus.ACCEPTED

    strategy.cancel_order(working)
    await _settle(node)
    assert working.status is OrderStatus.ACCEPTED

    strategy.cancel_all_orders(INSTRUMENT.id)
    await _settle(node)
    assert working.status is OrderStatus.ACCEPTED

    strategy.cancel_orders([working])
    await _settle(node)
    assert working.status is OrderStatus.ACCEPTED

    counts_after_settle = client.blocked_command_counts
    await asyncio.sleep(0.025)
    await _settle(node)
    assert client.blocked_command_counts == counts_after_settle
    assert execution.get_cmd_queue_task() is not None
    assert execution.get_evt_queue_task() is not None
    assert not execution.get_cmd_queue_task().done()
    assert not execution.get_evt_queue_task().done()
    assert execution._recon_check_retries == Counter()
    assert execution._position_recon_retries == Counter()
    assert node.cache.orders_inflight() == []

    risk.stop()
    execution.stop()
    await asyncio.sleep(0.001)
    return client, canary


def test_real_execution_engine_blocks_commands_and_settles(monkeypatch) -> None:
    for name in ReadOnlyHyperliquidExecClientFactory.SECRET_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    loop = asyncio.new_event_loop()
    node, strategy = _node(loop)
    try:
        client, canary = loop.run_until_complete(_exercise_engine_path(node, strategy))

        assert client.blocked_command_counts == {
            "submit_order": 1,
            "modify_order": 1,
            "cancel_order": 1,
            "cancel_all_orders": 1,
            "batch_cancel_orders": 1,
        }
        assert sum(canary.calls.values()) == 0
        assert node.kernel.risk_engine.cmd_qsize() == 0
        assert node.kernel.risk_engine.evt_qsize() == 0
        assert node.kernel.exec_engine.cmd_qsize() == 0
        assert node.kernel.exec_engine.evt_qsize() == 0
    finally:
        node.dispose()
        if not loop.is_closed():
            loop.close()


def _short_strategy(journal_path, state: StrategyState) -> ShortBtcRsiStrategy:
    if state is not StrategyState.NEVER_ENTERED:
        RunJournal(journal_path).save(
            RunRecord(
                run_id=f"run-{state.value.lower()}",
                state=state,
                exit_reason=("rsi" if state is StrategyState.EXITING else "test failure"),
            )
        )
    return ShortBtcRsiStrategy(
        ShortBtcRsiConfig(
            instrument_id=INSTRUMENT.id,
            bar_type=BarType.from_str(f"{INSTRUMENT.id}-1-DAY-LAST-EXTERNAL"),
            journal_path=str(journal_path),
            enable_order_submission=False,
        )
    )


async def _restore_scenario(
    node: TradingNode,
    strategy: ShortBtcRsiStrategy,
    actual: Decimal,
    protected: Decimal,
) -> tuple[dict[str, int], MutationCanary]:
    execution = node.kernel.exec_engine
    risk = node.kernel.risk_engine
    client = next(iter(execution._clients.values()))
    assert isinstance(client, ReadOnlyHyperliquidExecutionClient)
    canary = MutationCanary()
    client._client = canary
    client._ws_client = canary
    if actual > 0:
        node.cache.add_account(TestExecStubs.margin_account(account_id=client.account_id))
        reconciled = execution.reconcile_execution_report(
            PositionStatusReport(
                account_id=client.account_id,
                instrument_id=INSTRUMENT.id,
                position_side=PositionSide.SHORT,
                quantity=INSTRUMENT.make_qty(actual),
                report_id=UUID4(),
                ts_last=1,
                ts_init=1,
                venue_position_id=TestIdStubs.position_id(),
                avg_px_open=Decimal(1),
            )
        )
        assert reconciled is True
        assert node.cache.positions_open(instrument_id=INSTRUMENT.id)
    if protected > 0:
        protective = strategy.order_factory.stop_market(
            instrument_id=INSTRUMENT.id,
            order_side=OrderSide.BUY,
            quantity=INSTRUMENT.make_qty(protected),
            trigger_price=Price.from_str("2.00000"),
            reduce_only=True,
            tags=["native-price-protection"],
        )
        node.cache.add_order(protective)
        reconciled = execution.reconcile_execution_report(
            OrderStatusReport(
                account_id=client.account_id,
                instrument_id=INSTRUMENT.id,
                venue_order_id=VenueOrderId("PROTECTIVE-1"),
                order_side=OrderSide.BUY,
                order_type=OrderType.STOP_MARKET,
                time_in_force=TimeInForce.GTC,
                order_status=OrderStatus.ACCEPTED,
                quantity=INSTRUMENT.make_qty(protected),
                filled_qty=Quantity.from_int(0),
                report_id=UUID4(),
                ts_accepted=1,
                ts_last=1,
                ts_init=1,
                client_order_id=protective.client_order_id,
                trigger_price=Price.from_str("2.00000"),
                trigger_type=TriggerType.DEFAULT,
                reduce_only=True,
            )
        )
        assert reconciled is True
        assert protective in node.cache.orders_open(instrument_id=INSTRUMENT.id)
        record = strategy._journal.load()
        assert record is not None
        strategy._journal.save(
            RunRecord(
                run_id=record.run_id,
                state=record.state,
                exit_reason=record.exit_reason,
                protective_order=str(protective.client_order_id),
            )
        )
    execution.start()
    risk.start()
    strategy.start()
    await _settle(node)
    await asyncio.sleep(0.01)
    await _settle(node)
    # A stale timeout must be inert once a denial has moved the strategy out of PROTECTING.
    strategy._on_protection_timeout(None)
    # This is the callback target of the late exposure-sync timer.
    strategy._converge_protection()
    await _settle(node)
    blocked_command_counts = client.blocked_command_counts
    await asyncio.sleep(0.025)
    await _settle(node)
    assert client.blocked_command_counts == blocked_command_counts
    assert execution._recon_check_retries == Counter()
    assert execution._position_recon_retries == Counter()
    assert node.cache.orders_inflight() == []
    assert execution.get_cmd_queue_task() is not None
    assert execution.get_evt_queue_task() is not None
    assert not execution.get_cmd_queue_task().done()
    assert not execution.get_evt_queue_task().done()
    assert strategy.clock.next_time_ns(strategy.PROTECTION_TIMER) == 0
    strategy.stop()
    risk.stop()
    execution.stop()
    await asyncio.sleep(0.001)
    return blocked_command_counts, canary


@pytest.mark.parametrize(
    ("initial", "actual", "protected", "expected", "expected_commands"),
    [
        (StrategyState.NEVER_ENTERED, "0", "0", StrategyState.NEVER_ENTERED, {}),
        (StrategyState.OPEN, "6", "6", StrategyState.OPEN, {}),
        (
            StrategyState.PROTECTING,
            "6",
            "3",
            StrategyState.EMERGENCY_EXIT,
            {"modify_order": 1},
        ),
        (StrategyState.EXITING, "6", "0", StrategyState.RECOVERY_REQUIRED, {}),
        (
            StrategyState.EMERGENCY_EXIT,
            "6",
            "0",
            StrategyState.EMERGENCY_EXIT,
            {},
        ),
        (
            StrategyState.RECOVERY_REQUIRED,
            "6",
            "0",
            StrategyState.RECOVERY_REQUIRED,
            {},
        ),
    ],
)
def test_restart_states_settle_without_transport(
    monkeypatch,
    tmp_path,
    initial,
    actual,
    protected,
    expected,
    expected_commands,
) -> None:
    for name in ReadOnlyHyperliquidExecClientFactory.SECRET_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    loop = asyncio.new_event_loop()
    journal_path = tmp_path / f"{initial.value}.json"
    strategy = _short_strategy(journal_path, initial)
    node, _ = _node(loop)
    node.trader.add_strategy(strategy)
    monkeypatch.setattr(strategy, "_venue_exit_snapshot", lambda: ({}, None))
    try:
        blocked_command_counts, canary = loop.run_until_complete(
            _restore_scenario(node, strategy, Decimal(actual), Decimal(protected))
        )

        assert strategy._machine.snapshot.state is expected
        assert strategy._flatten_outstanding == {}
        assert blocked_command_counts == expected_commands
        assert sum(canary.calls.values()) == 0
        assert node.kernel.exec_engine.cmd_qsize() == 0
        assert node.kernel.exec_engine.evt_qsize() == 0
        if journal_path.exists():
            assert RunJournal(journal_path).load().state is expected
    finally:
        node.dispose()
        if not loop.is_closed():
            loop.close()
