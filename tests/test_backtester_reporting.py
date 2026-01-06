"""Tests for backtester reporting module."""

import csv
import json
import shutil
import tempfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import pytest

from src.backtester.engine import BacktestResult, EquityPoint, Trade
from src.backtester.reporting import (
    compute_metrics,
    generate_report,
    write_equity_csv,
    write_summary_json,
    write_trade_csv,
)

@contextmanager
def _workspace_tmpdir(prefix: str) -> Path:
    root = Path.cwd() / f".pytest_tmp_{prefix}" / uuid4().hex
    root.mkdir(parents=True, exist_ok=True)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _create_trade(
    net_pnl: float,
    entry_time: datetime | None = None,
    exit_time: datetime | None = None,
) -> Trade:
    """Helper to create a trade with given P&L."""
    now = datetime(2024, 1, 15, 12, 0, 0)
    return Trade(
        trade_id="test-001",
        symbol="BTCUSDT",
        direction="LONG",
        entry_price=50000.0,
        exit_price=50000.0 + net_pnl,
        quantity=0.1,
        entry_time=entry_time or now,
        exit_time=exit_time or now,
        gross_pnl=net_pnl,
        net_pnl=net_pnl,
        holding_hours=24.0,
        slippage_bps=5.0,
        is_partial_fill=False,
    )


def _create_result(
    trades: list[Trade],
    initial_equity: float = 10000.0,
    final_equity: float | None = None,
) -> BacktestResult:
    """Helper to create a BacktestResult with given trades."""
    net_pnl_sum = sum(t.net_pnl for t in trades)
    actual_final = final_equity or initial_equity + net_pnl_sum
    total_return = (actual_final - initial_equity) / initial_equity
    wins = len([t for t in trades if t.net_pnl > 0])
    equity_curve: list[EquityPoint] = []
    equity = initial_equity
    peak = initial_equity
    for trade in trades:
        equity += trade.net_pnl
        peak = max(peak, equity)
        dd = (peak - equity) / peak if peak > 0 else 0.0
        equity_curve.append(
            EquityPoint(
                timestamp=trade.exit_time,
                equity=equity,
                drawdown=dd,
            )
        )
    final_eq = final_equity or initial_equity + sum(t.net_pnl for t in trades)
    return BacktestResult(
        trades=trades,
        equity_curve=equity_curve,
        total_return=total_return,
        win_rate=wins / len(trades) if trades else 0.0,
        max_drawdown=0.1,
        total_trades=len(trades),
        final_equity=final_eq,
        initial_equity=initial_equity,
    )


