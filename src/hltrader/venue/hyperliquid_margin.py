from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.request import Request, urlopen

from hltrader.risk.guard import MarginMode
from hltrader.risk.margin_verification import (
    MarginVerificationReceipt,
    VerificationStatus,
)

SUPPORTED_INSTRUMENTS = {"BTC-USD-PERP.HYPERLIQUID": "BTC"}


class ClearinghouseParseError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class MarginVerificationRequest:
    account_address: str
    environment: str
    instrument_id: str
    coin: str
    expected_margin_mode: MarginMode
    expected_leverage: Decimal
    max_age_seconds: int = 300

    def __post_init__(self) -> None:
        if not re.fullmatch(r"0x[0-9a-fA-F]{40}", self.account_address):
            raise ValueError("account address must contain exactly 20 hexadecimal bytes")
        if self.environment not in {"mainnet", "testnet"}:
            raise ValueError("unsupported Hyperliquid environment")
        if SUPPORTED_INSTRUMENTS.get(self.instrument_id) != self.coin:
            raise ValueError("instrument does not map exactly to the requested Hyperliquid coin")
        if (
            not self.expected_leverage.is_finite()
            or self.expected_leverage <= 0
            or self.expected_leverage != self.expected_leverage.to_integral_value()
        ):
            raise ValueError("expected leverage must be a positive integer")
        if self.max_age_seconds < 0:
            raise ValueError("maximum evidence age cannot be negative")


@dataclass(frozen=True, slots=True)
class ObservedMarginState:
    account_address: str
    environment: str
    instrument_id: str
    observed_at: datetime
    position_found: bool
    margin_mode: MarginMode | None = None
    leverage: Decimal | None = None
    position_qty: Decimal | None = None
    evidence_source: str = "hyperliquid_clearinghouseState"

    def __post_init__(self) -> None:
        if not re.fullmatch(r"0x[0-9a-fA-F]{40}", self.account_address):
            raise ValueError("observed account address is invalid")
        if self.environment not in {"mainnet", "testnet"}:
            raise ValueError("observed environment is invalid")
        if self.observed_at.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")


def fetch_clearinghouse_state(
    *, account_address: str, environment: str, timeout_seconds: float = 10
) -> Any:
    """Read public account state. This function never signs or mutates venue state."""
    endpoints = {
        "mainnet": "https://api.hyperliquid.xyz/info",
        "testnet": "https://api.hyperliquid-testnet.xyz/info",
    }
    try:
        url = endpoints[environment]
    except KeyError as exc:
        raise ValueError(f"unsupported Hyperliquid environment: {environment}") from exc
    payload = json.dumps({"type": "clearinghouseState", "user": account_address}).encode()
    request = Request(url, data=payload, headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=timeout_seconds) as response:
        return json.loads(response.read())


def parse_clearinghouse_state(
    payload: Any,
    *,
    account_address: str,
    environment: str,
    instrument_id: str,
    coin: str,
    observed_at: datetime,
) -> ObservedMarginState:
    """Parse only documented position fields; absence of a position remains explicit."""
    if observed_at.tzinfo is None:
        raise ClearinghouseParseError("observed_at must be timezone-aware")
    if not isinstance(payload, Mapping):
        raise ClearinghouseParseError("clearinghouseState must be an object")
    positions = payload.get("assetPositions")
    if not isinstance(positions, list):
        raise ClearinghouseParseError("assetPositions must be a list")

    matches = []
    for item in positions:
        if not isinstance(item, Mapping) or not isinstance(item.get("position"), Mapping):
            raise ClearinghouseParseError("assetPositions contains an invalid position")
        position = item["position"]
        if position.get("coin") == coin:
            matches.append(position)
    if not matches:
        return ObservedMarginState(
            account_address,
            environment,
            instrument_id,
            observed_at,
            False,
        )
    if len(matches) != 1:
        raise ClearinghouseParseError("multiple positions match the requested instrument")

    position = matches[0]
    leverage = position.get("leverage")
    if not isinstance(leverage, Mapping):
        raise ClearinghouseParseError("position leverage is absent or invalid")
    try:
        margin_mode = MarginMode(leverage["type"])
        leverage_value = Decimal(str(leverage["value"]))
        position_qty = Decimal(str(position["szi"]))
    except (KeyError, InvalidOperation, TypeError, ValueError) as exc:
        raise ClearinghouseParseError("position margin evidence is invalid") from exc
    if (
        not leverage_value.is_finite()
        or leverage_value <= 0
        or leverage_value != leverage_value.to_integral_value()
        or not position_qty.is_finite()
        or position_qty == 0
    ):
        raise ClearinghouseParseError("position contains an unexpected numeric value")
    return ObservedMarginState(
        account_address,
        environment,
        instrument_id,
        observed_at,
        True,
        margin_mode,
        leverage_value,
        position_qty,
    )


