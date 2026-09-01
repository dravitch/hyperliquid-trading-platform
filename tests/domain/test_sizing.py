from decimal import Decimal

import pytest

from hltrader.domain.sizing import PositionSizing


def test_fixed_notional_rounds_quantity_down() -> None:
    sizing = PositionSizing(Decimal(300))
    assert sizing.base_quantity(Decimal(65000), Decimal("0.00001")) == Decimal("0.00461")


def test_leverage_is_not_a_sizing_input() -> None:
    assert not hasattr(PositionSizing, "leverage")


def test_rejects_quantity_below_venue_step() -> None:
    with pytest.raises(ValueError, match="too small"):
        PositionSizing(Decimal(1)).base_quantity(Decimal(65000), Decimal("0.001"))
