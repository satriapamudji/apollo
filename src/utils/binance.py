"""Binance filter helpers."""

from __future__ import annotations

from typing import Any

from src.risk.sizing import SymbolFilters


def parse_symbol_filters(filters: list[dict[str, Any]]) -> SymbolFilters:
    min_notional = 5.0
    min_qty = 0.001
    step_size = 0.001
    tick_size = 0.01
    for flt in filters:
        ftype = flt.get("filterType")
        if ftype in {"MIN_NOTIONAL", "NOTIONAL"}:
            min_notional = float(flt.get("notional", flt.get("minNotional", 0)))
        elif ftype in {"LOT_SIZE", "MARKET_LOT_SIZE"}:
            min_qty = max(min_qty, float(flt.get("minQty", min_qty)))
            step_size = max(step_size, float(flt.get("stepSize", step_size)))
        elif ftype == "PRICE_FILTER":
            tick_size = float(flt.get("tickSize", tick_size))
    return SymbolFilters(
        min_notional=min_notional,
        min_qty=min_qty,
        step_size=step_size,
        tick_size=tick_size,
    )
