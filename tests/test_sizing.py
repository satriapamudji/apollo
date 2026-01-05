from src.risk.sizing import PositionSizer, SymbolFilters


def test_position_sizer_respects_min_notional() -> None:
    sizer = PositionSizer(risk_per_trade_pct=1.0, max_leverage=5)
    filters = SymbolFilters(min_notional=20.0, min_qty=0.001, step_size=0.001, tick_size=0.01)
    size = sizer.calculate_size(
        equity=100.0,
        entry_price=100.0,
        stop_price=90.0,
        symbol_filters=filters,
        leverage=5,
    )
    assert size is None


def test_position_sizer_rounds_to_step() -> None:
    sizer = PositionSizer(risk_per_trade_pct=1.0, max_leverage=5)
    filters = SymbolFilters(min_notional=5.0, min_qty=0.001, step_size=0.01, tick_size=0.01)
    size = sizer.calculate_size(
        equity=100.0,
        entry_price=100.0,
        stop_price=92.0,
        symbol_filters=filters,
        leverage=5,
    )
    assert size is not None
    assert size.quantity == 0.12
