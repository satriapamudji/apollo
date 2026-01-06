"""Tests for performance telemetry module."""

import csv
import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.ledger.events import Event, EventType
from src.monitoring.performance_telemetry import (
    CostSummary,
    DailySummary,
    ExecutionSummary,
    PerformanceTelemetry,
    TradeSummary,
)


def _make_test_dir() -> Path:
    base = Path("data") / "test_telemetry" / f"tmp_{uuid4().hex}"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _create_mock_metrics() -> MagicMock:
    """Create a mock Metrics object with all required attributes."""
    metrics = MagicMock()
    metrics.orders_placed_total = MagicMock()
    metrics.orders_filled_total = MagicMock()
    metrics.fill_rate_pct = MagicMock()
    metrics.avg_entry_slippage_bps = MagicMock()
    metrics.avg_exit_slippage_bps = MagicMock()
    metrics.fees_paid_total = MagicMock()
    metrics.funding_received_total = MagicMock()
    metrics.funding_paid_total = MagicMock()
    metrics.net_funding = MagicMock()
    metrics.expectancy_per_trade = MagicMock()
    metrics.profit_factor_session = MagicMock()
    metrics.profit_factor_7d = MagicMock()
    metrics.profit_factor_30d = MagicMock()
    metrics.win_rate_pct = MagicMock()
    metrics.time_in_market_pct = MagicMock()
    metrics.avg_holding_time_hours = MagicMock()
    metrics.trades_closed_total = MagicMock()
    return metrics


def _make_event(
    event_type: EventType,
    payload: dict[str, Any],
    timestamp: datetime | None = None,
) -> Event:
    """Helper to create events for testing."""
    return Event(
        event_id=uuid4().hex,
        event_type=event_type,
        timestamp=timestamp or datetime.now(timezone.utc),
        sequence_num=1,
        payload=payload,
        metadata={},
    )


class TestCostSummary:
    def test_net_funding_positive(self) -> None:
        summary = CostSummary(funding_received=100.0, funding_paid=40.0)
        assert summary.net_funding == 60.0

    def test_net_funding_negative(self) -> None:
        summary = CostSummary(funding_received=20.0, funding_paid=80.0)
        assert summary.net_funding == -60.0

    def test_total_costs(self) -> None:
        summary = CostSummary(
            fees_paid=50.0,
            funding_received=20.0,
            funding_paid=30.0,
        )
        # total_costs = fees_paid + funding_paid - funding_received
        assert summary.total_costs == 60.0

    def test_default_values(self) -> None:
        summary = CostSummary()
        assert summary.fees_paid == 0.0
        assert summary.funding_received == 0.0
        assert summary.funding_paid == 0.0
        assert summary.net_funding == 0.0
        assert summary.total_costs == 0.0


class TestTradeSummary:
    def test_win_rate_pct_with_trades(self) -> None:
        summary = TradeSummary(total_trades=10, winning_trades=7, losing_trades=3)
        assert summary.win_rate_pct == 70.0

    def test_win_rate_pct_no_trades(self) -> None:
        summary = TradeSummary()
        assert summary.win_rate_pct == 0.0

    def test_expectancy_positive(self) -> None:
        summary = TradeSummary(
            total_trades=10,
            gross_profit=100.0,
            gross_loss=-40.0,  # net_pnl = 60
        )
        assert summary.net_pnl == 60.0
        assert summary.expectancy == 6.0

    def test_expectancy_negative(self) -> None:
        summary = TradeSummary(
            total_trades=10,
            gross_profit=30.0,
            gross_loss=-80.0,  # net_pnl = -50
        )
        assert summary.net_pnl == -50.0
        assert summary.expectancy == -5.0

    def test_expectancy_no_trades(self) -> None:
        summary = TradeSummary()
        assert summary.expectancy == 0.0

    def test_profit_factor_positive(self) -> None:
        summary = TradeSummary(gross_profit=100.0, gross_loss=-50.0)
        assert summary.profit_factor == 2.0

    def test_profit_factor_no_losses(self) -> None:
        summary = TradeSummary(gross_profit=100.0, gross_loss=0.0)
        assert summary.profit_factor == float("inf")

    def test_profit_factor_no_profit_no_loss(self) -> None:
        summary = TradeSummary()
        assert summary.profit_factor == 0.0

    def test_avg_holding_hours(self) -> None:
        summary = TradeSummary(total_trades=4, total_holding_hours=20.0)
        assert summary.avg_holding_hours == 5.0

    def test_avg_holding_hours_no_trades(self) -> None:
        summary = TradeSummary()
        assert summary.avg_holding_hours == 0.0

    def test_avg_entry_slippage_bps(self) -> None:
        summary = TradeSummary(entry_slippage_samples=[5.0, 10.0, 15.0])
        assert summary.avg_entry_slippage_bps == 10.0

    def test_avg_entry_slippage_bps_empty(self) -> None:
        summary = TradeSummary()
        assert summary.avg_entry_slippage_bps == 0.0

    def test_avg_exit_slippage_bps(self) -> None:
        summary = TradeSummary(exit_slippage_samples=[2.0, 4.0, 6.0])
        assert summary.avg_exit_slippage_bps == 4.0


