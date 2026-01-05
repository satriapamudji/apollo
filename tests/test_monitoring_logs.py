import csv
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from src.ledger.events import Event, EventType
from src.monitoring.order_log import OrderLogger
from src.monitoring.trade_log import TradeLogger


def _read_rows(path) -> list[dict[str, str]]:
    with open(path, newline="") as handle:
        return list(csv.DictReader(handle))


def _make_test_dir() -> Path:
    base = Path("data") / "test_ledgers" / f"tmp_{uuid4().hex}"
    base.mkdir(parents=True, exist_ok=True)
    return base


def test_trade_logger_writes_open_and_updates_on_close() -> None:
    test_dir = _make_test_dir()
    log_path = test_dir / "trades.csv"
    logger = TradeLogger(str(log_path))
    opened_at = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

    try:
        logger.handle_event(
            Event(
                event_id="1",
                event_type=EventType.POSITION_OPENED,
                timestamp=opened_at,
                sequence_num=1,
                payload={
                    "trade_id": "t-1",
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "quantity": 0.1,
                    "entry_price": 100.0,
                },
                metadata={},
            )
        )
        rows = _read_rows(log_path)
        assert len(rows) == 1
        assert rows[0]["trade_id"] == "t-1"
        assert rows[0]["exit_time"] == ""

        closed_at = opened_at + timedelta(hours=2)
        logger.handle_event(
            Event(
                event_id="2",
                event_type=EventType.POSITION_CLOSED,
                timestamp=closed_at,
                sequence_num=2,
                payload={
                    "trade_id": "t-1",
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "quantity": 0.1,
                    "entry_price": 100.0,
                    "exit_price": 110.0,
                    "realized_pnl": 1.0,
                    "reason": "EXIT",
                },
                metadata={},
            )
        )

        rows = _read_rows(log_path)
        assert len(rows) == 1
        assert rows[0]["trade_id"] == "t-1"
        assert rows[0]["exit_time"] != ""
        assert float(rows[0]["exit_price"]) == 110.0
        assert float(rows[0]["realized_pnl"]) == 1.0
        assert float(rows[0]["holding_hours"]) == 2.0
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def test_order_logger_appends_rows() -> None:
    test_dir = _make_test_dir()
    log_path = test_dir / "orders.csv"
    logger = OrderLogger(str(log_path))
    now = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

    try:
        logger.handle_event(
            Event(
                event_id="1",
                event_type=EventType.ORDER_PLACED,
                timestamp=now,
                sequence_num=1,
                payload={
                    "symbol": "BTCUSDT",
                    "side": "BUY",
                    "quantity": 0.1,
                    "price": 100.0,
                    "order_type": "LIMIT",
                    "client_order_id": "c-1",
                    "reduce_only": False,
                    "order_id": 123,
                },
                metadata={"trade_id": "t-1"},
            )
        )

        rows = _read_rows(log_path)
        assert len(rows) == 1
        assert rows[0]["event_type"] == EventType.ORDER_PLACED.value
        assert rows[0]["trade_id"] == "t-1"
        assert rows[0]["symbol"] == "BTCUSDT"
        assert rows[0]["client_order_id"] == "c-1"
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)