class TestComputeMetrics:
    """Tests for compute_metrics function."""

    def test_empty_trades(self) -> None:
        """Test metrics calculation with no trades."""
        result = _create_result([])
        metrics = compute_metrics(result)

        assert metrics["total_trades"] == 0
        assert metrics["expectancy"] == 0.0
        assert metrics["profit_factor"] == 0.0
        assert metrics["max_consecutive_losses"] == 0
        # Note: winning_trades/losing_trades only added when trades exist
        assert "winning_trades" not in metrics
        assert "losing_trades" not in metrics

    def test_all_winning_trades(self) -> None:
        """Test metrics with all winning trades."""
        trades = [_create_trade(100), _create_trade(200), _create_trade(150)]
        result = _create_result(trades)
        metrics = compute_metrics(result)

        assert metrics["total_trades"] == 3
        assert metrics["winning_trades"] == 3
        assert metrics["losing_trades"] == 0
        assert metrics["win_rate"] == 1.0
        # profit_factor is 'inf' string when there are no losses
        assert metrics["profit_factor"] == "inf"

    def test_all_losing_trades(self) -> None:
        """Test metrics with all losing trades."""
        trades = [_create_trade(-100), _create_trade(-200), _create_trade(-150)]
        result = _create_result(trades)
        metrics = compute_metrics(result)

        assert metrics["total_trades"] == 3
        assert metrics["winning_trades"] == 0
        assert metrics["losing_trades"] == 3
        assert metrics["win_rate"] == 0.0
        assert metrics["profit_factor"] == 0.0

    def test_expectancy_calculation(self) -> None:
        """Test expectancy: avg_win * win_rate - avg_loss * loss_rate."""
        # Create 4 trades: 3 wins of 100, 1 loss of 100
        # Expectancy = 100 * 0.75 - 100 * 0.25 = 75 - 25 = 50
        trades = [
            _create_trade(100),
            _create_trade(100),
            _create_trade(100),
            _create_trade(-100),
        ]
        result = _create_result(trades)
        metrics = compute_metrics(result)

        assert metrics["expectancy"] == pytest.approx(50.0, rel=0.01)

    def test_profit_factor(self) -> None:
        """Test profit factor calculation: sum(wins) / sum(losses)."""
        trades = [
            _create_trade(100),
            _create_trade(200),
            _create_trade(-100),
            _create_trade(-50),
        ]
        result = _create_result(trades)
        metrics = compute_metrics(result)

        # sum(wins) = 300, sum(losses) = 150, pf = 300/150 = 2.0
        assert metrics["profit_factor"] == pytest.approx(2.0, rel=0.01)

    def test_max_consecutive_losses(self) -> None:
        """Test max consecutive losses calculation."""
        # W, W, L, L, L, W, L, L = max 3 consecutive losses
        trades = [
            _create_trade(100),
            _create_trade(200),
            _create_trade(-50),
            _create_trade(-50),
            _create_trade(-50),
            _create_trade(100),
            _create_trade(-50),
            _create_trade(-50),
        ]
        result = _create_result(trades)
        metrics = compute_metrics(result)

        assert metrics["max_consecutive_losses"] == 3

    def test_monthly_returns(self) -> None:
        """Test monthly returns aggregation."""
        jan_15 = datetime(2024, 1, 15, 12, 0, 0)
        jan_20 = datetime(2024, 1, 20, 12, 0, 0)
        feb_10 = datetime(2024, 2, 10, 12, 0, 0)
        feb_25 = datetime(2024, 2, 25, 12, 0, 0)

        trades = [
            _create_trade(100, exit_time=jan_15),
            _create_trade(200, exit_time=jan_20),
            _create_trade(-50, exit_time=feb_10),
            _create_trade(150, exit_time=feb_25),
        ]
        result = _create_result(trades, initial_equity=1000.0)
        metrics = compute_metrics(result)

        monthly = metrics["monthly_returns"]
        # January: 300/1000 * 100 = 30%
        # February: 100/1000 * 100 = 10%
        assert "2024-01" in monthly
        assert "2024-02" in monthly
        assert monthly["2024-01"] == pytest.approx(30.0, rel=0.01)
        assert monthly["2024-02"] == pytest.approx(10.0, rel=0.01)

    def test_avg_r_multiple(self) -> None:
        """Test average R-multiple calculation."""
        trades = [
            _create_trade(200),  # R = 200/1000 = 0.2
            _create_trade(100),  # R = 100/1000 = 0.1
            _create_trade(-50),  # R = -50/1000 = -0.05
        ]
        result = _create_result(trades)
        metrics = compute_metrics(result)

        # (0.2 + 0.1 - 0.05) / 3 = 0.083
        assert metrics["avg_r_multiple"] == pytest.approx(0.08, rel=0.1)

    def test_avg_win_loss(self) -> None:
        """Test average win and loss calculations."""
        trades = [
            _create_trade(100),
            _create_trade(200),
            _create_trade(-50),
            _create_trade(-100),
        ]
        result = _create_result(trades)
        metrics = compute_metrics(result)

        assert metrics["avg_win"] == pytest.approx(150.0, rel=0.01)
        assert metrics["avg_loss"] == pytest.approx(75.0, rel=0.01)

    def test_largest_win_loss(self) -> None:
        """Test largest win and loss calculations."""
        trades = [
            _create_trade(100),
            _create_trade(500),  # Largest win
            _create_trade(-200),
            _create_trade(-50),
            _create_trade(-800),  # Largest loss
        ]
        result = _create_result(trades)
        metrics = compute_metrics(result)

        assert metrics["largest_win"] == pytest.approx(500.0, rel=0.01)
        assert metrics["largest_loss"] == pytest.approx(-800.0, rel=0.01)