class TestExecutionSummary:
    def test_fill_rate_pct(self) -> None:
        summary = ExecutionSummary(orders_placed=20, orders_filled=16)
        assert summary.fill_rate_pct == 80.0

    def test_fill_rate_pct_no_orders(self) -> None:
        summary = ExecutionSummary()
        assert summary.fill_rate_pct == 0.0

    def test_fill_rate_pct_all_filled(self) -> None:
        summary = ExecutionSummary(orders_placed=10, orders_filled=10)
        assert summary.fill_rate_pct == 100.0


class TestDailySummary:
    def test_to_dict(self) -> None:
        session_start = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        trades = TradeSummary(
            total_trades=10,
            winning_trades=6,
            losing_trades=4,
            gross_profit=120.0,
            gross_loss=-40.0,
        )
        execution = ExecutionSummary(orders_placed=12, orders_filled=10)
        costs = CostSummary(fees_paid=5.0, funding_received=2.0, funding_paid=3.0)

        summary = DailySummary(
            date="2026-01-01",
            session_start=session_start,
            trades=trades,
            execution=execution,
            costs=costs,
            time_in_market_pct=45.5,
        )

        result = summary.to_dict()

        assert result["date"] == "2026-01-01"
        assert result["trades"]["total"] == 10
        assert result["trades"]["wins"] == 6
        assert result["trades"]["win_rate_pct"] == 60.0
        assert result["performance"]["net_pnl"] == 80.0
        assert result["performance"]["expectancy"] == 8.0
        assert result["execution"]["fill_rate_pct"] == 83.33
        assert result["costs"]["net_funding"] == -1.0
        assert result["time"]["time_in_market_pct"] == 45.5

    def test_to_log_line(self) -> None:
        session_start = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        trades = TradeSummary(
            total_trades=5,
            winning_trades=3,
            losing_trades=2,
            gross_profit=50.0,
            gross_loss=-20.0,
        )
        execution = ExecutionSummary(orders_placed=6, orders_filled=5)
        costs = CostSummary(fees_paid=2.0, funding_received=1.0, funding_paid=0.5)

        summary = DailySummary(
            date="2026-01-01",
            session_start=session_start,
            trades=trades,
            execution=execution,
            costs=costs,
            time_in_market_pct=30.0,
        )

        log_line = summary.to_log_line()

        assert "date=2026-01-01" in log_line
        assert "trades=5" in log_line
        assert "win_rate=60.0%" in log_line
        assert "fill_rate=83.3%" in log_line

    def test_to_dict_with_inf_profit_factor(self) -> None:
        session_start = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        trades = TradeSummary(gross_profit=100.0, gross_loss=0.0)  # inf profit factor
        execution = ExecutionSummary()
        costs = CostSummary()

        summary = DailySummary(
            date="2026-01-01",
            session_start=session_start,
            trades=trades,
            execution=execution,
            costs=costs,
        )

        result = summary.to_dict()
        assert result["performance"]["profit_factor"] == "inf"


