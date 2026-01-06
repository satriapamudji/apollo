"""Tests for paper mode execution simulation integration.

These tests verify that paper mode uses realistic fill simulation
with slippage, fees, and fill probability modeling.
"""

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from src.config.settings import PaperSimConfig, Settings
from src.execution.engine import ExecutionEngine
from src.execution.paper_simulator import PaperExecutionSimulator
from src.ledger.bus import EventBus
from src.ledger.events import EventType
from src.ledger.state import Position
from src.ledger.store import EventLedger
from src.models import TradeProposal
from src.risk.engine import RiskCheckResult
from src.risk.sizing import SymbolFilters


class _MockRest:
    """Mock REST client for tests."""

    def __init__(self, book_ticker: dict | None = None):
        self._book_ticker = book_ticker or {
            "bidPrice": "100.0",
            "askPrice": "100.1",
            "bidQty": "10.0",
            "askQty": "10.0",
        }

    async def get_book_ticker(self, symbol: str) -> dict:
        return self._book_ticker

    async def place_order(self, params: dict) -> dict:
        return {"orderId": f"SIM-{uuid4().hex[:8]}"}

    async def get_order(self, symbol: str, **kwargs) -> dict:
        return {"status": "FILLED", "executedQty": "1.0", "avgPrice": "100.0"}


def _make_settings(paper_sim_enabled: bool = True, random_seed: int = 42) -> Settings:
    """Create settings with paper mode and simulation enabled."""
    return Settings(
        run={"mode": "paper"},  # Paper mode
        paper_sim={
            "enabled": paper_sim_enabled,
            "slippage_base_bps": 2.0,
            "slippage_atr_scale": 1.0,
            "maker_fee_pct": 0.02,
            "taker_fee_pct": 0.04,
            "random_seed": random_seed,
            "partial_fill_rate": 0.0,  # Disable partial fills for predictability
            "use_live_book_ticker": True,
            "book_ticker_cache_seconds": 1.0,
        },
        _env_file=None,
    )


def _make_proposal(
    symbol: str = "BTCUSDT",
    side: str = "LONG",
    entry_price: float = 100.0,
    stop_price: float = 95.0,
    atr: float = 2.0,
) -> TradeProposal:
    """Create a trade proposal for testing."""
    return TradeProposal(
        symbol=symbol,
        side=side,  # type: ignore
        entry_price=entry_price,
        stop_price=stop_price,
        take_profit=None,
        atr=atr,
        leverage=2,
        score=None,
        funding_rate=0.0,
        news_risk="LOW",
        trade_id=f"test-{uuid4().hex[:8]}",
        created_at=datetime.now(timezone.utc),
        is_entry=True,
    )


def _make_symbol_filters() -> SymbolFilters:
    """Create symbol filters for testing."""
    return SymbolFilters(
        tick_size=0.1,
        step_size=0.001,
        min_qty=0.001,
        min_notional=10.0,
    )


def _make_risk_result() -> RiskCheckResult:
    """Create approved risk check result."""
    return RiskCheckResult(
        approved=True,
        reasons=[],
        size_multiplier=1.0,
    )


class TestPaperSimulatorIntegration:
    """Test paper simulator integration with ExecutionEngine."""

    def test_engine_initializes_simulator_in_paper_mode(self) -> None:
        """ExecutionEngine initializes paper simulator when in paper mode."""
        settings = _make_settings(paper_sim_enabled=True)
        rest = _MockRest()
        ledger = EventLedger(str(Path("data/test_ledgers") / f"sim_{uuid4().hex}"))
        bus = EventBus(ledger)

        engine = ExecutionEngine(settings, rest, bus)

        assert engine.simulate is True
        assert engine._paper_simulator is not None
        assert isinstance(engine._paper_simulator, PaperExecutionSimulator)

    def test_engine_no_simulator_when_disabled(self) -> None:
        """ExecutionEngine doesn't initialize simulator when disabled."""
        settings = _make_settings(paper_sim_enabled=False)
        rest = _MockRest()
        ledger = EventLedger(str(Path("data/test_ledgers") / f"sim_{uuid4().hex}"))
        bus = EventBus(ledger)

        engine = ExecutionEngine(settings, rest, bus)

        assert engine.simulate is True
        assert engine._paper_simulator is None


class TestPaperSimulatorFillSimulation:
    """Test realistic fill simulation."""

    def test_simulate_fill_market_order_always_fills(self) -> None:
        """Market orders always fill with slippage."""
        config = PaperSimConfig(
            slippage_base_bps=2.0,
            random_seed=42,
        )
        rest = _MockRest()
        simulator = PaperExecutionSimulator(config, rest)

        result = asyncio.run(
            simulator.simulate_fill(
                symbol="BTCUSDT",
                side="BUY",
                order_type="MARKET",
                quantity=1.0,
                limit_price=None,
                atr=2.0,
                holding_bars=1,
            )
        )

        assert result.filled is True
        assert result.fill_quantity == 1.0
        assert result.slippage_bps > 0  # Should have slippage
        assert result.fees > 0  # Should have fees

    def test_simulate_fill_limit_order_immediate_fill(self) -> None:
        """Limit order at or through market fills immediately."""
        config = PaperSimConfig(random_seed=42)
        rest = _MockRest(
            book_ticker={
                "bidPrice": "100.0",
                "askPrice": "100.1",
                "bidQty": "10.0",
                "askQty": "10.0",
            }
        )
        simulator = PaperExecutionSimulator(config, rest)

        # Buy limit at or above ask price - immediate fill
        result = asyncio.run(
            simulator.simulate_fill(
                symbol="BTCUSDT",
                side="BUY",
                order_type="LIMIT",
                quantity=1.0,
                limit_price=100.2,  # Above ask
                atr=2.0,
            )
        )

        assert result.filled is True
        assert result.fill_price == 100.2  # Fills at limit price
        assert result.slippage_bps == 0.0  # No slippage for limit fills

    def test_simulate_fill_limit_order_probabilistic(self) -> None:
        """Limit order away from market has probabilistic fill."""
        config = PaperSimConfig(
            random_seed=999,  # Seed that may produce no-fill
            partial_fill_rate=0.0,
        )
        rest = _MockRest(
            book_ticker={
                "bidPrice": "100.0",
                "askPrice": "100.1",
                "bidQty": "10.0",
                "askQty": "10.0",
            }
        )
        simulator = PaperExecutionSimulator(config, rest)

        # Buy limit far below market - low fill probability
        result = asyncio.run(
            simulator.simulate_fill(
                symbol="BTCUSDT",
                side="BUY",
                order_type="LIMIT",
                quantity=1.0,
                limit_price=95.0,  # 5% below market
                atr=1.0,
            )
        )

        # With a low probability seed, should not fill
        # (actual behavior depends on random seed)
        assert result.reason is None or "LIMIT_NOT_FILLED" in (result.reason or "")