class TestWriteTradeCsv:
    """Tests for write_trade_csv function."""

    def test_csv_header_fields(self) -> None:
        """Test that CSV has correct header fields."""
        trades = [_create_trade(100)]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            path = Path(f.name)
            write_trade_csv(trades, path)

        with open(path) as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames

        assert fieldnames is not None
        assert "trade_id" in fieldnames
        assert "symbol" in fieldnames
        assert "side" in fieldnames
        assert "entry_time" in fieldnames
        assert "exit_time" in fieldnames
        assert "entry_price" in fieldnames
        assert "exit_price" in fieldnames
        assert "quantity" in fieldnames
        assert "gross_pnl" in fieldnames
        assert "net_pnl" in fieldnames
        assert "holding_hours" in fieldnames

    def test_trade_data_written(self) -> None:
        """Test that trade data is correctly written to CSV."""
        trade = _create_trade(100)
        trades = [trade]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            path = Path(f.name)
            write_trade_csv(trades, path)

        with open(path) as f:
            reader = csv.DictReader(f)
            row = next(reader)

        assert row["trade_id"] == "test-001"
        assert row["symbol"] == "BTCUSDT"
        assert row["side"] == "LONG"
        assert float(row["net_pnl"]) == pytest.approx(100.0, rel=0.001)

    def test_timestamp_format(self) -> None:
        """Test that timestamps are in ISO format."""
        trade = _create_trade(100)
        trades = [trade]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            path = Path(f.name)
            write_trade_csv(trades, path)

        with open(path) as f:
            reader = csv.DictReader(f)
            row = next(reader)

        # ISO format should contain 'T' or be parseable
        assert "2024-01-15" in row["entry_time"]
        assert "2024-01-15" in row["exit_time"]


