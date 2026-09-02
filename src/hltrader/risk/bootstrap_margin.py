from __future__ import annotations

import fcntl
import json
import os
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from .guard import MarginMode


class BootstrapStatus(StrEnum):
    CONFIGURED = "CONFIGURED"
    MISMATCH = "MISMATCH"
    UNVERIFIABLE = "UNVERIFIABLE"


class BootstrapReceiptError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class BootstrapExpectation:
    session_id: str
    account_address: str
    signer_address: str
    environment: str
    instrument_id: str
    coin: str
    asset: int
    margin_mode: MarginMode
    leverage: Decimal

    def __post_init__(self) -> None:
        _validate_target(self)


@dataclass(frozen=True, slots=True)
class UpdateLeverageCommand:
    session_id: str
    account_address: str
    signer_address: str
    environment: str
    instrument_id: str
    coin: str
    asset: int
    is_cross: bool
    leverage: int
    nonce: int
    action_type: str = "updateLeverage"

    def __post_init__(self) -> None:
        _validate_identity(
            self.session_id,
            self.account_address,
            self.signer_address,
            self.environment,
            self.instrument_id,
            self.coin,
        )
        if self.asset < 0 or self.leverage <= 0 or self.nonce < 0:
            raise ValueError("invalid updateLeverage command values")

    def action(self) -> dict[str, str | int | bool]:
        return {
            "type": self.action_type,
            "asset": self.asset,
            "isCross": self.is_cross,
            "leverage": self.leverage,
        }


@dataclass(frozen=True, slots=True)
class BootstrapMarginReceipt:
    status: BootstrapStatus
    session_id: str
    account_address: str
    signer_address: str
    environment: str
    instrument_id: str
    coin: str
    asset: int
    expected_margin_mode: MarginMode
    expected_leverage: Decimal
    action_type: str
    action_is_cross: bool | None
    action_leverage: int | None
    nonce: int | None
    response_type: str | None
    observed_at: datetime
    reason: str
    consumed_at: datetime | None = None
    consumed_for_entry_id: str | None = None

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")
        if self.consumed_at is not None and self.consumed_at.tzinfo is None:
            raise ValueError("consumed_at must be timezone-aware")
        if not self.expected_leverage.is_finite() or self.expected_leverage <= 0:
            raise ValueError("expected leverage must be finite and positive")
        if self.asset < 0:
            raise ValueError("asset index cannot be negative")

    def matches(
        self,
        expectation: BootstrapExpectation,
        *,
        now: datetime,
        max_age_seconds: int,
    ) -> bool:
        age = now.astimezone(UTC) - self.observed_at.astimezone(UTC)
        return (
            self.status is BootstrapStatus.CONFIGURED
            and self.consumed_at is None
            and self.consumed_for_entry_id is None
            and self.session_id == expectation.session_id
            and self.account_address.lower() == expectation.account_address.lower()
            and self.signer_address.lower() == expectation.signer_address.lower()
            and self.environment == expectation.environment
            and self.instrument_id == expectation.instrument_id
            and self.coin == expectation.coin
            and self.asset == expectation.asset
            and self.expected_margin_mode is expectation.margin_mode
            and self.expected_leverage == expectation.leverage
            and self.action_type == "updateLeverage"
            and self.action_is_cross is (expectation.margin_mode is MarginMode.CROSS)
            and self.action_leverage == int(expectation.leverage)
            and self.response_type == "default"
            and 0 <= age.total_seconds() <= max_age_seconds
        )


