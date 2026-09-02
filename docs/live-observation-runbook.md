# Nautilus testnet observation runner

## Purpose

`hnt-live-observe` assembles NautilusTrader 1.231.0, the public Hyperliquid testnet data client,
and `ShortBtcRsiStrategy` while making execution unavailable.

The supported mode is exactly:

```text
DATA_ONLY
environment = testnet
enable_order_submission = false
exec_clients = {}
```

Mainnet and `EXECUTION_CAPABLE` are unsupported even if
`HLTRADER_MAINNET_ENABLED=true`. The runner rejects a populated `HYPERLIQUID_TESTNET_PK` rather
than allowing a secret to change its capability.

## Local wiring check

From the repository root:

```bash
uv run hnt-live-observe --check
```

This constructs `TradingNode`, loads the existing YAML configuration, instantiates
`ShortBtcRsiStrategy`, registers `HyperliquidLiveDataClientFactory`, builds the public data client,
and disposes the node. It does not connect a WebSocket, sign a request, mutate leverage, or submit
an order.

Expected result:

```text
configuration wiring = PROVEN_LOCALLY
live websocket behavior = TO_PROVE_TESTNET
```

Without `--check`, the command may connect only to public Hyperliquid testnet market data. It is
intended to observe the BTC perpetual instrument, quote ticks and external daily bars requested by
the strategy. No claim is made yet about prolonged WebSocket stability or venue event ordering.

## Reconciliation boundary

Hyperliquid's execution client resolves an execution account from private signing identity during
construction. It is also the path which supplies private orders, positions and account events to
Nautilus reconciliation. It is deliberately absent here.

Consequences:

- a missing journal plus an empty local cache initializes the strategy as `NEVER_ENTERED`;
- `enable_order_submission=false` prevents the flat strategy from entering;
- this proves local startup semantics, not that the venue account is economically flat;
- an existing journal blocks startup because data-only mode cannot reconcile it against private
  venue state;
- `EXECUTION_CAPABLE` requires a future, separately authorized runner and tests proving that its
  reconciliation cannot produce an unintended order.

The observation runner therefore proves configuration wiring locally. Private venue
reconciliation remains `TO_PROVE_TESTNET`.

## Local operator status

`hnt-status` is a read-only view of local orchestration evidence:

```bash
uv run hnt-status \
  --journal var/run/short_btc_rsi.json \
  --audit var/log/short_btc_rsi.audit.jsonl
```

It prints the journal state and, when available, quantities and the last event from the final
JSONL audit record. Missing files produce `NEVER_ENTERED` with unknown quantities and do not create
any files. `RECOVERY_REQUIRED` and `STATE_CONFLICT` set `recovery_required: true`.

The existing strategy persists its run journal. Automatic production of the configured audit
JSONL by the live strategy is not wired in this milestone; absent audit evidence is displayed as
`-`, never fabricated.

## Two complementary operator views

Hyperliquid UI is the venue-side source for:

- positions;
- open orders;
- PnL;
- funding;
- venue-side exposure.

`hnt-status` is the local orchestration view for:

- `PROTECTING`, `OPEN`, `EMERGENCY_EXIT`, `RECOVERY_REQUIRED` and `STATE_CONFLICT`;
- local exit reasons such as `rsi_exit`, `price_exit` and `emergency_exit`;
- the latest locally recorded event and quantities when an audit trace exists.

Neither view silently replaces the other. A combined multi-wallet or multi-strategy dashboard is
Phase 5/post-MVP work and is not part of this runner.

