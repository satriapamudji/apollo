"""Tests for event-driven multi-symbol backtesting engine."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from src.backtester.events import BarEvent, FundingEvent
from src.backtester.backtest_ledger import BacktestLedger, NullLedger
from src.backtester.execution_model import (
    IdealExecution,
    RealisticExecution,
    FillResult,
    create_execution_model,
)
from src.backtester.strategy_runner import (
    StrategyContext,
    SignalBatch,
    MultiSymbolRunner,
)


def make_score():
    """Create a test CompositeScore."""
    from src.strategy.scoring import CompositeScore

    return CompositeScore(
        trend_score=0.5,
        volatility_score=0.5,
        entry_quality=0.5,
        funding_penalty=0.0,
        news_modifier=0.0,
        liquidity_score=0.5,
        crowding_score=0.0,
        funding_volatility_score=0.0,
        oi_expansion_score=0.0,
        taker_imbalance_score=0.0,
        volume_score=0.5,
        composite=1.0,
    )


def make_bar(
    symbol: str,
    ts: datetime,
    close: float = 100.0,
    sequence: int = 0,
) -> BarEvent:
    """Create a test BarEvent."""
    return BarEvent(
        symbol=symbol,
        interval="4h",
        timestamp=ts,
        open=close - 1,
        high=close + 1,
        low=close - 2,
        close=close,
        volume=1000.0,
        sequence=sequence,
    )


class TestBacktestLedger:
    """Tests for BacktestLedger event logging."""

    def test_create_ledger(self, workspace_tmp_path: Path) -> None:
        """Should create ledger in output directory."""
        ledger = BacktestLedger(str(workspace_tmp_path))
        assert ledger.out_dir == workspace_tmp_path
        assert ledger.event_count == 0
        ledger.close()

    def test_append_backtest_event(self, workspace_tmp_path: Path) -> None:
        """Should append and convert backtest events."""
        with BacktestLedger(str(workspace_tmp_path)) as ledger:
            ts = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
            bar = make_bar("BTCUSDT", ts)
            event = ledger.append_backtest_event(bar)

            assert "CANDLE" in event.event_type.value.upper() or "CLOSE" in event.event_type.value.upper()
            assert ledger.event_count == 1

    def test_flush_writes_to_file(self, workspace_tmp_path: Path) -> None:
        """Flush should write buffered events to file."""
        with BacktestLedger(str(workspace_tmp_path), buffer_size=1) as ledger:
            ts = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
            bar = make_bar("BTCUSDT", ts)
            ledger.append_backtest_event(bar)
            ledger.flush()

        events_file = workspace_tmp_path / "events.jsonl"
        assert events_file.exists()
        content = events_file.read_text(encoding="utf-8")
        assert "BTCUSDT" in content

    def test_context_manager(self, workspace_tmp_path: Path) -> None:
        """Context manager should flush on exit."""
        with BacktestLedger(str(workspace_tmp_path)) as ledger:
            ts = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
            bar = make_bar("BTCUSDT", ts)
            ledger.append_backtest_event(bar)

        events_file = workspace_tmp_path / "events.jsonl"
        assert events_file.exists()


class TestNullLedger:
    """Tests for NullLedger (no-op implementation)."""

    def test_null_ledger_discards_events(self) -> None:
        """NullLedger should accept events but discard them."""
        ledger = NullLedger()

        ts = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
        bar = make_bar("BTCUSDT", ts)

        ledger.append_backtest_event(bar)
        ledger.flush()
        ledger.close()

        assert ledger.event_count == 0

    def test_null_ledger_context_manager(self) -> None:
        """NullLedger should work as context manager."""
        with NullLedger() as ledger:
            ts = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
            bar = make_bar("BTCUSDT", ts)
            ledger.append_backtest_event(bar)


class TestIdealExecution:
    """Tests for IdealExecution model."""

    def test_fill_with_slippage(self) -> None:
        """Should fill at price + slippage."""
        from src.models import TradeProposal

        exec_model = IdealExecution(slippage_pct=0.001)  # 10 bps

        proposal = TradeProposal(
            symbol="BTCUSDT",
            side="LONG",
            entry_price=100.0,
            stop_price=95.0,
            take_profit=110.0,
            atr=2.0,
            leverage=2,
            score=make_score(),
            funding_rate=0.0,
            news_risk="LOW",
            trade_id="test-1",
            created_at=datetime.now(timezone.utc),
        )

        result = exec_model.simulate_fill(
            proposal=proposal,
            current_price=100.0,
            atr=2.0,
            requested_quantity=1.0,
        )

        assert result.filled
        assert result.fill_price == pytest.approx(100.1)  # 100 * 1.001
        assert result.fill_quantity == 1.0
        assert result.slippage_bps == 10.0

    def test_short_slippage_direction(self) -> None:
        """Short orders should pay slippage below entry."""
        from src.models import TradeProposal

        exec_model = IdealExecution(slippage_pct=0.001)

        proposal = TradeProposal(
            symbol="BTCUSDT",
            side="SHORT",
            entry_price=100.0,
            stop_price=105.0,
            take_profit=90.0,
            atr=2.0,
            leverage=2,
            score=make_score(),
            funding_rate=0.0,
            news_risk="LOW",
            trade_id="test-2",
            created_at=datetime.now(timezone.utc),
        )

        result = exec_model.simulate_fill(
            proposal=proposal,
            current_price=100.0,
            atr=2.0,
            requested_quantity=1.0,
        )

        assert result.filled
        assert result.fill_price == pytest.approx(99.9)  # 100 * 0.999


class TestRealisticExecution:
    """Tests for RealisticExecution model."""

    def test_deterministic_with_seed(self) -> None:
        """Same seed should produce same results."""
        from src.models import TradeProposal

        proposal = TradeProposal(
            symbol="BTCUSDT",
            side="LONG",
            entry_price=100.0,
            stop_price=95.0,
            take_profit=110.0,
            atr=2.0,
            leverage=2,
            score=make_score(),
            funding_rate=0.0,
            news_risk="LOW",
            trade_id="test-3",
            created_at=datetime.now(timezone.utc),
        )

        exec1 = RealisticExecution(random_seed=42)
        result1 = exec1.simulate_fill(proposal, 100.0, 2.0, 1.0)

        exec2 = RealisticExecution(random_seed=42)
        result2 = exec2.simulate_fill(proposal, 100.0, 2.0, 1.0)

        assert result1.filled == result2.filled
        if result1.filled:
            assert result1.fill_price == result2.fill_price


class TestCreateExecutionModel:
    """Tests for execution model factory."""

    def test_create_ideal(self) -> None:
        """Should create IdealExecution for 'ideal' type."""
        model = create_execution_model(model_type="ideal", slippage_pct=0.001)
        assert isinstance(model, IdealExecution)

    def test_create_realistic(self) -> None:
        """Should create RealisticExecution for 'realistic' type."""
        model = create_execution_model(model_type="realistic", random_seed=42)
        assert isinstance(model, RealisticExecution)


class TestSignalBatch:
    """Tests for SignalBatch signal grouping."""

    def test_get_entry_signals(self) -> None:
        """Should filter to LONG/SHORT signals only."""
        from src.strategy.signals import Signal, SignalType

        score = make_score()

        signals = {
            "BTCUSDT": Signal(
                symbol="BTCUSDT",
                signal_type=SignalType.LONG,
                score=score,
                price=100.0,
                atr=2.0,
            ),
            "ETHUSDT": Signal(
                symbol="ETHUSDT",
                signal_type=SignalType.NONE,
                score=None,
                price=50.0,
                atr=1.0,
            ),
            "SOLUSDT": Signal(
                symbol="SOLUSDT",
                signal_type=SignalType.SHORT,
                score=score,
                price=25.0,
                atr=0.5,
            ),
        }

        batch = SignalBatch(
            timestamp=datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc),
            signals=signals,
        )

        entries = batch.get_entry_signals()
        assert len(entries) == 2
        assert "BTCUSDT" in entries
        assert "SOLUSDT" in entries
        assert "ETHUSDT" not in entries

    def test_get_exit_signals(self) -> None:
        """Should filter to EXIT signals only."""
        from src.strategy.signals import Signal, SignalType

        signals = {
            "BTCUSDT": Signal(
                symbol="BTCUSDT",
                signal_type=SignalType.EXIT,
                score=None,
                price=100.0,
                atr=2.0,
            ),
            "ETHUSDT": Signal(
                symbol="ETHUSDT",
                signal_type=SignalType.LONG,
                score=None,
                price=50.0,
                atr=1.0,
            ),
        }

        batch = SignalBatch(
            timestamp=datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc),
            signals=signals,
        )

        exits = batch.get_exit_signals()
        assert len(exits) == 1
        assert "BTCUSDT" in exits


class TestSymbolState:
    """Tests for per-symbol state tracking."""

    def test_add_bar_to_history(self) -> None:
        """Should accumulate bars in history."""
        from src.backtester.replay_engine import SymbolState

        state = SymbolState(symbol="BTCUSDT")

        ts1 = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
        ts2 = datetime(2024, 1, 1, 4, 0, tzinfo=timezone.utc)

        state.add_bar(make_bar("BTCUSDT", ts1, close=100))
        state.add_bar(make_bar("BTCUSDT", ts2, close=101))

        assert len(state.fourh_history) == 2

    def test_get_history_df(self) -> None:
        """Should convert history to DataFrame."""
        from src.backtester.replay_engine import SymbolState

        state = SymbolState(symbol="BTCUSDT")

        ts1 = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
        ts2 = datetime(2024, 1, 1, 4, 0, tzinfo=timezone.utc)

        state.add_bar(make_bar("BTCUSDT", ts1, close=100))
        state.add_bar(make_bar("BTCUSDT", ts2, close=101))

        df = state.get_history_df()

        assert len(df) == 2
        assert "open" in df.columns
        assert "close" in df.columns
        assert df["close"].iloc[-1] == 101

    def test_history_lookback_limit(self) -> None:
        """Should limit history to lookback window."""
        from src.backtester.replay_engine import SymbolState

        state = SymbolState(symbol="BTCUSDT")

        # Add 300 bars using timedelta to avoid hour overflow
        base_ts = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
        for i in range(300):
            ts = base_ts + timedelta(hours=i * 4)
            state.add_bar(make_bar("BTCUSDT", ts, close=100 + i))

        # Default lookback is 200
        df = state.get_history_df(lookback=200)
        assert len(df) == 200
        # Should have the most recent bars
        assert df["close"].iloc[-1] == 100 + 299


class TestMultiSymbolResult:
    """Tests for multi-symbol result structure."""

    def test_result_structure(self) -> None:
        """Should have all required fields."""
        from src.backtester.replay_engine import MultiSymbolResult

        result = MultiSymbolResult(
            trades=[],
            equity_curve=[],
            total_return=0.0,
            win_rate=0.0,
            max_drawdown=0.0,
            total_trades=0,
            final_equity=100.0,
            initial_equity=100.0,
        )

        assert result.initial_equity == 100.0
        assert result.final_equity == 100.0
        assert result.total_trades == 0
        assert result.symbols_traded == []


class TestDeterminism:
    """Tests for backtest determinism."""

    def test_same_seed_same_results(self) -> None:
        """Same random seed should produce identical results."""
        from src.models import TradeProposal

        proposal = TradeProposal(
            symbol="BTCUSDT",
            side="LONG",
            entry_price=100.0,
            stop_price=95.0,
            take_profit=110.0,
            atr=2.0,
            leverage=2,
            score=make_score(),
            funding_rate=0.0,
            news_risk="LOW",
            trade_id="test-det",
            created_at=datetime.now(timezone.utc),
        )

        # Run multiple fills with same seed
        results1 = []
        results2 = []

        for i in range(10):
            exec1 = RealisticExecution(random_seed=123 + i)
            exec2 = RealisticExecution(random_seed=123 + i)

            r1 = exec1.simulate_fill(proposal, 100.0, 2.0, 1.0)
            r2 = exec2.simulate_fill(proposal, 100.0, 2.0, 1.0)

            results1.append((r1.filled, r1.fill_price))
            results2.append((r2.filled, r2.fill_price))

        assert results1 == results2
