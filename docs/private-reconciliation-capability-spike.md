# Hyperliquid private reconciliation capability spike

## Verdict

```text
SAFE_WRAPPER_FEASIBLE
```

NautilusTrader 1.231.0 has no native private read-only mode. Its official Hyperliquid execution
client combines report queries, private/user subscriptions and venue commands in one class over
one HTTP/WS client. However, the report and subscription paths can identify an account from an
explicit public `account_address` without a signer. A structural wrapper can therefore preserve
the official read/reconciliation implementation while overriding every command entry point.

The wrapper is implemented and tested as a candidate boundary, but it is not registered in
`hnt-live-observe`. No private connection or live reconciliation was performed.

## Inspected NautilusTrader 1.231.0 paths

- `adapters/hyperliquid/config.py`: `HyperliquidExecClientConfig` has no `read_only` flag.
- `adapters/hyperliquid/factories.py`: the official factory resolves the execution account and
  creates one `HyperliquidHttpClient` used by `HyperliquidExecutionClient`.
- `adapters/hyperliquid/execution.py`: `_connect` loads account state and subscribes to order
  updates/user events; report methods call `request_*`; command methods call WS/HTTP mutation
  methods.
- `live/execution_client.py`: public `submit_order`, `modify_order` and cancellation methods
  schedule their protected mutation coroutines. `generate_mass_status` combines orders, fills and
  positions for startup reconciliation.
- `live/execution_engine.py`: startup reconciliation calls `generate_mass_status`; continuous
  reconciliation reads reports. Synthetic reconciliation orders are local cache/event constructs,
  not venue submissions.
- `execution/engine.pyx`: strategy commands are routed to the registered execution client's public
  submit/modify/cancel methods.

## Capability map

| Capability | Data client | Exec client | Requires signer for read | Can mutate venue |
|---|---:|---:|---:|---:|
| public quotes | yes | no | no | no |
| bars | yes | no | no | no |
| account state | no | yes | no, with explicit account address | no |
| position reports | no | yes | no, with explicit account address | no |
| order status/open orders | no | yes | no, with explicit account address | no |
| fill reports | no | yes | no, with explicit account address | no |
| order updates/user events WS | no | yes | no; subscription uses account address | no |
| submit order/list | no | yes | yes for an effective venue command | yes |
| modify order | no | yes | yes for an effective venue command | yes |
| cancel/batch/cancel-all | no | yes | yes for an effective venue command | yes |
| split/merge/negate outcome helpers | no | yes | yes for an effective venue command | yes |

The “exec client” column describes the official class boundary, not a claim that all listed reads
are cryptographically private. Hyperliquid exposes the relevant account reads by address. In
Nautilus they are nevertheless delivered through the execution adapter and execution reports.

## Alternatives evaluated

### A. Native read-only mode

Rejected for 1.231.0. Neither `HyperliquidExecClientConfig`, the factory, the execution client nor
`LiveExecEngineConfig` exposes a flag which removes command methods while retaining reports.

```text
native_read_only_mode = NO
```

### B. Capability wrapper

Feasible as a candidate. `ReadOnlyHyperliquidExecutionClient` inherits official connection,
subscription and report methods, but overrides:

- all six public command entry points used by `ExecutionEngine`;
- their six protected mutation coroutines;
- four additional split/merge/negate mutation helpers.

`ReadOnlyHyperliquidExecClientFactory` additionally requires testnet and an explicit
`account_address`, rejects config credentials, vaults and credential environment variables, and
constructs a dedicated non-cached HTTP client. It never reuses the official process-global cached
client, which could previously have been created with a signer.

The wrapper currently raises `ReadOnlyCapabilityError` synchronously before a public command can
schedule a transport coroutine. This is fail-closed, although the effect of that exception on a
full live engine lifecycle remains to be tested before integration.

### C. Separate private reader

Technically possible using address-based Hyperliquid info calls, but not selected. It would require
reimplementing or adapting Nautilus report parsing and feeding the execution reconciliation API.
The wrapper reuses the official adapter's account state, reports and subscriptions with a smaller
divergence.

## Threat model

| Scenario | Candidate barrier | Remaining proof |
|---|---|---|
| strategy bug calls `submit_order` | public wrapper override raises before task/transport | full node command-queue behavior |
| late timer submits during startup | same public override | deterministic node lifecycle test |
| reconciliation emits a command | Nautilus reconciliation itself applies local reports/events; any later strategy command meets wrapper | startup with real report fixtures/live account |
| Nautilus retry path submits automatically | mutation coroutine never entered, so its retry/WS-post path cannot start | regression audit on Nautilus upgrades |
| existing journal restores `EMERGENCY_EXIT` | flatten reaches blocked public submit method | resulting state/error handling in wired node |
| open exposure discovered at startup | reports can populate cache; any protection/flatten command is blocked | safe operator outcome and no queue-task loss |

## Invariants and limits

```text
PRIVATE_RECONCILIATION
MUST NOT imply
COMMAND_CAPABILITY
```

Locally proven:

- explicit account identity resolves without a signer;
- official report implementations remain inherited unchanged;
- every identified public and internal Hyperliquid mutation entry point is overridden;
- factory rejects mainnet, signer, vault, missing account and credential-bearing environment.

Not proven:

- real account state/report responses without a signer on testnet;
- WebSocket user subscriptions against a real account;
- full startup reconciliation with the wrapper registered in `TradingNode`;
- behavior of the execution command queue after a blocked command;
- completeness of the mutation inventory after a NautilusTrader upgrade.

For those reasons the candidate factory is not wired into the runner yet.

