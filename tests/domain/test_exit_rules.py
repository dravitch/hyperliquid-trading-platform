from decimal import Decimal

import pytest

from hltrader.domain.exit_rules import ExitReason, PriceDirection, PriceExitRule, RsiExitRule


def test_rsi_boundary_is_inclusive() -> None:
    rule = RsiExitRule(Decimal(20))
    assert rule.evaluate(Decimal(20)).reason is ExitReason.RSI
    assert not rule.evaluate(Decimal("20.01")).should_exit
    assert not rule.evaluate(None).should_exit


@pytest.mark.parametrize(
    ("direction", "price", "expected"),
    [
        (PriceDirection.ABOVE, "60000", True),
        (PriceDirection.ABOVE, "59999.99", False),
        (PriceDirection.BELOW, "60000", True),
        (PriceDirection.BELOW, "60000.01", False),
    ],
)
def test_price_boundary(direction: PriceDirection, price: str, expected: bool) -> None:
    result = PriceExitRule(Decimal(60000), direction).evaluate(Decimal(price))
    assert result.should_exit is expected


def test_invalid_indicator_and_prices_fail_closed() -> None:
    with pytest.raises(ValueError):
        RsiExitRule().evaluate(Decimal(101))
    with pytest.raises(ValueError):
        PriceExitRule(Decimal(60000), PriceDirection.ABOVE).evaluate(Decimal(0))
