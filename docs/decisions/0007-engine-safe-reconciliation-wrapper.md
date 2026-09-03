# ADR 0007 — Engine-safe reconciliation-only Hyperliquid wrapper

## Status

Accepted for disconnected integration; live testnet behavior remains unproven.

## Context

NautilusTrader 1.231.0 combines account-state reports and venue commands in
`HyperliquidExecutionClient`. It has no native read-only mode. The first wrapper prototype raised
from public command methods. Although this prevented transport access, `ExecutionEngine` had
already cached submitted orders, so a caught exception could leave an `INITIALIZED` order with no
terminal event.

## Decision

Use the official Nautilus rejection event path at every public command boundary:

- submit/list → `OrderDenied`;
- modify → `OrderModifyRejected`;
- cancel, cancel-all and batch-cancel → `OrderCancelRejected` for affected orders.

Keep protected Hyperliquid mutation coroutines and split/merge/negate helpers overridden with
immediate exceptions as defense in depth. Continue to forbid all signer/vault inputs, credential
environment variables and mainnet. Call the retained read capability `ACCOUNT_STATE_READS`.

`ShortBtcRsiStrategy` consumes local denials explicitly: denied outstanding flatten quantities
are removed; `EXITING` becomes `RECOVERY_REQUIRED`; `EMERGENCY_EXIT` remains absorbing; protective
denial becomes `EMERGENCY_EXIT` and cancels the protection timer.

## Evidence

A disconnected test uses a real `TradingNode`, `LiveRiskEngine`, `LiveExecutionEngine`, wrapper
instance and registered strategy. It exercises submit, modify, cancel, cancel-all, batch-cancel,
partial protection convergence, late timeout, `EXITING` resume and emergency flatten. A canary on
both underlying transports records zero calls. All engine queues drain, retry counters remain
zero, queue tasks remain alive, and local order/lifecycle states settle deterministically.

## Consequences

- Verdict: `RECONCILIATION_WRAPPER_ENGINE_SAFE` for NautilusTrader 1.231.0 locally.
- The wrapper is suitable for the next disconnected runner-wiring milestone.
- This ADR does not authorize a key, network connection, testnet mutation, order or mainnet.
- Account-scoped WS behavior and real mass-status reconciliation remain testnet proofs.
- The mutation inventory test must fail on changes in known adapter mutation families; upgrades
  require source re-audit before the pinned set changes.