def classify_bootstrap_response(
    expectation: BootstrapExpectation,
    command: UpdateLeverageCommand,
    response: Any,
    *,
    observed_at: datetime,
) -> BootstrapMarginReceipt:
    mismatches = []
    if command.session_id != expectation.session_id:
        mismatches.append("process session")
    if command.account_address.lower() != expectation.account_address.lower():
        mismatches.append("account address")
    if command.signer_address.lower() != expectation.signer_address.lower():
        mismatches.append("signer address")
    if command.environment != expectation.environment:
        mismatches.append("environment")
    if command.instrument_id != expectation.instrument_id or command.coin != expectation.coin:
        mismatches.append("instrument")
    if command.asset != expectation.asset:
        mismatches.append("asset index")
    expected_is_cross = expectation.margin_mode is MarginMode.CROSS
    if command.is_cross is not expected_is_cross:
        mismatches.append("margin mode")
    if Decimal(command.leverage) != expectation.leverage:
        mismatches.append("leverage")
    if command.action_type != "updateLeverage":
        mismatches.append("action type")

    response_type = None
    if isinstance(response, dict) and isinstance(response.get("response"), dict):
        raw_response_type = response["response"].get("type")
        if isinstance(raw_response_type, str):
            response_type = raw_response_type
    if mismatches:
        status = BootstrapStatus.MISMATCH
        reason = f"bootstrap command mismatch: {', '.join(mismatches)}"
    elif not isinstance(response, dict) or not isinstance(response.get("status"), str):
        status = BootstrapStatus.UNVERIFIABLE
        reason = "malformed updateLeverage response"
    elif response.get("status") != "ok":
        status = BootstrapStatus.MISMATCH
        reason = "Hyperliquid rejected updateLeverage"
    elif response.get("response") != {"type": "default"}:
        status = BootstrapStatus.UNVERIFIABLE
        reason = "unexpected successful updateLeverage response"
    else:
        status = BootstrapStatus.CONFIGURED
        reason = "Hyperliquid committed and accepted the exact updateLeverage command"
    return BootstrapMarginReceipt(
        status=status,
        session_id=expectation.session_id,
        account_address=expectation.account_address,
        signer_address=expectation.signer_address,
        environment=expectation.environment,
        instrument_id=expectation.instrument_id,
        coin=expectation.coin,
        asset=expectation.asset,
        expected_margin_mode=expectation.margin_mode,
        expected_leverage=expectation.leverage,
        action_type=command.action_type,
        action_is_cross=command.is_cross,
        action_leverage=command.leverage,
        nonce=command.nonce,
        response_type=response_type,
        observed_at=observed_at,
        reason=reason,
    )


def unverifiable_bootstrap_receipt(
    expectation: BootstrapExpectation,
    *,
    observed_at: datetime,
    reason: str,
) -> BootstrapMarginReceipt:
    return BootstrapMarginReceipt(
        BootstrapStatus.UNVERIFIABLE,
        expectation.session_id,
        expectation.account_address,
        expectation.signer_address,
        expectation.environment,
        expectation.instrument_id,
        expectation.coin,
        expectation.asset,
        expectation.margin_mode,
        expectation.leverage,
        "updateLeverage",
        None,
        None,
        None,
        None,
        observed_at,
        reason,
    )


def perform_bootstrap_attempt(
    expectation: BootstrapExpectation,
    command: UpdateLeverageCommand,
    *,
    observed_at: datetime,
    submit: Callable[[dict[str, str | int | bool], int], Any],
) -> BootstrapMarginReceipt:
    """Submit exactly once through an injected signer/transport; never retry unknown outcomes."""
    try:
        response = submit(command.action(), command.nonce)
    except Exception as exc:  # noqa: BLE001 - mutation boundary must convert ambiguity to evidence
        return unverifiable_bootstrap_receipt(
            expectation,
            observed_at=observed_at,
            reason=f"ambiguous updateLeverage outcome: {type(exc).__name__}: {exc}",
        )
    return classify_bootstrap_response(
        expectation,
        command,
        response,
        observed_at=observed_at,
    )


def load_bootstrap_receipt(path: Path) -> BootstrapMarginReceipt:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return _receipt_from_dict(value)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise BootstrapReceiptError(f"invalid bootstrap margin receipt: {path}") from exc


