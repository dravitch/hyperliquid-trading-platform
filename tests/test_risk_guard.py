from decimal import Decimal

import pytest

from hltrader.risk.guard import MarginMode, RiskGuard, RiskViolation, VenueMarginState


@pytest.fixture
def guard() -> RiskGuard:
    return RiskGuard(Decimal(300), Decimal(3), MarginMode.ISOLATED, Decimal(3))


def test_authorizes_exact_configured_limits(guard: RiskGuard) -> None:
    guard.authorize_entry(Decimal(300), VenueMarginState(MarginMode.ISOLATED, Decimal(3)))


@pytest.mark.parametrize(
    "venue",
    [
        VenueMarginState(MarginMode.CROSS, Decimal(3)),
        VenueMarginState(MarginMode.ISOLATED, Decimal(2)),
        VenueMarginState(MarginMode.ISOLATED, Decimal(4)),
    ],
)
def test_margin_or_leverage_mismatch_fails_closed(
    guard: RiskGuard, venue: VenueMarginState
) -> None:
    with pytest.raises(RiskViolation):
        guard.authorize_entry(Decimal(300), venue)


def test_notional_over_limit_fails_closed(guard: RiskGuard) -> None:
    with pytest.raises(RiskViolation):
        guard.authorize_entry(Decimal("300.01"), VenueMarginState(MarginMode.ISOLATED, Decimal(3)))