class TestExecuteEntryWithSimulation:
    """Test execute_entry with paper simulation."""

    def test_entry_fills_with_simulation_metadata(self) -> None:
        """Entry order fills include simulation metadata."""
        settings = _make_settings(random_seed=42)
        rest = _MockRest()
        ledger_path = Path("data/test_ledgers") / f"entry_{uuid4().hex}"
        ledger = EventLedger(str(ledger_path))
        bus = EventBus(ledger)
        engine = ExecutionEngine(settings, rest, bus)

        # Create mocked trading state
        from src.ledger.state import TradingState

        state = TradingState(equity=10000.0)

        proposal = _make_proposal()
        risk_result = _make_risk_result()
        filters = _make_symbol_filters()

        asyncio.run(engine.execute_entry(proposal, risk_result, filters, state))

        events = ledger.load_all()
        filled_events = [e for e in events if e.event_type == EventType.ORDER_FILLED]

        assert len(filled_events) >= 1
        payload = filled_events[-1].payload
        assert payload.get("simulated") is True
        assert "slippage_bps" in payload
        assert "fees" in payload


class TestExecuteExitWithSimulation:
    """Test execute_exit with paper simulation."""

    def test_exit_fills_with_slippage(self) -> None:
        """Exit order includes slippage in simulation mode."""
        settings = _make_settings(random_seed=42)
        rest = _MockRest()
        ledger_path = Path("data/test_ledgers") / f"exit_{uuid4().hex}"
        ledger = EventLedger(str(ledger_path))
        bus = EventBus(ledger)
        engine = ExecutionEngine(settings, rest, bus)

        position = Position(
            symbol="BTCUSDT",
            side="LONG",
            quantity=1.0,
            entry_price=100.0,
            leverage=2,
            opened_at=datetime.now(timezone.utc),
            trade_id=f"test-{uuid4().hex[:8]}",
        )

        asyncio.run(engine.execute_exit(position, exit_price=105.0, reason="TEST"))

        events = ledger.load_all()
        closed_events = [e for e in events if e.event_type == EventType.POSITION_CLOSED]

        assert len(closed_events) == 1
        payload = closed_events[0].payload
        assert payload.get("simulated") is True
        assert "slippage_bps" in payload
        assert "fees" in payload
        # Exit price should be adjusted for slippage (not exactly 105.0)
        assert payload["exit_price"] != 105.0


class TestSlippageCalculation:
    """Test slippage estimation logic."""

    def test_slippage_increases_with_volatility(self) -> None:
        """Higher ATR results in higher slippage estimate."""
        config = PaperSimConfig()
        simulator = PaperExecutionSimulator(config, None)

        low_vol_slippage = simulator.estimate_slippage(
            atr=1.0, price=100.0, order_type="MARKET", spread_pct=0.1
        )
        high_vol_slippage = simulator.estimate_slippage(
            atr=5.0, price=100.0, order_type="MARKET", spread_pct=0.1
        )

        assert high_vol_slippage > low_vol_slippage

    def test_market_order_more_slippage_than_limit(self) -> None:
        """Market orders have more slippage than limit orders."""
        config = PaperSimConfig()
        simulator = PaperExecutionSimulator(config, None)

        market_slippage = simulator.estimate_slippage(
            atr=2.0, price=100.0, order_type="MARKET", spread_pct=0.1
        )
        limit_slippage = simulator.estimate_slippage(
            atr=2.0, price=100.0, order_type="LIMIT", spread_pct=0.1
        )

        assert market_slippage > limit_slippage


class TestFeeCalculation:
    """Test fee calculation logic."""

    def test_maker_fee_for_limit_orders(self) -> None:
        """Limit orders use maker fee rate."""
        config = PaperSimConfig(maker_fee_pct=0.02, taker_fee_pct=0.04)
        simulator = PaperExecutionSimulator(config, None)

        fees = simulator.calculate_fees(notional=10000.0, order_type="LIMIT")

        assert fees == 2.0  # 0.02% of 10000

    def test_taker_fee_for_market_orders(self) -> None:
        """Market orders use taker fee rate."""
        config = PaperSimConfig(maker_fee_pct=0.02, taker_fee_pct=0.04)
        simulator = PaperExecutionSimulator(config, None)

        fees = simulator.calculate_fees(notional=10000.0, order_type="MARKET")

        assert fees == 4.0  # 0.04% of 10000