class TestPerformanceTelemetry:
    @pytest.fixture
    def test_dir(self) -> Path:
        path = _make_test_dir()
        yield path
        shutil.rmtree(path, ignore_errors=True)

    @pytest.fixture
    def telemetry(self, test_dir: Path) -> PerformanceTelemetry:
        metrics = _create_mock_metrics()
        trades_csv = test_dir / "trades.csv"
        return PerformanceTelemetry(
            metrics=metrics,
            rest_client=None,
            logs_path=str(test_dir),
            trades_csv_path=str(trades_csv),
            simulate=True,
        )

    @pytest.mark.asyncio
    async def test_handle_order_placed_increments_counter(
        self, telemetry: PerformanceTelemetry
    ) -> None:
        event = _make_event(
            EventType.ORDER_PLACED,
            {"symbol": "BTCUSDT", "side": "BUY", "reduce_only": False},
        )

        await telemetry.handle_order_placed(event)

        assert telemetry._orders_placed == 1
        telemetry.metrics.orders_placed_total.inc.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_order_placed_ignores_reduce_only(
        self, telemetry: PerformanceTelemetry
    ) -> None:
        event = _make_event(
            EventType.ORDER_PLACED,
            {"symbol": "BTCUSDT", "side": "SELL", "reduce_only": True},
        )

        await telemetry.handle_order_placed(event)

        assert telemetry._orders_placed == 0
        telemetry.metrics.orders_placed_total.inc.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_order_filled_increments_counter(
        self, telemetry: PerformanceTelemetry
    ) -> None:
        event = _make_event(
            EventType.ORDER_FILLED,
            {"symbol": "BTCUSDT", "reduce_only": False},
        )

        await telemetry.handle_order_filled(event)

        assert telemetry._orders_filled == 1
        telemetry.metrics.orders_filled_total.inc.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_order_filled_tracks_slippage(
        self, telemetry: PerformanceTelemetry
    ) -> None:
        event = _make_event(
            EventType.ORDER_FILLED,
            {
                "symbol": "BTCUSDT",
                "reduce_only": False,
                "expected_price": 100.0,
                "price": 100.05,  # 5 bps slippage
            },
        )

        await telemetry.handle_order_filled(event)

        assert len(telemetry._entry_slippage_samples) == 1
        assert abs(telemetry._entry_slippage_samples[0] - 5.0) < 0.1

    @pytest.mark.asyncio
    async def test_handle_position_opened_tracks_open_time(
        self, telemetry: PerformanceTelemetry
    ) -> None:
        now = datetime.now(timezone.utc)
        event = _make_event(
            EventType.POSITION_OPENED,
            {"symbol": "BTCUSDT"},
            timestamp=now,
        )

        await telemetry.handle_position_opened(event)

        assert "BTCUSDT" in telemetry._position_open_times
        assert telemetry._position_open_times["BTCUSDT"] == now

    @pytest.mark.asyncio
    async def test_handle_position_closed_tracks_duration(
        self, telemetry: PerformanceTelemetry
    ) -> None:
        open_time = datetime.now(timezone.utc) - timedelta(hours=2)
        close_time = datetime.now(timezone.utc)

        # First open
        open_event = _make_event(
            EventType.POSITION_OPENED,
            {"symbol": "BTCUSDT"},
            timestamp=open_time,
        )
        await telemetry.handle_position_opened(open_event)

        # Then close
        close_event = _make_event(
            EventType.POSITION_CLOSED,
            {"symbol": "BTCUSDT", "realized_pnl": 100.0},
            timestamp=close_time,
        )
        await telemetry.handle_position_closed(close_event)

        assert "BTCUSDT" not in telemetry._position_open_times
        # Should have ~2 hours (7200 seconds) of position time
        assert telemetry._total_position_seconds > 7000

    @pytest.mark.asyncio
    async def test_handle_position_closed_tracks_exit_slippage(
        self, telemetry: PerformanceTelemetry
    ) -> None:
        event = _make_event(
            EventType.POSITION_CLOSED,
            {
                "symbol": "BTCUSDT",
                "expected_exit_price": 100.0,
                "exit_price": 99.90,  # 10 bps slippage
            },
        )

        await telemetry.handle_position_closed(event)

        assert len(telemetry._exit_slippage_samples) == 1
        assert abs(telemetry._exit_slippage_samples[0] - 10.0) < 0.1

    def test_fill_rate_update(self, telemetry: PerformanceTelemetry) -> None:
        telemetry._orders_placed = 10
        telemetry._orders_filled = 8
        telemetry._update_fill_rate()

        telemetry.metrics.fill_rate_pct.set.assert_called_with(80.0)

    def test_get_execution_summary(self, telemetry: PerformanceTelemetry) -> None:
        telemetry._orders_placed = 15
        telemetry._orders_filled = 12

        summary = telemetry.get_execution_summary()

        assert summary.orders_placed == 15
        assert summary.orders_filled == 12
        assert summary.fill_rate_pct == 80.0


