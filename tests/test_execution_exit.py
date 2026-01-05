import asyncio
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from src.config.settings import Settings
from src.execution.engine import ExecutionEngine
from src.ledger.bus import EventBus
from src.ledger.events import EventType
from src.ledger.state import Position
from src.ledger.store import EventLedger


class _DummyRest:
    async def place_order(self, params):
        return {"orderId": 123}

    async def get_order(self, symbol, client_order_id=None, order_id=None):
        return {
            "status": "FILLED",
            "executedQty": "0.5",
            "avgPrice": "101.5",
            "orderId": 123,
        }


def test_execute_exit_uses_fill_price() -> None:
    settings = Settings(
        run={"mode": "testnet", "enable_trading": True},
        binance_testnet_api_key="k",
        binance_testnet_secret_key="s",
        _env_file=None,
    )
    ledger_path = Path("data") / "test_ledgers" / f"exit_{uuid4().hex}"
    ledger = EventLedger(str(ledger_path))
    bus = EventBus(ledger)
    engine = ExecutionEngine(settings, _DummyRest(), bus)
    position = Position(
        symbol="BTCUSDT",
        side="LONG",
        quantity=0.5,
        entry_price=100.0,
        leverage=2,
        opened_at=datetime.now(timezone.utc),
    )

    asyncio.run(engine.execute_exit(position, exit_price=99.0, reason="TEST"))
    events = ledger.load_all()
    closed = [e for e in events if e.event_type == EventType.POSITION_CLOSED]
    assert closed
    assert closed[-1].payload["exit_price"] == 101.5
