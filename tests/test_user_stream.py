from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import pytest

from src.config.settings import Settings
from src.execution.user_stream import UserDataStream
from src.ledger.events import EventType
from src.ledger.state import Position, StateManager


@dataclass
class DummyEventBus:
    events: list[tuple[EventType, dict[str, Any], dict[str, Any]]] = field(default_factory=list)

    async def publish(
        self,
        event_type: EventType,
        payload: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.events.append((event_type, payload, metadata or {}))


@pytest.mark.asyncio
async def test_reduce_only_stop_fill_emits_position_closed() -> None:
    settings = Settings()
    state = StateManager()
    state.state.positions["BNBUSDT"] = Position(
        symbol="BNBUSDT",
        side="LONG",
        quantity=1.0,
        entry_price=100.0,
        leverage=2,
        opened_at=datetime.now(timezone.utc),
        trade_id="t1",
    )
    bus = DummyEventBus()
    stream = UserDataStream(settings, rest=None, event_bus=bus, state_manager=state)  # type: ignore[arg-type]

    await stream._handle_order_trade_update(
        {
            "e": "ORDER_TRADE_UPDATE",
            "o": {
                "s": "BNBUSDT",
                "c": "C1",
                "S": "SELL",
                "o": "STOP_MARKET",
                "X": "FILLED",
                "x": "TRADE",
                "i": 123,
                "R": True,
                "sp": "95.0",
                "z": "1",
                "l": "1",
                "ap": "94.0",
                "L": "94.0",
                "rp": "0",
            },
        }
    )

    assert [e[0] for e in bus.events] == [EventType.ORDER_FILLED, EventType.POSITION_CLOSED]
    closed = bus.events[1]
    assert closed[1]["symbol"] == "BNBUSDT"
    assert closed[1]["reason"] == "STOP_LOSS"
    assert closed[1]["trade_id"] == "t1"
    assert float(closed[1]["realized_pnl"]) == pytest.approx(-6.0)


@pytest.mark.asyncio
async def test_bot_exit_order_fill_does_not_emit_position_closed() -> None:
    settings = Settings()
    state = StateManager()
    state.state.positions["BNBUSDT"] = Position(
        symbol="BNBUSDT",
        side="LONG",
        quantity=1.0,
        entry_price=100.0,
        leverage=2,
        opened_at=datetime.now(timezone.utc),
        trade_id="t1",
    )
    bus = DummyEventBus()
    stream = UserDataStream(settings, rest=None, event_bus=bus, state_manager=state)  # type: ignore[arg-type]

    await stream._handle_order_trade_update(
        {
            "e": "ORDER_TRADE_UPDATE",
            "o": {
                "s": "BNBUSDT",
                "c": "T_BNBUSDT_E_1234567890_abcd",
                "S": "SELL",
                "o": "MARKET",
                "X": "FILLED",
                "x": "TRADE",
                "i": 555,
                "R": True,
                "z": "1",
                "ap": "101.0",
            },
        }
    )

    assert [e[0] for e in bus.events] == [EventType.ORDER_FILLED]
