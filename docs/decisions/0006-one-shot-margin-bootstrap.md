# ADR 0006: One-shot margin bootstrap from `updateLeverage`

## Status

Accepted — 2026-09-02.

## Context and API characterization

Hyperliquid documents the signed L1 action:

```json
{"type":"updateLeverage","asset":0,"isCross":false,"leverage":3}
```

`asset` is the coin index, `isCross=false` selects isolated margin, and `leverage` is a
venue-constrained integer. The documented successful exchange response is
`{"status":"ok","response":{"type":"default"}}`. Hyperliquid's API-server documentation says
the server waits for inclusion in a committed L1 block and returns the L1 execution response.
Thus the exact success response proves execution acceptance of this configuration command. It
does not prove a separately readable pre-position state.

The official Python SDK implements exactly this action in `Exchange.update_leverage`, signs it
with `sign_l1_action`, and supports agent/API wallets. API wallets sign on behalf of their master
account; public account queries must still use the master address. `account_address` is not part
of the `updateLeverage` wire action. `vaultAddress` is the separate mechanism for acting for a
vault/subaccount. This project initially supports only the explicitly configured master account,
with no vault/subaccount bootstrap.

The official Nautilus Hyperliquid integration says leverage must be set through the web UI or API
before trading; NautilusTrader 1.231.0 exposes no strategy-level leverage update operation.
Together with the venue action semantics, this documents pre-position use, but it remains
unverified empirically in this project until a controlled testnet mutation is authorized.

## Decision

Keep `MarginVerificationReceipt` unchanged in meaning: it represents an observed position from
`clearinghouseState`. Add a distinct `BootstrapMarginReceipt` representing only this proposition:

> Hyperliquid returned its exact committed-L1 success response for an `updateLeverage` command
> whose account binding, environment, instrument/coin, asset index, isolated/cross flag and
> leverage exactly match the requested first entry.

The bootstrap statuses are `CONFIGURED`, `MISMATCH`, and `UNVERIFIABLE`. Rejection is a mismatch;
malformed responses and transport/timeouts are unverifiable. Unknown outcomes are never retried
by this component, even though setting the same target is economically idempotent, because nonce,
signer authorization and routing may still be ambiguous.

The receipt is short-lived and consumed atomically for one concrete client order ID immediately
before `ENTERING` is persisted. Consumption writes `consumed_at` and `consumed_for_entry_id` under
an exclusive file lock. A consumed receipt cannot authorize another entry.

Restart policy is deliberately fail-closed:

- before consumption, a fresh receipt may be consumed only during the same process session in
  which the strategy started with `NEVER_ENTERED`;
- after consumption, restart cannot reuse it;
- a crash after consumption but before entry persistence sacrifices the receipt and requires a
  new explicit bootstrap command;
- a crash after entry persistence follows existing `ENTERING` reconciliation and cannot silently
  adopt an external position.

## Synchronous and ambiguous failures

Authoritative `status != ok`, signature/authorization errors, invalid leverage and invalid asset
are synchronous negative responses. A timeout, disconnect, invalid JSON, or loss of the response
after request transmission is ambiguous: the mutation may have committed, so the result is
`UNVERIFIABLE` and entry remains blocked.

## Consequences

- No absence of public evidence is reclassified as `clearinghouseState VERIFIED`.
- Generating a positive receipt requires a future signed testnet bootstrap component; this
  milestone implements only the response contract and one-shot strategy consumption.
- No trading order, leverage mutation, mainnet call, or testnet runner is introduced here.

## Sources

- Hyperliquid exchange endpoint, “Update leverage”.
- Hyperliquid API servers, committed-block response semantics.
- Hyperliquid “Nonces and API wallets”.
- Official `hyperliquid-python-sdk`, `Exchange.update_leverage` and agent setup.
- NautilusTrader Hyperliquid integration, account and position management.
