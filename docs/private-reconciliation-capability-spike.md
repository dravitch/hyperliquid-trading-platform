# Hyperliquid account-state reconciliation capability spike

## Verdict

```text
RECONCILIATION_WRAPPER_ENGINE_SAFE
```

For NautilusTrader 1.231.0, the reconciliation-only wrapper blocks every identified command
before either Hyperliquid transport and settles the real `LiveRiskEngine` / `LiveExecutionEngine`
path deterministically. This is a local, disconnected proof. It does not prove testnet account
responses or WebSocket behavior.

The original synchronous `ReadOnlyCapabilityError` at the public methods was rejected: the
`ExecutionEngine` catches the exception and keeps its queue alive, but the submitted order has
already entered the cache as `INITIALIZED`. That leaves a false non-terminal local order. Public
methods now emit Nautilus's official `OrderDenied`, `OrderModifyRejected` or
`OrderCancelRejected` events. Protected mutation coroutines still raise as defense in depth.

## Inspected NautilusTrader 1.231.0 paths

- `HyperliquidExecClientConfig` has no read-only flag.
- The official factory creates one `HyperliquidHttpClient` shared by account-state reports and
  venue mutations.
- `HyperliquidExecutionClient._connect` loads account state and subscribes by explicit account
  address; report methods use `request_*`; mutations use the HTTP/WS command methods.
- `LiveExecutionEngine.execute` enqueues commands. Its command loop catches client exceptions,
  but an exception does not synthesize an order terminal event.
- `ExecutionEngine._execute_command` adds a submitted order to the cache before calling the
  execution client's public `submit_order` method.
- Startup reconciliation calls `generate_mass_status`; reconciliation reports update local cache
  and events. They are not venue mutations.

## Capability map

| Capability | Data client | Exec client | Signer required | Can mutate venue |
|---|---:|---:|---:|---:|
| public quotes / bars | yes | no | no | no |
| account state | no | yes | no, with explicit account address | no |
| position reports | no | yes | no, with explicit account address | no |
| order status/open orders | no | yes | no, with explicit account address | no |
| fill reports | no | yes | no, with explicit account address | no |
| account-scoped WS events | no | yes | no; subscription is address-scoped | no |
| submit / modify / cancel | no | yes | yes for an effective command | yes |
| split / merge / negate helpers | no | yes | yes for an effective command | yes |

These are `ACCOUNT_STATE_READS` or `ACCOUNT_SCOPED_READS`: Hyperliquid exposes them by address;
they are not authenticated private actions. “Private authenticated action” is reserved here for a
path using an agent wallet signer.

## Engine proof

The disconnected integration harness constructs a real `TradingNode`, real risk and execution
engines, the real wrapper, and a registered Nautilus `Strategy`. Both underlying `_client` and
`_ws_client` are replaced only in the test by a mutation canary which raises
`MUTATION TRANSPORT REACHED` on any call.

Observed command results:

| Intent | Local result | Queue result | Transport calls |
|---|---|---|---:|
| submit | `OrderDenied`; cached order becomes `DENIED` | settled | 0 |
| modify | `OrderModifyRejected`; working order remains `ACCEPTED` | settled | 0 |
| cancel | `OrderCancelRejected`; working order remains `ACCEPTED` | settled | 0 |
| cancel all | cancel rejection for each matching local order | settled | 0 |
| batch cancel | cancel rejection for each item | settled | 0 |

After drain and an additional observation interval:

```text
risk command queue = 0
risk event queue = 0
execution command queue = 0
execution event queue = 0
reconciliation retry counters = 0
position retry counters = 0
queue tasks = alive
mutation canary calls = 0
```

Queue sizes and retry counters require pinned Nautilus 1.231.0 internals because no equivalent
public inspection API exists.

## Startup lifecycle scenarios

Deterministic `PositionStatusReport` and `OrderStatusReport` objects are passed through the real
execution reconciliation interface before `ShortBtcRsiStrategy` starts:

| Observed state / journal | Result | Command boundary |
|---|---|---|
| flat, no journal | `NEVER_ENTERED` | no command |
| short + exact accepted protector | `OPEN` | no command |
| short + partial accepted protector | modify denied, then `EMERGENCY_EXIT` | wrapper blocks modify; risk denies flatten; zero transport |
| `EXITING` + shortfall | `RECOVERY_REQUIRED` | reduce-only flatten denied locally; zero transport |
| `EMERGENCY_EXIT` + exposure | remains `EMERGENCY_EXIT` | reduce-only flatten denied locally; zero transport |
| `RECOVERY_REQUIRED` | remains absorbing | no command |

`ShortBtcRsiStrategy.on_order_denied` removes denied flatten quantities from
`_flatten_outstanding`. A normal exit denial becomes `RECOVERY_REQUIRED`; an emergency flatten
denial preserves the absorbing `EMERGENCY_EXIT`. Protective submit/modify denial enters
`EMERGENCY_EXIT` and cancels its watchdog. Replaying the late timeout or the exposure-sync
callback is inert. No case claims a venue command is pending after local denial.

## Mutation inventory and upgrade policy

The wrapper pins the 16 identified Hyperliquid mutation methods in
`NAUTILUS_HYPERLIQUID_MUTATION_METHODS`. A test derives the adapter methods matching the known
submit/modify/cancel/split/merge/negate families and requires exact equality. Any change in those
families after a Nautilus upgrade fails CI and requires a fresh source audit before updating the
set. This guard is deliberately bounded; it does not claim to infer every possible future
mutation from semantics alone.

## Remaining limits

Locally proven:

- real Strategy → RiskEngine → ExecutionEngine → wrapper command flow;
- official reconciliation reports feeding position and protector state;
- zero HTTP/WS mutation calls for all tested commands and lifecycle resumptions;
- deterministic queue drain, no retries and no false submitted/outstanding state;
- factory still refuses signer, vault, credential environment, mainnet and missing account.

Not proven:

- real testnet account-state report contents without a signer;
- account-scoped WebSocket subscription and reconnect behavior;
- startup mass-status timing and ordering against a real account;
- long-running queue behavior under real WS concurrency;
- semantic completeness of the mutation inventory after a future adapter redesign.

The wrapper remains unwired from `hnt-live-observe` until a separately authorized disconnected
runner-integration milestone applies the decision in ADR 0007.
