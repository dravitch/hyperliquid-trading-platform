# Hyperliquid testnet roadmap

This roadmap separates the administrative bootstrap proof from the future Nautilus strategy
runtime. Completing the bootstrap does not start `ShortBtcRsiStrategy` and does not authorize an
order.

## A. Administrative bootstrap spike

```text
distinct testnet agent
        ↓
updateLeverage dry-run
        ↓
one controlled updateLeverage mutation
        ↓
bootstrap proof closure
```

The existing `hnt-update-leverage-spike` runner implements only this narrow path. Dry-run is the
default. Its signed mode is guarded separately and must not be confused with a strategy runtime.

## B. Future testnet strategy runtime

Code inspection on 2 September 2026 found no runner which instantiates a Nautilus `TradingNode`,
configures Hyperliquid data and execution clients, and adds `ShortBtcRsiStrategy`. The current
runners are only:

- `src/hltrader/runners/backtest.py`;
- `src/hltrader/runners/update_leverage_spike.py`.

A later milestone must add a distinct entry point such as `src/hltrader/runners/live.py`. Its
expected wiring, to be validated against NautilusTrader 1.231.0 before implementation, is:

```text
TradingNode
    ↓
HyperliquidDataClientConfig
HyperliquidExecClientConfig
    ↓
ShortBtcRsiStrategy
```

That future runner must remain fail-closed when account identity, environment, margin evidence,
reconciliation, secrets, or explicit order authorization are absent.

## Ordered milestones

1. Create and authorize a distinct agent on Hyperliquid testnet.
2. Run the `updateLeverage` dry-run and review its local plan.
3. Perform only the separately authorized, guarded and one-shot signed `updateLeverage` spike.
4. Close the bootstrap proof from its authoritative or ambiguous result.
5. Implement the fail-closed live/testnet Nautilus runner.
6. Start `TradingNode` with order submission disabled.
7. Verify market data and startup reconciliation without trading.
8. Execute a first testnet cycle with separately approved minimal notional.
9. Run Phase 3.5 crash/restart drills.
10. Observe WebSocket and order lifecycle behavior for a prolonged period.
11. Consider a mainnet canary only after all required proofs are closed.

Each mutation, order-submission step, and mainnet step requires its own authorization. Progress in
an earlier milestone does not implicitly authorize a later one.

## Backtest evidence boundary

The following statements must remain separate:

```text
mark_price_as_venue_trigger_rule = DOCUMENTED_CONFIRMED
BacktestEngine native mark-price equivalence = UNVERIFIABLE
```

Hyperliquid documents mark price as the venue trigger rule. In the pinned NautilusTrader 1.231.0
probe, `MarkPriceUpdate` reaches the strategy, but the simulated `STOP_MARKET` does not trigger
when only mark price crosses the threshold. Phase 2 therefore does not establish full fidelity to
Hyperliquid. Quote, OHLC and last trade data must never be substituted silently for mark price.