class TestPerformanceTelemetryCSVParsing:
    @pytest.fixture
    def test_dir(self) -> Path:
        path = _make_test_dir()
        yield path
        shutil.rmtree(path, ignore_errors=True)

    def _write_trades_csv(self, path: Path, rows: list[dict]) -> None:
        """Helper to write test trades.csv."""
        fieldnames = [
            "trade_id",
            "symbol",
            "side",
            "entry_time",
            "exit_time",
            "entry_price",
            "exit_price",
            "quantity",
            "realized_pnl",
            "holding_hours",
            "reason",
        ]
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)

    def test_compute_from_trades_csv_empty(self, test_dir: Path) -> None:
        trades_csv = test_dir / "trades.csv"
        telemetry = PerformanceTelemetry(
            metrics=_create_mock_metrics(),
            rest_client=None,
            logs_path=str(test_dir),
            trades_csv_path=str(trades_csv),
            simulate=True,
        )

        summary = telemetry.compute_from_trades_csv()

        assert summary.total_trades == 0
        assert summary.win_rate_pct == 0.0

    def test_compute_from_trades_csv_with_trades(self, test_dir: Path) -> None:
        trades_csv = test_dir / "trades.csv"
        now = datetime.now(timezone.utc)

        rows = [
            {
                "trade_id": "t-1",
                "symbol": "BTCUSDT",
                "side": "LONG",
                "entry_time": (now - timedelta(hours=10)).isoformat(),
                "exit_time": (now - timedelta(hours=5)).isoformat(),
                "entry_price": "100.0",
                "exit_price": "110.0",
                "quantity": "0.1",
                "realized_pnl": "10.0",
                "holding_hours": "5.0",
                "reason": "EXIT",
            },
            {
                "trade_id": "t-2",
                "symbol": "ETHUSDT",
                "side": "SHORT",
                "entry_time": (now - timedelta(hours=8)).isoformat(),
                "exit_time": (now - timedelta(hours=4)).isoformat(),
                "entry_price": "2000.0",
                "exit_price": "2100.0",
                "quantity": "0.05",
                "realized_pnl": "-5.0",
                "holding_hours": "4.0",
                "reason": "STOP_LOSS",
            },
        ]
        self._write_trades_csv(trades_csv, rows)

        telemetry = PerformanceTelemetry(
            metrics=_create_mock_metrics(),
            rest_client=None,
            logs_path=str(test_dir),
            trades_csv_path=str(trades_csv),
            simulate=True,
        )
        # Set session start to before all trades
        telemetry.session_start = now - timedelta(days=1)

        summary = telemetry.compute_from_trades_csv()

        assert summary.total_trades == 2
        assert summary.winning_trades == 1
        assert summary.losing_trades == 1
        assert summary.gross_profit == 10.0
        assert summary.gross_loss == -5.0
        assert summary.win_rate_pct == 50.0
        assert summary.total_holding_hours == 9.0

    def test_compute_from_trades_csv_with_window(self, test_dir: Path) -> None:
        trades_csv = test_dir / "trades.csv"
        now = datetime.now(timezone.utc)

        rows = [
            {
                "trade_id": "t-1",
                "symbol": "BTCUSDT",
                "side": "LONG",
                "entry_time": (now - timedelta(days=10)).isoformat(),
                "exit_time": (now - timedelta(days=9)).isoformat(),  # 10 days ago
                "entry_price": "100.0",
                "exit_price": "110.0",
                "quantity": "0.1",
                "realized_pnl": "100.0",
                "holding_hours": "24.0",
                "reason": "EXIT",
            },
            {
                "trade_id": "t-2",
                "symbol": "ETHUSDT",
                "side": "SHORT",
                "entry_time": (now - timedelta(days=2)).isoformat(),
                "exit_time": (now - timedelta(days=1)).isoformat(),  # 1 day ago
                "entry_price": "2000.0",
                "exit_price": "1900.0",
                "quantity": "0.05",
                "realized_pnl": "5.0",
                "holding_hours": "24.0",
                "reason": "EXIT",
            },
        ]
        self._write_trades_csv(trades_csv, rows)

        telemetry = PerformanceTelemetry(
            metrics=_create_mock_metrics(),
            rest_client=None,
            logs_path=str(test_dir),
            trades_csv_path=str(trades_csv),
            simulate=True,
        )

        # 7-day window should only include the recent trade
        summary = telemetry.compute_from_trades_csv(window_days=7)

        assert summary.total_trades == 1
        assert summary.gross_profit == 5.0

    def test_compute_from_trades_csv_skips_open_trades(self, test_dir: Path) -> None:
        trades_csv = test_dir / "trades.csv"
        now = datetime.now(timezone.utc)

        rows = [
            {
                "trade_id": "t-1",
                "symbol": "BTCUSDT",
                "side": "LONG",
                "entry_time": (now - timedelta(hours=5)).isoformat(),
                "exit_time": "",  # Still open
                "entry_price": "100.0",
                "exit_price": "",
                "quantity": "0.1",
                "realized_pnl": "",
                "holding_hours": "",
                "reason": "",
            },
        ]
        self._write_trades_csv(trades_csv, rows)

        telemetry = PerformanceTelemetry(
            metrics=_create_mock_metrics(),
            rest_client=None,
            logs_path=str(test_dir),
            trades_csv_path=str(trades_csv),
            simulate=True,
        )
        telemetry.session_start = now - timedelta(days=1)

        summary = telemetry.compute_from_trades_csv()

        assert summary.total_trades == 0


