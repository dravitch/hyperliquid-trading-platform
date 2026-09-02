from __future__ import annotations

import argparse
import json
import os
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

from hltrader.risk.bootstrap_margin import (
    BootstrapExpectation,
    BootstrapMarginReceipt,
    UpdateLeverageCommand,
    perform_bootstrap_attempt,
    save_bootstrap_receipt,
)
from hltrader.risk.guard import MarginMode

INSTRUMENT_ID = "BTC-USD-PERP.HYPERLIQUID"
COIN = "BTC"
DEFAULT_LEVERAGE = 3
MUTATION_GUARD_ENV = "HLTRADER_ALLOW_TESTNET_MARGIN_MUTATION"
SDK_VERSION = "0.24.0"


class SpikeConfigurationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class UpdateLeverageSpikePlan:
    session_id: str
    account_address: str
    signer_address: str
    environment: str
    instrument_id: str
    coin: str
    asset: int
    margin_mode: MarginMode
    leverage: int
    execute: bool

    def __post_init__(self) -> None:
        if self.environment != "testnet":
            raise SpikeConfigurationError("updateLeverage spike is testnet-only")
        if self.asset < 0:
            raise SpikeConfigurationError("asset index cannot be negative")
        if self.leverage <= 0:
            raise SpikeConfigurationError("leverage must be positive")

    def action(self) -> dict[str, str | int | bool]:
        return {
            "type": "updateLeverage",
            "asset": self.asset,
            "isCross": self.margin_mode is MarginMode.CROSS,
            "leverage": self.leverage,
        }

    def public_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["margin_mode"] = self.margin_mode.value
        payload["action"] = self.action()
        payload["sdk_version"] = SDK_VERSION
        return payload


def require_mutation_guard(*, execute: bool, guard_value: str | None) -> None:
    """Require two independent operator actions before any signed mutation can occur."""
    if not execute:
        return
    if guard_value != "true":
        raise SpikeConfigurationError(
            f"--execute requires {MUTATION_GUARD_ENV}=true; no mutation was sent"
        )


def build_plan(
    *,
    account_address: str,
    signer_address: str,
    asset: int,
    leverage: int = DEFAULT_LEVERAGE,
    execute: bool = False,
    session_id: str | None = None,
) -> UpdateLeverageSpikePlan:
    return UpdateLeverageSpikePlan(
        session_id=session_id or str(uuid4()),
        account_address=account_address,
        signer_address=signer_address,
        environment="testnet",
        instrument_id=INSTRUMENT_ID,
        coin=COIN,
        asset=asset,
        margin_mode=MarginMode.ISOLATED,
        leverage=leverage,
        execute=execute,
    )


def expectation_from_plan(plan: UpdateLeverageSpikePlan) -> BootstrapExpectation:
    return BootstrapExpectation(
        session_id=plan.session_id,
        account_address=plan.account_address,
        signer_address=plan.signer_address,
        environment=plan.environment,
        instrument_id=plan.instrument_id,
        coin=plan.coin,
        asset=plan.asset,
        margin_mode=plan.margin_mode,
        leverage=Decimal(plan.leverage),
    )


def command_from_plan(plan: UpdateLeverageSpikePlan, *, nonce: int) -> UpdateLeverageCommand:
    return UpdateLeverageCommand(
        session_id=plan.session_id,
        account_address=plan.account_address,
        signer_address=plan.signer_address,
        environment=plan.environment,
        instrument_id=plan.instrument_id,
        coin=plan.coin,
        asset=plan.asset,
        is_cross=plan.margin_mode is MarginMode.CROSS,
        leverage=plan.leverage,
        nonce=nonce,
    )


def execute_plan(
    plan: UpdateLeverageSpikePlan,
    *,
    nonce: int,
    observed_at: datetime,
    submit: Callable[[dict[str, str | int | bool], int], Any],
) -> BootstrapMarginReceipt:
    if not plan.execute:
        raise SpikeConfigurationError("refusing mutation because plan.execute is false")
    return perform_bootstrap_attempt(
        expectation_from_plan(plan),
        command_from_plan(plan, nonce=nonce),
        observed_at=observed_at,
        submit=submit,
    )