def save_bootstrap_receipt(path: Path, receipt: BootstrapMarginReceipt) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(receipt)
    payload["status"] = receipt.status.value
    payload["expected_margin_mode"] = receipt.expected_margin_mode.value
    payload["expected_leverage"] = str(receipt.expected_leverage)
    payload["observed_at"] = receipt.observed_at.isoformat()
    payload["consumed_at"] = receipt.consumed_at.isoformat() if receipt.consumed_at else None
    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(payload, temporary, indent=2, sort_keys=True)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError as exc:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise BootstrapReceiptError(f"cannot persist bootstrap margin receipt: {path}") from exc


def consume_bootstrap_receipt(
    path: Path,
    expectation: BootstrapExpectation,
    *,
    now: datetime,
    max_age_seconds: int,
    entry_id: str,
) -> bool:
    lock_path = path.with_suffix(f"{path.suffix}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with lock_path.open("a", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            receipt = load_bootstrap_receipt(path)
            if not receipt.matches(expectation, now=now, max_age_seconds=max_age_seconds):
                return False
            save_bootstrap_receipt(
                path,
                replace(receipt, consumed_at=now, consumed_for_entry_id=entry_id),
            )
            return True
    except (OSError, BootstrapReceiptError):
        return False


def _receipt_from_dict(value: dict[str, Any]) -> BootstrapMarginReceipt:
    observed_at = datetime.fromisoformat(value["observed_at"])
    consumed_at = (
        datetime.fromisoformat(value["consumed_at"]) if value.get("consumed_at") else None
    )
    if observed_at.tzinfo is None or (consumed_at is not None and consumed_at.tzinfo is None):
        raise ValueError("bootstrap timestamps must be timezone-aware")
    return BootstrapMarginReceipt(
        status=BootstrapStatus(value["status"]),
        session_id=str(value["session_id"]),
        account_address=str(value["account_address"]),
        signer_address=str(value["signer_address"]),
        environment=str(value["environment"]),
        instrument_id=str(value["instrument_id"]),
        coin=str(value["coin"]),
        asset=int(value["asset"]),
        expected_margin_mode=MarginMode(value["expected_margin_mode"]),
        expected_leverage=Decimal(str(value["expected_leverage"])),
        action_type=str(value["action_type"]),
        action_is_cross=value.get("action_is_cross"),
        action_leverage=(
            int(value["action_leverage"]) if value.get("action_leverage") is not None else None
        ),
        nonce=int(value["nonce"]) if value.get("nonce") is not None else None,
        response_type=(str(value["response_type"]) if value.get("response_type") else None),
        observed_at=observed_at,
        reason=str(value["reason"]),
        consumed_at=consumed_at,
        consumed_for_entry_id=(
            str(value["consumed_for_entry_id"])
            if value.get("consumed_for_entry_id") is not None
            else None
        ),
    )


def _validate_target(expectation: BootstrapExpectation) -> None:
    _validate_identity(
        expectation.session_id,
        expectation.account_address,
        expectation.signer_address,
        expectation.environment,
        expectation.instrument_id,
        expectation.coin,
    )
    if expectation.asset < 0:
        raise ValueError("invalid bootstrap instrument identity")
    if (
        not expectation.leverage.is_finite()
        or expectation.leverage <= 0
        or expectation.leverage != expectation.leverage.to_integral_value()
    ):
        raise ValueError("bootstrap leverage must be a positive integer")


def _validate_identity(
    session_id: str,
    account_address: str,
    signer_address: str,
    environment: str,
    instrument_id: str,
    coin: str,
) -> None:
    if not session_id:
        raise ValueError("bootstrap session identity cannot be empty")
    if not re.fullmatch(r"0x[0-9a-fA-F]{40}", account_address):
        raise ValueError("bootstrap account address is invalid")
    if not re.fullmatch(r"0x[0-9a-fA-F]{40}", signer_address):
        raise ValueError("bootstrap signer address is invalid")
    if environment not in {"mainnet", "testnet"}:
        raise ValueError("unsupported Hyperliquid environment")
    if not instrument_id or not coin:
        raise ValueError("invalid bootstrap instrument identity")
