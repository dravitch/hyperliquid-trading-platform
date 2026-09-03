# Hyperliquid testnet roadmap

This roadmap separates the administrative bootstrap proof from the future Nautilus strategy
runtime. Completing the bootstrap does not start `ShortBtcRsiStrategy` and does not authorize an
order.

## A. Administrative bootstrap spike

```text
mainnet prerequisite for the same master
        ↓
testnet faucet eligibility and funding
        ↓
distinct authorized testnet agent
        ↓
public userRole(agent) → master verification
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

1. Satisfy and verify the mainnet prerequisite required by the faucet for the same master address.
2. Fund the testnet account and confirm that the funds are effectively available.
3. Create and authorize a distinct agent on Hyperliquid testnet.
4. Verify publicly that `userRole(agent)` returns `role=agent` and the expected master in
   `data.user`.
5. Set only the public `HYPERLIQUID_AGENT_ADDRESS` for the dry-run.
6. Run the `updateLeverage` dry-run and review its local plan.
7. Perform only the separately authorized, guarded and one-shot signed `updateLeverage` spike,
   then close the bootstrap proof from its authoritative or ambiguous result.
8. Implement the fail-closed live/testnet Nautilus runner.
9. Start `TradingNode` with order submission disabled.
10. Verify market data and startup reconciliation without trading.
11. Execute a first testnet cycle with separately approved minimal notional.
12. Run Phase 3.5 crash/restart drills.
13. Observe WebSocket and order lifecycle behavior for a prolonged period.
14. Consider a mainnet canary only after all required proofs are closed.

Each mutation, order-submission step, and mainnet step requires its own authorization. Progress in
an earlier milestone does not implicitly authorize a later one.

The mainnet prerequisite in step 1 is an operator activation required for testnet faucet
eligibility. It is not a mainnet strategy-runtime milestone: `HLTRADER_MAINNET_ENABLED=false`
remains mandatory and no mainnet order is authorized.

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