def _load_official_sdk() -> tuple[Any, Any, Any, Any, Any]:
    try:
        import eth_account
        from hyperliquid.exchange import Exchange
        from hyperliquid.info import Info
        from hyperliquid.utils import constants
        from hyperliquid.utils.signing import get_timestamp_ms, sign_l1_action
    except ImportError as exc:
        raise SpikeConfigurationError(
            "official Hyperliquid SDK is required only for the live spike; run with "
            f"`uv run --with hyperliquid-python-sdk=={SDK_VERSION} ...`"
        ) from exc
    return eth_account, Exchange, Info, constants, (get_timestamp_ms, sign_l1_action)


def discover_testnet_asset_index(coin: str = COIN) -> int:
    """Resolve the venue asset index from testnet metadata without signing anything."""
    _, _, Info, constants, _ = _load_official_sdk()
    info = Info(constants.TESTNET_API_URL, skip_ws=True)
    asset = info.name_to_asset(coin)
    if not isinstance(asset, int) or asset < 0:
        raise SpikeConfigurationError(f"unexpected asset index for {coin}: {asset!r}")
    return asset


def make_testnet_submitter(
    *,
    private_key: str,
    account_address: str,
    expected_signer_address: str,
) -> tuple[Callable[[dict[str, str | int | bool], int], Any], Callable[[], int]]:
    """Build an exact-nonce testnet L1 transport using the official SDK signing primitives."""
    eth_account, Exchange, _, constants, signing = _load_official_sdk()
    get_timestamp_ms, sign_l1_action = signing
    wallet = eth_account.Account.from_key(private_key)
    if wallet.address.lower() != expected_signer_address.lower():
        raise SpikeConfigurationError(
            "HYPERLIQUID_TESTNET_PK does not match HYPERLIQUID_AGENT_ADDRESS"
        )
    exchange = Exchange(
        wallet=wallet,
        base_url=constants.TESTNET_API_URL,
        account_address=account_address,
    )

    def submit(action: dict[str, str | int | bool], nonce: int) -> Any:
        signature = sign_l1_action(wallet, action, None, nonce, None, False)
        payload = {
            "action": action,
            "nonce": nonce,
            "signature": signature,
            "vaultAddress": None,
            "expiresAfter": None,
        }
        return exchange.post("/exchange", payload)

    return submit, get_timestamp_ms


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SpikeConfigurationError(f"missing required environment variable: {name}")
    return value


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Controlled Hyperliquid testnet updateLeverage spike. Never submits trades."
    )
    parser.add_argument("--execute", action="store_true", help="send one signed testnet mutation")
    parser.add_argument("--asset", type=int, default=None, help="expected BTC asset index")
    parser.add_argument("--leverage", type=int, default=DEFAULT_LEVERAGE)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/testnet/update-leverage-spike.json"),
    )
    parser.add_argument(
        "--receipt",
        type=Path,
        default=Path("var/run/bootstrap-margin-receipt.json"),
    )
    args = parser.parse_args(argv)

    require_mutation_guard(
        execute=args.execute,
        guard_value=os.environ.get(MUTATION_GUARD_ENV),
    )
    account_address = _require_env("HYPERLIQUID_ACCOUNT_ADDRESS")
    signer_address = _require_env("HYPERLIQUID_AGENT_ADDRESS")
    discovered_asset = discover_testnet_asset_index()
    if args.asset is not None and args.asset != discovered_asset:
        raise SpikeConfigurationError(
            f"configured asset index {args.asset} differs from testnet metadata {discovered_asset}"
        )

    plan = build_plan(
        account_address=account_address,
        signer_address=signer_address,
        asset=discovered_asset,
        leverage=args.leverage,
        execute=args.execute,
    )
    report: dict[str, object] = {
        "plan": plan.public_dict(),
        "mutation_sent": False,
        "receipt_status": None,
        "note": "dry-run only; no signed mutation sent",
    }

    if args.execute:
        private_key = _require_env("HYPERLIQUID_TESTNET_PK")
        submit, get_timestamp_ms = make_testnet_submitter(
            private_key=private_key,
            account_address=account_address,
            expected_signer_address=signer_address,
        )
        nonce = int(get_timestamp_ms())
        observed_at = datetime.now(UTC)
        receipt = execute_plan(
            plan,
            nonce=nonce,
            observed_at=observed_at,
            submit=submit,
        )
        save_bootstrap_receipt(args.receipt, receipt)
        report.update(
            {
                "mutation_sent": True,
                "nonce": nonce,
                "receipt_status": receipt.status.value,
                "response_type": receipt.response_type,
                "reason": receipt.reason,
                "receipt_path": str(args.receipt),
                "note": "one updateLeverage mutation attempted; no trading order submitted",
            }
        )

    _write_json(args.output, report)
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
