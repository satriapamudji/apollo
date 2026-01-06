import asyncio
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from src.config.settings import Settings
from src.execution.engine import ExecutionEngine
from src.ledger.bus import EventBus
from src.ledger.events import EventType
from src.ledger.state import Position
from src.ledger.store import EventLedger
from src.models import TradeProposal


class _DummyRest:
    """Mock REST client for testing."""

    def __init__(self):
        self.placed_orders = []
        self.cancelled_orders = []

    async def place_order(self, params):
        self.placed_orders.append(params)
        return {"orderId": 123, "clientOrderId": params.get("client_order_id")}

    async def cancel_order(self, symbol, client_order_id):
        self.cancelled_orders.append({"symbol": symbol, "client_order_id": client_order_id})

    async def get_order(self, symbol, client_order_id=None, order_id=None):
        return {
            "status": "FILLED",
            "executedQty": "0.5",
            "avgPrice": "101.5",
            "orderId": 123,
        }


def test_trailing_stop_not_updated_before_start_threshold_long() -> None:
    """Test that trailing stop doesn't update before price moves trailing_start_atr in favor."""
    settings = Settings(
        run={"mode": "testnet", "enable_trading": True},
        binance_testnet_api_key="k",
        binance_testnet_secret_key="s",
        _env_file=None,
    )
    ledger_path = Path("data") / "test_ledgers" / f"trail_{uuid4().hex}"
    ledger = EventLedger(str(ledger_path))
    bus = EventBus(ledger)
    rest = _DummyRest()
    engine = ExecutionEngine(settings, rest, bus)

    # Position with entry at 100, stop at 98 (2*ATR where ATR=1)
    position = Position(
        symbol="BTCUSDT",
        side="LONG",
        quantity=0.5,
        entry_price=100.0,
        stop_price=98.0,  # 2 ATR below entry
        leverage=2,
        opened_at=datetime.now(timezone.utc),
    )

    # Price moves to 101 (only 1 ATR profit, less than trailing_start_atr=1.5)
    result = asyncio.run(engine.update_trailing_stop(position, current_price=101.0, atr=1.0, tick_size=0.01))
    assert result is False  # Should not update because not past trailing start
    assert len(rest.placed_orders) == 0  # No order should be placed


def test_trailing_stop_not_updated_before_start_threshold_short() -> None:
    """Test that trailing stop doesn't update for SHORT before price moves in favor."""
    settings = Settings(
        run={"mode": "testnet", "enable_trading": True},
        binance_testnet_api_key="k",
        binance_testnet_secret_key="s",
        _env_file=None,
    )
    ledger_path = Path("data") / "test_ledgers" / f"trail_{uuid4().hex}"
    ledger = EventLedger(str(ledger_path))
    bus = EventBus(ledger)
    rest = _DummyRest()
    engine = ExecutionEngine(settings, rest, bus)

    # Short position with entry at 100, stop at 102 (2*ATR where ATR=1)
    position = Position(
        symbol="BTCUSDT",
        side="SHORT",
        quantity=0.5,
        entry_price=100.0,
        stop_price=102.0,
        leverage=2,
        opened_at=datetime.now(timezone.utc),
    )

    # Price moves to 99 (only 1 ATR profit, less than trailing_start_atr=1.5)
    result = asyncio.run(engine.update_trailing_stop(position, current_price=99.0, atr=1.0, tick_size=0.01))
    assert result is False
    assert len(rest.placed_orders) == 0


def test_trailing_stop_updates_after_start_threshold_long() -> None:
    """Test that trailing stop updates after price moves past trailing_start_atr."""
    settings = Settings(
        run={"mode": "testnet", "enable_trading": True},
        binance_testnet_api_key="k",
        binance_testnet_secret_key="s",
        _env_file=None,
    )
    ledger_path = Path("data") / "test_ledgers" / f"trail_{uuid4().hex}"
    ledger = EventLedger(str(ledger_path))
    bus = EventBus(ledger)
    rest = _DummyRest()
    engine = ExecutionEngine(settings, rest, bus)

    # Position with entry at 100, stop at 98 (2*ATR where ATR=1)
    position = Position(
        symbol="BTCUSDT",
        side="LONG",
        quantity=0.5,
        entry_price=100.0,
        stop_price=98.0,
        leverage=2,
        opened_at=datetime.now(timezone.utc),
    )

    # Price moves to 103 (3 ATR profit, past trailing_start_atr=1.5)
    # trailing_distance_atr=1.5, so new stop should be 103 - 1.5 = 101.5
    result = asyncio.run(engine.update_trailing_stop(position, current_price=103.0, atr=1.0, tick_size=0.01))
    assert result is True
    assert len(rest.placed_orders) == 1
    order = rest.placed_orders[0]
    assert order["symbol"] == "BTCUSDT"
    assert order["side"] == "SELL"
    assert order["type"] == "STOP_MARKET"
    assert order["reduceOnly"] == "true"
    # Stop price should be ~101.5 (103 - 1.5)
    assert abs(float(order["stopPrice"]) - 101.5) < 0.02