def classify_margin_evidence(
    request: MarginVerificationRequest,
    observation: ObservedMarginState,
    *,
    now: datetime,
) -> MarginVerificationReceipt:
    """Pure ternary classification. Missing evidence can never become VERIFIED."""
    mismatches = []
    if observation.account_address.lower() != request.account_address.lower():
        mismatches.append("account address")
    if observation.environment != request.environment:
        mismatches.append("environment")
    if observation.instrument_id != request.instrument_id:
        mismatches.append("instrument")
    age = now.astimezone(UTC) - observation.observed_at.astimezone(UTC)
    if mismatches:
        status = VerificationStatus.MISMATCH
        reason = f"bound request mismatch: {', '.join(mismatches)}"
    elif not 0 <= age.total_seconds() <= request.max_age_seconds:
        status = VerificationStatus.UNVERIFIABLE
        reason = "venue observation is stale or from the future"
    elif not observation.position_found:
        status = VerificationStatus.UNVERIFIABLE
        reason = "no relevant position exposes margin mode and leverage"
    elif observation.margin_mode is not request.expected_margin_mode:
        status = VerificationStatus.MISMATCH
        reason = "observed margin mode differs from expected mode"
    elif observation.leverage != request.expected_leverage:
        status = VerificationStatus.MISMATCH
        reason = "observed leverage differs from expected leverage"
    else:
        status = VerificationStatus.VERIFIED
        reason = "position margin mode and leverage match exactly"
    return MarginVerificationReceipt(
        status=status,
        account_address=request.account_address,
        environment=request.environment,
        instrument_id=request.instrument_id,
        expected_margin_mode=request.expected_margin_mode,
        expected_leverage=request.expected_leverage,
        observed_margin_mode=observation.margin_mode,
        observed_leverage=observation.leverage,
        observed_position_qty=observation.position_qty,
        evidence_source=observation.evidence_source,
        observed_at=observation.observed_at,
        reason=reason,
    )


def verify_margin_state(
    request: MarginVerificationRequest,
    *,
    now: datetime,
    fetcher: Callable[..., Any] = fetch_clearinghouse_state,
) -> MarginVerificationReceipt:
    """Fetch, parse and classify; all transport/parsing failures produce UNVERIFIABLE."""
    try:
        payload = fetcher(
            account_address=request.account_address,
            environment=request.environment,
        )
        observation = parse_clearinghouse_state(
            payload,
            account_address=request.account_address,
            environment=request.environment,
            instrument_id=request.instrument_id,
            coin=request.coin,
            observed_at=now,
        )
        return classify_margin_evidence(request, observation, now=now)
    except Exception as exc:  # noqa: BLE001 - transport/parser boundary must fail closed
        return MarginVerificationReceipt(
            status=VerificationStatus.UNVERIFIABLE,
            account_address=request.account_address,
            environment=request.environment,
            instrument_id=request.instrument_id,
            expected_margin_mode=request.expected_margin_mode,
            expected_leverage=request.expected_leverage,
            observed_margin_mode=None,
            observed_leverage=None,
            observed_position_qty=None,
            evidence_source="hyperliquid_clearinghouseState",
            observed_at=now,
            reason=f"venue evidence unavailable: {type(exc).__name__}: {exc}",
        )