class TestPerformanceTelemetryDailySnapshot:
    @pytest.fixture
    def test_dir(self) -> Path:
        path = _make_test_dir()
        yield path
        shutil.rmtree(path, ignore_errors=True)

    def test_write_daily_snapshot_creates_files(self, test_dir: Path) -> None:
        telemetry = PerformanceTelemetry(
            metrics=_create_mock_metrics(),
            rest_client=None,
            logs_path=str(test_dir),
            trades_csv_path=str(test_dir / "trades.csv"),
            simulate=True,
        )

        summary = DailySummary(
            date="2026-01-01",
            session_start=datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
            trades=TradeSummary(total_trades=5),
            execution=ExecutionSummary(orders_placed=6, orders_filled=5),
            costs=CostSummary(),
        )

        json_path, csv_path = telemetry.write_daily_snapshot(summary)

        assert json_path.exists()
        assert csv_path.exists()

        # Verify JSON content
        with open(json_path) as f:
            data = json.load(f)
        assert data["date"] == "2026-01-01"
        assert data["trades"]["total"] == 5

        # Verify CSV content
        with open(csv_path, newline="") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) == 1
        assert rows[0]["date"] == "2026-01-01"
        assert rows[0]["trades"] == "5"


class TestPerformanceTelemetryBinanceFetch:
    @pytest.fixture
    def test_dir(self) -> Path:
        path = _make_test_dir()
        yield path
        shutil.rmtree(path, ignore_errors=True)

    @pytest.mark.asyncio
    async def test_fetch_costs_from_binance(self, test_dir: Path) -> None:
        mock_client = AsyncMock()
        mock_client.get_income_history = AsyncMock(
            side_effect=[
                # First call for COMMISSION
                [{"income": "-10.5"}, {"income": "-5.0"}],
                # Second call for FUNDING_FEE
                [{"income": "3.0"}, {"income": "-2.0"}],
            ]
        )

        telemetry = PerformanceTelemetry(
            metrics=_create_mock_metrics(),
            rest_client=mock_client,
            logs_path=str(test_dir),
            trades_csv_path=str(test_dir / "trades.csv"),
            simulate=False,
        )

        start_time = datetime.now(timezone.utc) - timedelta(days=1)
        costs = await telemetry.fetch_costs_from_binance(start_time)

        assert costs.fees_paid == 15.5
        assert costs.funding_received == 3.0
        assert costs.funding_paid == 2.0
        assert costs.net_funding == 1.0

    @pytest.mark.asyncio
    async def test_fetch_costs_no_client(self, test_dir: Path) -> None:
        telemetry = PerformanceTelemetry(
            metrics=_create_mock_metrics(),
            rest_client=None,
            logs_path=str(test_dir),
            trades_csv_path=str(test_dir / "trades.csv"),
            simulate=True,
        )

        start_time = datetime.now(timezone.utc) - timedelta(days=1)
        costs = await telemetry.fetch_costs_from_binance(start_time)

        assert costs.fees_paid == 0.0
        assert costs.funding_received == 0.0


class TestPerformanceTelemetryMetricsUpdate:
    @pytest.fixture
    def test_dir(self) -> Path:
        path = _make_test_dir()
        yield path
        shutil.rmtree(path, ignore_errors=True)

    @pytest.mark.asyncio
    async def test_update_metrics(self, test_dir: Path) -> None:
        metrics = _create_mock_metrics()
        telemetry = PerformanceTelemetry(
            metrics=metrics,
            rest_client=None,
            logs_path=str(test_dir),
            trades_csv_path=str(test_dir / "trades.csv"),
            simulate=True,
        )

        await telemetry.update_metrics()

        # Verify metrics were set
        metrics.expectancy_per_trade.set.assert_called()
        metrics.profit_factor_session.set.assert_called()
        metrics.profit_factor_7d.set.assert_called()
        metrics.profit_factor_30d.set.assert_called()
        metrics.win_rate_pct.set.assert_called()
        metrics.avg_holding_time_hours.set.assert_called()
        metrics.time_in_market_pct.set.assert_called()
