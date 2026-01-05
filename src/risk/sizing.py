"""Position sizing utilities."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN


@dataclass(frozen=True)
class SymbolFilters:
    min_notional: float
    min_qty: float
    step_size: float
    tick_size: float


@dataclass(frozen=True)
class PositionSize:
    quantity: float
    notional: float
    leverage: int


def _round_step(value: float, step: float) -> float:
    if step <= 0:
        return value
    precision = abs(Decimal(str(step)).as_tuple().exponent)
    quant = Decimal(str(step))
    rounded = (Decimal(str(value)) / quant).to_integral_value(rounding=ROUND_DOWN) * quant
    return float(round(rounded, precision))


class PositionSizer:
    """Risk-based position sizing with leverage caps."""

    def __init__(self, risk_per_trade_pct: float, max_leverage: int) -> None:
        self.risk_per_trade_pct = risk_per_trade_pct
        self.max_leverage = max_leverage

    def calculate_size(
        self,
        equity: float,
        entry_price: float,
        stop_price: float,
        symbol_filters: SymbolFilters,
        leverage: int,
    ) -> PositionSize | None:
        if equity <= 0 or entry_price <= 0:
            return None
        stop_distance_pct = abs(entry_price - stop_price) / entry_price
        if stop_distance_pct <= 0:
            return None
        risk_amount = equity * (self.risk_per_trade_pct / 100)
        position_value = risk_amount / stop_distance_pct

        max_position_value = equity * min(leverage, self.max_leverage)
        position_value = min(position_value, max_position_value)

        if position_value < symbol_filters.min_notional:
            return None

        quantity = position_value / entry_price
        quantity = _round_step(quantity, symbol_filters.step_size)
        if quantity < symbol_filters.min_qty or quantity <= 0:
            return None

        notional = quantity * entry_price
        return PositionSize(quantity=quantity, notional=notional, leverage=leverage)
