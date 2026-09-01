# ADR 0004: Venue-first restart reconciliation

## Status

Accepted — 2026-09-01.

## Context

The lifecycle journal persisted only the latest exit order identifier, while the
strategy tracked pending flatten quantities only in `_flatten_outstanding` memory.
After a process restart, reconciliation also collapsed every non-terminal journal
state with exposure into `OPEN` or `PROTECTING`. This lost an existing `EXITING` or
`EMERGENCY_EXIT` intent and made an already-open reduce-only exit invisible to the
shortfall calculation.

Those two behaviors can cause either a duplicate flatten or an abandoned exit.
Persisting the in-memory quantity would not solve the problem because fills and
cancellations can advance at the venue while the process is stopped.

## Decision

The journal persists exit order identifiers as run identity and intent, including
all identifiers created while one run is closing. Quantities are never restored
from the journal.

At restart, the lifecycle is reconstructed from:

1. the current venue/cache short position;
2. open reduce-only BUY exit orders whose identifiers belong to the journaled run;
3. the current venue/cache protective trigger;
4. the journaled lifecycle intent.

The remaining close quantity is always:

`actual exposure - open journal-linked reduce-only exit leaves quantity`.

An open reduce-only BUY exit not attributable to the run, multiple conflicting
identities, or an outstanding quantity greater than exposure is a state conflict.
No automatic order is submitted in that condition.

For a journaled `EXITING` or `EMERGENCY_EXIT`, a missing exit order with remaining
exposure authorizes automatic submission of exactly the observed shortfall. This
does not create a new trading decision: it resumes an already-persisted close
intent. `EMERGENCY_EXIT` remains absorbing. A zero position and no relevant open
exit completes either close intent as `CLOSED_FINAL`.

For `PROTECTING`, `OPEN` is reconstructed only when one uniquely identified native
protector covers the observed exposure exactly. Partial protection remains
`PROTECTING`. A journaled protector absent from venue/cache is ambiguous and fails
closed as `STATE_CONFLICT`.

`RECOVERY_REQUIRED` is preserved across restart. A zero position alone does not
clear it because unresolved entry or order intent may still exist outside the
limited snapshot.

Two equivalent reconciliation calls are idempotent: venue-observed open order
leaves quantities suppress duplicate submissions, and terminal/conflict states do
not emit actions.

## Consequences

- Venue/cache owns economic quantities; the journal owns identity and intent.
- The journal schema remains backward compatible with its former single
  `exit_order` field.
- Restart safety depends on Nautilus cache fidelity for open-order identity,
  reduce-only flags, leaves quantity, and position quantity. Those properties still
  require testnet validation later.
- Ambiguity deliberately requires manual recovery instead of guessing ownership.
