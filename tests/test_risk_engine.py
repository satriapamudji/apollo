from datetime import datetime, timezone

from src.config.settings import RiskConfig
from src.ledger.state import Order, TradingState
from src.models import TradeProposal
from src.risk.engine import RiskEngine
from src.risk.sizing import SymbolFilters


def _proposal(**overrides):
    base = TradeProposal(
        symbol="BTCUSDT",
        side="LONG",
        entry_price=100.0,
        stop_price=98.0,
        take_profit=106.0,
        atr=2.0,
        leverage=3,
        score=None,
        funding_rate=0.0,
        news_risk="LOW",
        trade_id="t-1",
        created_at=datetime.now(timezone.utc),
        is_entry=True,
    )
    data = base.__dict__ | overrides
    return TradeProposal(**data)


def test_risk_engine_approves_valid_trade() -> None:
    engine = RiskEngine(RiskConfig())
    state = TradingState(equity=100.0, peak_equity=100.0)
    filters = SymbolFilters(min_notional=5.0, min_qty=0.001, step_size=0.001, tick_size=0.01)
    result = engine.evaluate(state, _proposal(), filters, datetime.now(timezone.utc))
    assert result.approved
    assert result.reasons == []


def test_risk_engine_rejects_high_news_risk() -> None:
    engine = RiskEngine(RiskConfig())
    state = TradingState(equity=100.0, peak_equity=100.0)
    filters = SymbolFilters(min_notional=5.0, min_qty=0.001, step_size=0.001, tick_size=0.01)
    proposal = _proposal(news_risk="HIGH")
    result = engine.evaluate(state, proposal, filters, datetime.now(timezone.utc))
    assert not result.approved
    assert "NEWS_HIGH_RISK" in result.reasons


def test_risk_engine_triggers_circuit_breaker_on_drawdown() -> None:
    engine = RiskEngine(RiskConfig())
    state = TradingState(equity=89.0, peak_equity=100.0)
    filters = SymbolFilters(min_notional=5.0, min_qty=0.001, step_size=0.001, tick_size=0.01)
    result = engine.evaluate(state, _proposal(), filters, datetime.now(timezone.utc))
    assert result.circuit_breaker
    assert not result.approved


def test_risk_engine_rejects_open_order() -> None:
    engine = RiskEngine(RiskConfig())
    state = TradingState(equity=100.0, peak_equity=100.0)
    state.open_orders["open-1"] = Order(
        client_order_id="open-1",
        symbol="BTCUSDT",
        side="BUY",
        order_type="LIMIT",
        quantity=0.1,
        price=100.0,
        stop_price=None,
        reduce_only=False,
        status="NEW",
        created_at=datetime.now(timezone.utc),
    )
    filters = SymbolFilters(min_notional=5.0, min_qty=0.001, step_size=0.001, tick_size=0.01)
    result = engine.evaluate(state, _proposal(), filters, datetime.now(timezone.utc))
    assert not result.approved
    assert "OPEN_ORDER_EXISTS" in result.reasons
