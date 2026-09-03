# Controlled Hyperliquid testnet `updateLeverage` spike

This spike exists to prove one narrow fact before wiring the full testnet runner:

> Can the configured, approved API/agent wallet successfully execute one `updateLeverage`
> mutation for BTC on Hyperliquid testnet while the account is flat?

It does **not** submit a trading order and it cannot target mainnet.

This is the administrative bootstrap path, not the strategy runtime. The repository does not yet
contain a live Nautilus runner which instantiates `TradingNode` with Hyperliquid data/execution
clients and `ShortBtcRsiStrategy`. The two tracks and their ordered milestones are documented in
[`TESTNET_ROADMAP.md`](TESTNET_ROADMAP.md).

## Safety properties

- Dry-run is the default.
- `--execute` is required before a signed mutation is possible.
- `HLTRADER_ALLOW_TESTNET_MARGIN_MUTATION=true` is also required.
- The private key must match `HYPERLIQUID_AGENT_ADDRESS`.
- The runner always uses Hyperliquid testnet.
- BTC asset index is resolved from testnet metadata at runtime.
- Supplying `--asset` acts only as an assertion: a mismatch aborts before signing.
- The mutation is attempted once. Timeout or transport ambiguity becomes `UNVERIFIABLE`; there
  is no automatic retry.
- No order-submission API is called by this runner.
- Mainnet remains independently locked by `HLTRADER_MAINNET_ENABLED=false`.

Generated artifacts live under ignored `artifacts/` and `var/` paths.

## SDK isolation

The repository does not add the official Hyperliquid SDK to its permanent dependency set during
this spike. Run the command with the exact temporary SDK version:

```bash
uv run --with hyperliquid-python-sdk==0.24.0 hnt-update-leverage-spike
```

The SDK is used for testnet metadata, wallet parsing and L1 signing only when the runner executes.

## 1. Dry-run

Required public environment variables:

```text
HYPERLIQUID_ACCOUNT_ADDRESS=<master account address>
HYPERLIQUID_AGENT_ADDRESS=<approved testnet agent address>
```

Then run:

```bash
uv run --with hyperliquid-python-sdk==0.24.0 hnt-update-leverage-spike
```

Expected properties:

```text
mutation_sent = false
plan.environment = testnet
plan.coin = BTC
plan.margin_mode = isolated
plan.leverage = 3
```

The runner resolves the BTC asset index from testnet metadata without signing.

## 2. Preconditions before the controlled mutation

Do not execute until all are true:

1. `HYPERLIQUID_ACCOUNT_ADDRESS` is the intended master account.
2. `HYPERLIQUID_AGENT_ADDRESS` is a distinct API/agent wallet authorized for that account on
   testnet.
3. `HYPERLIQUID_TESTNET_PK` is the private key for that agent wallet, never the master key.
4. The account is intentionally flat for BTC.
5. No trading runner is active.
6. `HLTRADER_MAINNET_ENABLED=false` remains set.
7. The dry-run report is reviewed.

## 3. One controlled mutation

Arm the second guard only for this shell invocation:

```bash
HLTRADER_ALLOW_TESTNET_MARGIN_MUTATION=true \
uv run --with hyperliquid-python-sdk==0.24.0 \
  hnt-update-leverage-spike --execute --leverage 3
```

The runner performs exactly one signed `updateLeverage` attempt with isolated margin
(`isCross=false`).

A successful authoritative response is classified through the existing
`BootstrapMarginReceipt` contract. The expected positive status is `CONFIGURED`.

The default receipt path is:

```text
var/run/bootstrap-margin-receipt.json
```

The audit report is:

```text
artifacts/testnet/update-leverage-spike.json
```

## 4. Interpretation

### `CONFIGURED`

Hyperliquid returned the exact response already accepted by ADR 0006 for the exact bound command.
This proves command acceptance, not a separately readable flat-account leverage state.

### `MISMATCH`

The venue rejected the command or the command/expectation binding diverged. Do not continue to
first-entry testing.

### `UNVERIFIABLE`

The outcome is ambiguous, including timeout, disconnect or malformed/unexpected response. Do not
retry automatically and do not authorize entry.

## 5. Evidence to record

After the spike, record in the next ADR / `PROGRESSION.md` update:

```text
sdk_version:
testnet_asset_index_btc:
signer_is_agent:
account_flat_before_mutation:
response_shape:
bootstrap_status:
nonce:
mutation_attempt_count:
trading_orders_submitted: 0
```

Do not record private keys or signatures.