def test_trailing_stop_updates_after_start_threshold_short() -> None:
    """Test that trailing stop updates for SHORT after price moves in favor."""
    settings = Settings(
        run={"mode": "testnet", "enable_trading": True},
        binance_testnet_api_key="k",
        binance_testnet_secret_key="s",
        _env_file=None,
    )
    ledger_path = Path("data") / "test_ledgers" / f"trail_{uuid4().hex}"
    ledger = EventLedger(str(ledger_path))
    bus = EventBus(ledger)
    rest = _DummyRest()
    engine = ExecutionEngine(settings, rest, bus)

    # Short position with entry at 100, stop at 102
    position = Position(
        symbol="BTCUSDT",
        side="SHORT",
        quantity=0.5,
        entry_price=100.0,
        stop_price=102.0,
        leverage=2,
        opened_at=datetime.now(timezone.utc),
    )

    # Price moves to 97 (3 ATR profit, past trailing_start_atr=1.5)
    # trailing_distance_atr=1.5, so new stop should be 97 + 1.5 = 98.5
    result = asyncio.run(engine.update_trailing_stop(position, current_price=97.0, atr=1.0, tick_size=0.01))
    assert result is True
    assert len(rest.placed_orders) == 1
    order = rest.placed_orders[0]
    assert order["side"] == "BUY"  # For SHORT, we buy to close
    assert order["type"] == "STOP_MARKET"
    assert abs(float(order["stopPrice"]) - 98.5) < 0.02


def test_trailing_stop_does_not_widen_long() -> None:
    """Test that trailing stop doesn't widen for LONG positions."""
    settings = Settings(
        run={"mode": "testnet", "enable_trading": True},
        binance_testnet_api_key="k",
        binance_testnet_secret_key="s",
        _env_file=None,
    )
    ledger_path = Path("data") / "test_ledgers" / f"trail_{uuid4().hex}"
    ledger = EventLedger(str(ledger_path))
    bus = EventBus(ledger)
    rest = _DummyRest()
    engine = ExecutionEngine(settings, rest, bus)

    # Position with stop already at 101 (2 ATR profit, then trailed to 101)
    position = Position(
        symbol="BTCUSDT",
        side="LONG",
        quantity=0.5,
        entry_price=100.0,
        stop_price=101.0,  # Already at a good level
        leverage=2,
        opened_at=datetime.now(timezone.utc),
    )

    # Price drops to 100.5 - stop should NOT move down to 99
    # (new_stop would be 100.5 - 1.5 = 99, which is worse than current 101)
    result = asyncio.run(engine.update_trailing_stop(position, current_price=100.5, atr=1.0, tick_size=0.01))
    assert result is False  # Should not update because it would widen
    assert len(rest.placed_orders) == 0


def test_trailing_stop_does_not_widen_short() -> None:
    """Test that trailing stop doesn't widen for SHORT positions."""
    settings = Settings(
        run={"mode": "testnet", "enable_trading": True},
        binance_testnet_api_key="k",
        binance_testnet_secret_key="s",
        _env_file=None,
    )
    ledger_path = Path("data") / "test_ledgers" / f"trail_{uuid4().hex}"
    ledger = EventLedger(str(ledger_path))
    bus = EventBus(ledger)
    rest = _DummyRest()
    engine = ExecutionEngine(settings, rest, bus)

    # Short position with stop already at 99
    position = Position(
        symbol="BTCUSDT",
        side="SHORT",
        quantity=0.5,
        entry_price=100.0,
        stop_price=99.0,
        leverage=2,
        opened_at=datetime.now(timezone.utc),
    )

    # Price rises to 99.5 - stop should NOT move up to 101
    result = asyncio.run(engine.update_trailing_stop(position, current_price=99.5, atr=1.0, tick_size=0.01))
    assert result is False
    assert len(rest.placed_orders) == 0


