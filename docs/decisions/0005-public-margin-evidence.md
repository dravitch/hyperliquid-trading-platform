# ADR 0005: Public Hyperliquid margin evidence

## Status

Accepted — 2026-09-01.

## Context

REV3.1 requires exact leverage and isolated-margin verification before entry. The public
`clearinghouseState` response exposes `assetPositions[].position.coin`, signed size `szi`, and
`leverage.type` / `leverage.value` for an existing position. It does not expose a pre-position
margin-mode and leverage setting for an absent asset position.

The response body also does not echo the queried account address or API environment. Those values
can be bound to the transport request and receipt, but cannot be independently authenticated from
the response body. Hyperliquid explicitly warns that querying an agent address instead of the
master/sub-account address returns an empty result.

## Decision

The read-only verifier uses `clearinghouseState` and classifies evidence as:

- `VERIFIED` only for one uniquely matching, non-zero position whose observed leverage type and
  value exactly match the requested mode and leverage;
- `MISMATCH` when bound account/environment/instrument context or observable margin values differ;
- `UNVERIFIABLE` when the position is absent, required fields are absent or malformed, numeric
  values are invalid, evidence is stale, transport fails, or the response is ambiguous.

Transport binds the requested account and environment to the observation. It does not claim that
the response cryptographically proves account ownership. Parsing and classification remain pure
and independently testable. No endpoint capable of changing margin, leverage, or orders is used.

## Consequences

- A flat BTC account cannot produce a `VERIFIED` receipt through this public endpoint.
- The strategy can enter only when an already-observable BTC position supplies matching evidence.
  This creates a deliberate bootstrap blocker for a strategy that requires verification before
  opening its first position.
- Resolving that bootstrap tension requires a separate product/risk decision or a stronger public
  source; it is not bypassed in this milestone.
- Receipt freshness uses the local UTC observation time at successful response parsing. The API
  response's optional `time` is not relied upon for the contract.

## Sources

- Hyperliquid, “Info endpoint — Perpetuals”, `clearinghouseState` schema.
- Hyperliquid, “Info endpoint”, account-address warning.
- Hyperliquid, “Margining”, position margin mode and leverage behavior.