class TestWriteEquityCsv:
    """Tests for write_equity_csv function."""

    def test_csv_header_fields(self) -> None:
        """Test that CSV has correct header fields."""
        equity_points = [
            EquityPoint(
                timestamp=datetime(2024, 1, 1),
                equity=10000.0,
                drawdown=0.0,
            )
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            path = Path(f.name)
            write_equity_csv(equity_points, path)

        with open(path) as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames

        assert fieldnames is not None
        assert "timestamp" in fieldnames
        assert "equity" in fieldnames
        assert "drawdown" in fieldnames
        assert "drawdown_pct" in fieldnames

    def test_equity_data_written(self) -> None:
        """Test that equity data is correctly written to CSV."""
        equity_points = [
            EquityPoint(
                timestamp=datetime(2024, 1, 1),
                equity=10000.0,
                drawdown=0.05,
            )
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            path = Path(f.name)
            write_equity_csv(equity_points, path)

        with open(path) as f:
            reader = csv.DictReader(f)
            row = next(reader)

        assert float(row["equity"]) == pytest.approx(10000.0, rel=0.001)
        assert float(row["drawdown"]) == pytest.approx(0.05, rel=0.001)
        assert float(row["drawdown_pct"]) == pytest.approx(5.0, rel=0.001)

    def test_multiple_equity_points(self) -> None:
        """Test writing multiple equity points."""
        equity_points = [
            EquityPoint(timestamp=datetime(2024, 1, 1), equity=10000.0, drawdown=0.0),
            EquityPoint(timestamp=datetime(2024, 1, 2), equity=10100.0, drawdown=0.0),
            EquityPoint(timestamp=datetime(2024, 1, 3), equity=9900.0, drawdown=0.02),
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            path = Path(f.name)
            write_equity_csv(equity_points, path)

        with open(path) as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        assert len(rows) == 3


class TestWriteSummaryJson:
    """Tests for write_summary_json function."""

    def test_valid_json(self) -> None:
        """Test that written JSON is valid and parseable."""
        metrics = {"total_return": 0.15, "win_rate": 0.6, "profit_factor": 2.0}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            path = Path(f.name)
            write_summary_json(metrics, path)

        with open(path) as f:
            loaded = json.load(f)

        assert loaded["total_return"] == pytest.approx(0.15, rel=0.001)
        assert loaded["win_rate"] == pytest.approx(0.6, rel=0.001)
        assert loaded["profit_factor"] == pytest.approx(2.0, rel=0.001)

    def test_all_metrics_present(self) -> None:
        """Test that all expected metrics are written."""
        trades = [_create_trade(100)]
        result = _create_result(trades)
        metrics = compute_metrics(result)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            path = Path(f.name)
            write_summary_json(metrics, path)

        with open(path) as f:
            loaded = json.load(f)

        required_fields = [
            "initial_equity",
            "final_equity",
            "total_return",
            "total_trades",
            "win_rate",
            "max_drawdown",
            "expectancy",
            "profit_factor",
            "avg_r_multiple",
            "max_consecutive_losses",
            "monthly_returns",
        ]
        for field in required_fields:
            assert field in loaded, f"Missing field: {field}"


class TestGenerateReport:
    """Tests for generate_report function."""

    def test_creates_output_directory(self) -> None:
        """Test that output directory is created if it doesn't exist."""
        trades = [_create_trade(100)]
        result = _create_result(trades)

        with _workspace_tmpdir("backtester_reporting") as tmpdir:
            out_dir = tmpdir / "new_dir" / "nested"
            generate_report(result, out_dir, "BTCUSDT")

            assert out_dir.exists()
            assert out_dir.is_dir()

    def test_generates_all_files(self) -> None:
        """Test that all three output files are generated."""
        trades = [_create_trade(100)]
        result = _create_result(trades)

        with _workspace_tmpdir("backtester_reporting") as tmpdir:
            out_dir = tmpdir / "report"
            generate_report(result, out_dir, "BTCUSDT")

            assert (out_dir / "trades.csv").exists()
            assert (out_dir / "equity.csv").exists()
            assert (out_dir / "summary.json").exists()

    def test_report_is_deterministic(self) -> None:
        """Test that same inputs produce identical reports."""
        trades = [_create_trade(100)]
        result = _create_result(trades)

        with _workspace_tmpdir("backtester_reporting") as tmpdir:
            out_dir1 = tmpdir / "report1"
            generate_report(result, out_dir1, "BTCUSDT")

            out_dir2 = tmpdir / "report2"
            generate_report(result, out_dir2, "BTCUSDT")

            # Compare summary.json contents (excluding timestamp for determinism)
            with open(out_dir1 / "summary.json") as f1, open(out_dir2 / "summary.json") as f2:
                content1 = json.load(f1)
                content2 = json.load(f2)

            # Remove timestamp for comparison (it differs between runs)
            content1.pop("generated_at", None)
            content2.pop("generated_at", None)

            assert content1 == content2

            # Compare trades.csv contents
            with open(out_dir1 / "trades.csv") as f1, open(out_dir2 / "trades.csv") as f2:
                rows1 = list(csv.DictReader(f1))
                rows2 = list(csv.DictReader(f2))

            assert rows1 == rows2


class TestReportingEdgeCases:
    """Tests for edge cases in reporting."""

    def test_single_trade(self) -> None:
        """Test metrics with a single winning trade."""
        trades = [_create_trade(100)]
        result = _create_result(trades)
        metrics = compute_metrics(result)

        assert metrics["total_trades"] == 1
        assert metrics["winning_trades"] == 1
        assert metrics["losing_trades"] == 0
        assert metrics["expectancy"] == 100.0
        # profit_factor is 'inf' string when there are no losses
        assert metrics["profit_factor"] == "inf"

    def test_single_losing_trade(self) -> None:
        """Test metrics with a single losing trade."""
        trades = [_create_trade(-100)]
        result = _create_result(trades)
        metrics = compute_metrics(result)

        assert metrics["total_trades"] == 1
        assert metrics["winning_trades"] == 0
        assert metrics["losing_trades"] == 1
        assert metrics["expectancy"] == -100.0
        assert metrics["profit_factor"] == 0.0
        assert metrics["max_consecutive_losses"] == 1

    def test_profit_factor_inf_with_no_losses(self) -> None:
        """Test profit factor is 'inf' when there are no losses."""
        trades = [_create_trade(100), _create_trade(200)]
        result = _create_result(trades)
        metrics = compute_metrics(result)

        # profit_factor is serialized as 'inf' string
        assert metrics["profit_factor"] == "inf"

    def test_profit_factor_zero_with_no_wins(self) -> None:
        """Test profit factor is 0 when there are no wins."""
        trades = [_create_trade(-100), _create_trade(-200)]
        result = _create_result(trades)
        metrics = compute_metrics(result)

        assert metrics["profit_factor"] == 0.0

    def test_empty_monthly_returns(self) -> None:
        """Test monthly returns is empty dict with no trades."""
        result = _create_result([])
        metrics = compute_metrics(result)

        assert metrics["monthly_returns"] == {}