def test_partial_take_profit_placement() -> None:
    """Test that partial TP is placed at 2*ATR for 25% of position."""
    settings = Settings(
        run={"mode": "testnet", "enable_trading": True},
        binance_testnet_api_key="k",
        binance_testnet_secret_key="s",
        _env_file=None,
    )
    ledger_path = Path("data") / "test_ledgers" / f"ptp_{uuid4().hex}"
    ledger = EventLedger(str(ledger_path))
    bus = EventBus(ledger)
    rest = _DummyRest()
    engine = ExecutionEngine(settings, rest, bus)

    # Create a trade proposal with ATR
    proposal = TradeProposal(
        symbol="BTCUSDT",
        side="LONG",
        entry_price=100.0,
        stop_price=98.0,
        take_profit=None,  # No fixed TP - using partial TP + trailing
        atr=1.0,  # ATR = 1.0
        leverage=2,
        score=None,
        funding_rate=0.0,
        news_risk="LOW",
        trade_id="test_trade_123",
        created_at=datetime.now(timezone.utc),
    )

    # Execute protective orders (0.5 quantity)
    asyncio.run(engine._place_protective_orders(proposal, quantity=0.5, stop_price=98.0, tick_size=0.01))

    # Should have 2 orders: SL + partial TP
    assert len(rest.placed_orders) == 2

    # Find the TP order by type (not by client_order_id since it uses first letter only)
    tp_orders = [o for o in rest.placed_orders if o.get("type") == "TAKE_PROFIT_MARKET"]
    assert len(tp_orders) == 1
    tp_order = tp_orders[0]

    # Partial TP should be 25% of 0.5 = 0.125
    assert tp_order["quantity"] == 0.125
    # TP should be at entry + 2*ATR = 102.0
    assert abs(float(tp_order["stopPrice"]) - 102.0) < 0.02
    assert tp_order["reduceOnly"] == "true"


def test_partial_take_profit_short() -> None:
    """Test that partial TP is placed correctly for SHORT positions."""
    settings = Settings(
        run={"mode": "testnet", "enable_trading": True},
        binance_testnet_api_key="k",
        binance_testnet_secret_key="s",
        _env_file=None,
    )
    ledger_path = Path("data") / "test_ledgers" / f"ptp_{uuid4().hex}"
    ledger = EventLedger(str(ledger_path))
    bus = EventBus(ledger)
    rest = _DummyRest()
    engine = ExecutionEngine(settings, rest, bus)

    proposal = TradeProposal(
        symbol="BTCUSDT",
        side="SHORT",
        entry_price=100.0,
        stop_price=102.0,
        take_profit=None,
        atr=1.0,
        leverage=2,
        score=None,
        funding_rate=0.0,
        news_risk="LOW",
        trade_id="test_trade_456",
        created_at=datetime.now(timezone.utc),
    )

    asyncio.run(engine._place_protective_orders(proposal, quantity=0.5, stop_price=102.0, tick_size=0.01))

    # Should have 2 orders: SL + partial TP
    assert len(rest.placed_orders) == 2

    # Find the TP order by type
    tp_orders = [o for o in rest.placed_orders if o.get("type") == "TAKE_PROFIT_MARKET"]
    assert len(tp_orders) == 1
    tp_order = tp_orders[0]

    # Partial TP should be 25% of 0.5 = 0.125
    assert tp_order["quantity"] == 0.125
    # TP should be at entry - 2*ATR = 98.0
    assert abs(float(tp_order["stopPrice"]) - 98.0) < 0.02
    assert tp_order["side"] == "BUY"  # For SHORT, we buy to close


def test_trailing_stop_logs_event_on_failure() -> None:
    """Test that MANUAL_INTERVENTION is published when trailing stop update fails."""
    settings = Settings(
        run={"mode": "testnet", "enable_trading": True},
        binance_testnet_api_key="k",
        binance_testnet_secret_key="s",
        _env_file=None,
    )
    ledger_path = Path("data") / "test_ledgers" / f"trail_fail_{uuid4().hex}"
    ledger = EventLedger(str(ledger_path))
    bus = EventBus(ledger)

    class _FailingRest(_DummyRest):
        async def place_order(self, params):
            raise Exception("Simulated API failure")

    rest = _FailingRest()
    engine = ExecutionEngine(settings, rest, bus)

    position = Position(
        symbol="BTCUSDT",
        side="LONG",
        quantity=0.5,
        entry_price=100.0,
        stop_price=98.0,
        leverage=2,
        opened_at=datetime.now(timezone.utc),
    )

    result = asyncio.run(engine.update_trailing_stop(position, current_price=103.0, atr=1.0, tick_size=0.01))
    assert result is False

    # Should publish MANUAL_INTERVENTION event
    events = ledger.load_all()
    intervention_events = [e for e in events if e.event_type == EventType.MANUAL_INTERVENTION]
    assert len(intervention_events) == 1
    assert intervention_events[0].payload["action"] == "TRAILING_STOP_UPDATE_FAILED"
