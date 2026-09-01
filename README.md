# Hyperliquid Trading Platform

Initial implementation of the pure, framework-independent domain described in
`hyperliquid-trading-platform-spec.md`.

The current milestone deliberately does **not** submit orders. It implements and tests the
exit rules, fixed-notional sizing, risk checks, protection invariant, restart reconciliation,
state machine, and durable run journal before coupling them to NautilusTrader.

## Development

Requires Python 3.11 or newer.

```bash
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
```

The sample configuration is fail-closed for live use: `deployment_enabled: false`. The price
threshold direction and notional must be reviewed before testnet deployment.

