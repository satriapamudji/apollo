"""Tests for spread modeling and spread-aware execution simulation."""

from datetime import datetime, timezone

import pandas as pd
import pytest

from src.backtester.execution_sim import ExecutionSimulator
from src.backtester.spread import (
    ConservativeSpreadModel,
    HistoricalSpreadProvider,
    HybridSpreadProvider,
    SpreadSnapshot,
)


class TestSpreadSnapshot:
    """Tests for SpreadSnapshot dataclass."""

    def test_mid_price_calculation(self) -> None:
        """Mid price should be average of bid and ask."""
        snapshot = SpreadSnapshot(
            timestamp=datetime.now(timezone.utc),
            symbol="BTCUSDT",
            bid=100.0,
            ask=100.10,
        )
        assert snapshot.mid == 100.05

    def test_spread_pct_calculation(self) -> None:
        """Spread percentage should be (ask-bid)/mid * 100."""
        snapshot = SpreadSnapshot(
            timestamp=datetime.now(timezone.utc),
            symbol="BTCUSDT",
            bid=100.0,
            ask=100.10,
        )
        # Spread = 0.10, mid = 100.05
        # spread_pct = (0.10 / 100.05) * 100 ≈ 0.0999%
        assert snapshot.spread_pct == pytest.approx(0.0999, rel=0.01)

    def test_spread_decimal_calculation(self) -> None:
        """Spread decimal should be spread_pct / 100."""
        snapshot = SpreadSnapshot(
            timestamp=datetime.now(timezone.utc),
            symbol="BTCUSDT",
            bid=100.0,
            ask=100.10,
        )
        assert snapshot.spread_decimal == pytest.approx(snapshot.spread_pct / 100)

    def test_modeled_flag_default_false(self) -> None:
        """is_modeled should default to False."""
        snapshot = SpreadSnapshot(
            timestamp=datetime.now(timezone.utc),
            symbol="BTCUSDT",
            bid=100.0,
            ask=100.10,
        )
        assert snapshot.is_modeled is False

    def test_zero_mid_returns_zero_spread(self) -> None:
        """Zero mid price should return 0% spread to avoid division by zero."""
        snapshot = SpreadSnapshot(
            timestamp=datetime.now(timezone.utc),
            symbol="BTCUSDT",
            bid=0.0,
            ask=0.0,
        )
        assert snapshot.spread_pct == 0.0


class TestConservativeSpreadModel:
    """Tests for ConservativeSpreadModel."""

    def test_base_spread_applied(self) -> None:
        """Base spread should be applied in calm conditions."""
        model = ConservativeSpreadModel(base_spread_pct=0.02, atr_scale=0.0)
        snapshot = model.get_spread(
            symbol="BTCUSDT",
            timestamp=datetime.now(timezone.utc),
            price=100.0,
            atr=0.3,  # LOW volatility (0.3%)
        )
        # With atr_scale=0, only base spread applies
        # LOW regime multiplier = 0.5
        # spread_pct = 0.02 * 0.5 = 0.01%
        assert snapshot.spread_pct == pytest.approx(0.01, rel=0.01)

    def test_atr_scaling_increases_spread(self) -> None:
        """Higher ATR should increase spread."""
        model = ConservativeSpreadModel(base_spread_pct=0.01, atr_scale=0.5)

        # Low ATR
        low_atr_snapshot = model.get_spread(
            symbol="BTCUSDT",
            timestamp=datetime.now(timezone.utc),
            price=100.0,
            atr=0.5,  # 0.5% ATR
        )

        # High ATR
        high_atr_snapshot = model.get_spread(
            symbol="BTCUSDT",
            timestamp=datetime.now(timezone.utc),
            price=100.0,
            atr=2.0,  # 2% ATR
        )

        assert high_atr_snapshot.spread_pct > low_atr_snapshot.spread_pct

    def test_volatility_regime_multipliers(self) -> None:
        """Different volatility regimes should apply different multipliers."""
        model = ConservativeSpreadModel(base_spread_pct=0.02, atr_scale=0.0)

        # LOW regime (ATR < 0.5%)
        low = model.get_spread(
            symbol="BTCUSDT",
            timestamp=datetime.now(timezone.utc),
            price=100.0,
            atr=0.3,  # 0.3% ATR
        )

        # NORMAL regime (ATR 0.5-1.5%)
        normal = model.get_spread(
            symbol="BTCUSDT",
            timestamp=datetime.now(timezone.utc),
            price=100.0,
            atr=1.0,  # 1% ATR
        )

        # HIGH regime (ATR > 1.5%)
        high = model.get_spread(
            symbol="BTCUSDT",
            timestamp=datetime.now(timezone.utc),
            price=100.0,
            atr=2.0,  # 2% ATR
        )

        # Multipliers: LOW=0.5x, NORMAL=1.0x, HIGH=2.0x
        assert normal.spread_pct > low.spread_pct
        assert high.spread_pct > normal.spread_pct
        # HIGH should be ~4x LOW (2.0/0.5)
        assert high.spread_pct / low.spread_pct == pytest.approx(4.0, rel=0.01)

    def test_modeled_flag_set(self) -> None:
        """Model-generated snapshots should have is_modeled=True."""
        model = ConservativeSpreadModel()
        snapshot = model.get_spread(
            symbol="BTCUSDT",
            timestamp=datetime.now(timezone.utc),
            price=100.0,
            atr=1.0,
        )
        assert snapshot.is_modeled is True

    def test_bid_ask_symmetric_around_mid(self) -> None:
        """Bid and ask should be symmetric around mid price (= input price)."""
        model = ConservativeSpreadModel()
        snapshot = model.get_spread(
            symbol="BTCUSDT",
            timestamp=datetime.now(timezone.utc),
            price=100.0,
            atr=1.0,
        )
        # Mid should equal input price
        assert snapshot.mid == pytest.approx(100.0, rel=0.001)
        # Distance from mid should be equal on both sides
        assert (snapshot.ask - snapshot.mid) == pytest.approx(
            snapshot.mid - snapshot.bid, rel=0.001
        )

    def test_zero_price_edge_case(self) -> None:
        """Zero price should return zero bid/ask."""
        model = ConservativeSpreadModel()
        snapshot = model.get_spread(
            symbol="BTCUSDT",
            timestamp=datetime.now(timezone.utc),
            price=0.0,
            atr=1.0,
        )
        assert snapshot.bid == 0.0
        assert snapshot.ask == 0.0


class TestHistoricalSpreadProvider:
    """Tests for HistoricalSpreadProvider."""

    def test_exact_timestamp_match(self) -> None:
        """Should return spread for exact timestamp match."""
        ts = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        df = pd.DataFrame(
            {
                "symbol": ["BTCUSDT"],
                "bid": [99.95],
                "ask": [100.05],
            },
            index=pd.DatetimeIndex([ts], name="timestamp"),
        )
        provider = HistoricalSpreadProvider(df)

        snapshot = provider.get_spread(
            symbol="BTCUSDT",
            timestamp=ts,
            price=100.0,
            atr=1.0,
        )

        assert snapshot is not None
        assert snapshot.bid == 99.95
        assert snapshot.ask == 100.05
        assert snapshot.is_modeled is False

    def test_forward_fill_no_lookahead(self) -> None:
        """Should return most recent spread at or before timestamp (no lookahead)."""
        ts1 = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        ts2 = datetime(2024, 1, 1, 12, 30, 0, tzinfo=timezone.utc)

        df = pd.DataFrame(
            {
                "symbol": ["BTCUSDT", "BTCUSDT"],
                "bid": [99.95, 99.90],
                "ask": [100.05, 100.10],
            },
            index=pd.DatetimeIndex([ts1, ts2], name="timestamp"),
        )
        provider = HistoricalSpreadProvider(df)

        # Query between the two timestamps - should get the earlier one
        query_ts = datetime(2024, 1, 1, 12, 15, 0, tzinfo=timezone.utc)
        snapshot = provider.get_spread(
            symbol="BTCUSDT",
            timestamp=query_ts,
            price=100.0,
            atr=1.0,
        )

        assert snapshot is not None
        assert snapshot.bid == 99.95  # First spread, not second

    def test_before_first_timestamp_returns_none(self) -> None:
        """Should return None if query is before all data."""
        ts = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        df = pd.DataFrame(
            {
                "symbol": ["BTCUSDT"],
                "bid": [99.95],
                "ask": [100.05],
            },
            index=pd.DatetimeIndex([ts], name="timestamp"),
        )
        provider = HistoricalSpreadProvider(df)

        # Query before data starts
        query_ts = datetime(2024, 1, 1, 11, 0, 0, tzinfo=timezone.utc)
        snapshot = provider.get_spread(
            symbol="BTCUSDT",
            timestamp=query_ts,
            price=100.0,
            atr=1.0,
        )

        assert snapshot is None

    def test_empty_data_returns_none(self) -> None:
        """Should return None for empty DataFrame."""
        df = pd.DataFrame(columns=["symbol", "bid", "ask"])
        df.index = pd.DatetimeIndex([], name="timestamp")
        provider = HistoricalSpreadProvider(df)

        snapshot = provider.get_spread(
            symbol="BTCUSDT",
            timestamp=datetime.now(timezone.utc),
            price=100.0,
            atr=1.0,
        )

        assert snapshot is None

    def test_symbol_filtering(self) -> None:
        """Should filter by symbol when multiple symbols in data."""
        ts = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        df = pd.DataFrame(
            {
                "symbol": ["BTCUSDT", "ETHUSDT"],
                "bid": [99.95, 1999.0],
                "ask": [100.05, 2001.0],
            },
            index=pd.DatetimeIndex([ts, ts], name="timestamp"),
        )
        provider = HistoricalSpreadProvider(df)

        btc_snapshot = provider.get_spread(
            symbol="BTCUSDT",
            timestamp=ts,
            price=100.0,
            atr=1.0,
        )
        eth_snapshot = provider.get_spread(
            symbol="ETHUSDT",
            timestamp=ts,
            price=2000.0,
            atr=20.0,
        )

        assert btc_snapshot is not None
        assert btc_snapshot.bid == 99.95
        assert eth_snapshot is not None
        assert eth_snapshot.bid == 1999.0


class TestHybridSpreadProvider:
    """Tests for HybridSpreadProvider."""

    def test_uses_historical_when_available(self) -> None:
        """Should use historical spread when data exists."""
        ts = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        df = pd.DataFrame(
            {
                "symbol": ["BTCUSDT"],
                "bid": [99.95],
                "ask": [100.05],
            },
            index=pd.DatetimeIndex([ts], name="timestamp"),
        )
        historical = HistoricalSpreadProvider(df)
        hybrid = HybridSpreadProvider(historical_provider=historical)

        snapshot = hybrid.get_spread(
            symbol="BTCUSDT",
            timestamp=ts,
            price=100.0,
            atr=1.0,
        )

        assert snapshot.is_modeled is False
        assert snapshot.bid == 99.95

    def test_falls_back_to_model(self) -> None:
        """Should fall back to model when no historical data."""
        ts = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        df = pd.DataFrame(columns=["symbol", "bid", "ask"])
        df.index = pd.DatetimeIndex([], name="timestamp")
        historical = HistoricalSpreadProvider(df)
        hybrid = HybridSpreadProvider(historical_provider=historical)

        snapshot = hybrid.get_spread(
            symbol="BTCUSDT",
            timestamp=ts,
            price=100.0,
            atr=1.0,
        )

        assert snapshot.is_modeled is True

    def test_no_historical_provider_uses_model(self) -> None:
        """Should use model when no historical provider configured."""
        hybrid = HybridSpreadProvider(historical_provider=None)

        snapshot = hybrid.get_spread(
            symbol="BTCUSDT",
            timestamp=datetime.now(timezone.utc),
            price=100.0,
            atr=1.0,
        )

        assert snapshot.is_modeled is True


class TestSlippageDirection:
    """Tests for directional slippage in execution simulation."""

    def test_buy_slippage_increases_price(self) -> None:
        """BUY orders should have slippage that increases fill price (adverse)."""
        sim = ExecutionSimulator(random_seed=42)

        filled, fill_price, _ = sim.fill_order(
            proposal_price=100.0,
            current_price=100.0,
            order_type="MARKET",
            atr=1.0,
            atr_pct=0.01,
            side="BUY",
        )

        assert filled
        assert fill_price > 100.0, "BUY fill price should be higher due to adverse slippage"

    def test_sell_slippage_decreases_price(self) -> None:
        """SELL orders should have slippage that decreases fill price (adverse)."""
        sim = ExecutionSimulator(random_seed=42)

        filled, fill_price, _ = sim.fill_order(
            proposal_price=100.0,
            current_price=100.0,
            order_type="MARKET",
            atr=1.0,
            atr_pct=0.01,
            side="SELL",
        )

        assert filled
        assert fill_price < 100.0, "SELL fill price should be lower due to adverse slippage"

    def test_buy_and_sell_slippage_symmetric(self) -> None:
        """BUY and SELL slippage should be symmetric (same absolute impact)."""
        sim1 = ExecutionSimulator(random_seed=42)
        sim2 = ExecutionSimulator(random_seed=42)

        _, buy_price, _ = sim1.fill_order(
            proposal_price=100.0,
            current_price=100.0,
            order_type="MARKET",
            atr=1.0,
            atr_pct=0.01,
            side="BUY",
        )
        _, sell_price, _ = sim2.fill_order(
            proposal_price=100.0,
            current_price=100.0,
            order_type="MARKET",
            atr=1.0,
            atr_pct=0.01,
            side="SELL",
        )

        # Distance from proposal price should be equal
        buy_slip = buy_price - 100.0
        sell_slip = 100.0 - sell_price
        assert buy_slip == pytest.approx(sell_slip, rel=0.001)


class TestHalfSpreadFloor:
    """Tests for half-spread slippage floor."""

    def test_half_spread_floor_for_market_orders(self) -> None:
        """Market orders should use half-spread as slippage floor."""
        sim = ExecutionSimulator(random_seed=42)

        # Very wide spread (1% = 100 bps)
        # Half-spread = 0.5% = 0.005 decimal
        _, fill_price, _ = sim.fill_order(
            proposal_price=100.0,
            current_price=100.0,
            order_type="MARKET",
            atr=0.1,  # Very low ATR
            atr_pct=0.001,  # Very low volatility -> low model slippage
            side="BUY",
            spread_pct=1.0,  # 1% spread -> 0.5% half-spread
        )

        # With 1% spread, half-spread = 0.5%
        # Fill price should be at least 100 * 1.005 = 100.5
        # Use small tolerance for floating point comparison
        assert fill_price >= 100.5 - 1e-9, "Half-spread floor should apply"

    def test_half_spread_floor_for_aggressive_limit(self) -> None:
        """Aggressive limit orders (within 5 bps) should use half-spread floor."""
        sim = ExecutionSimulator(random_seed=42)

        # Run enough times to get a fill
        fill_count = 0
        for _ in range(100):
            filled, fill_price, _ = sim.fill_order(
                proposal_price=100.0,
                current_price=100.0,
                order_type="LIMIT",
                atr=0.1,
                atr_pct=0.001,
                side="BUY",
                limit_distance_bps=3.0,  # Aggressive limit
                spread_pct=1.0,
            )
            if filled:
                fill_count += 1
                # When filled, half-spread floor should apply
                # Use small tolerance for floating point comparison
                assert fill_price >= 100.5 - 1e-9, (
                    "Half-spread floor should apply to aggressive limits"
                )

        assert fill_count > 0, "Some limit orders should fill"

    def test_model_slippage_used_when_greater_than_half_spread(self) -> None:
        """Model slippage should be used when greater than half-spread."""
        sim = ExecutionSimulator(random_seed=42)

        # Tiny spread (0.01% = 1 bp), but HIGH volatility -> high model slippage
        _, fill_price, _ = sim.fill_order(
            proposal_price=100.0,
            current_price=100.0,
            order_type="MARKET",
            atr=3.0,  # 3% ATR -> HIGH regime
            atr_pct=0.03,
            side="BUY",
            spread_pct=0.01,  # Tiny spread
        )

        # Half-spread = 0.005% = 0.00005 decimal
        # Model slippage should be much higher in HIGH regime
        # Fill price should reflect model slippage, not just half-spread
        assert fill_price > 100.0 + 0.005, "Model slippage should exceed tiny half-spread"

    def test_no_spread_floor_for_passive_limit(self) -> None:
        """Passive limit orders (> 5 bps) should NOT use half-spread floor."""
        sim = ExecutionSimulator(random_seed=42)

        # Run enough times to get fills
        for _ in range(100):
            filled, fill_price, _ = sim.fill_order(
                proposal_price=100.0,
                current_price=100.0,
                order_type="LIMIT",
                atr=0.1,
                atr_pct=0.001,  # Low volatility -> low model slippage
                side="BUY",
                limit_distance_bps=20.0,  # Passive limit (> 5 bps)
                spread_pct=1.0,  # Wide spread
            )
            if filled:
                # For passive limits, only model slippage applies, not half-spread floor
                # The fill should NOT necessarily exceed half-spread
                # This is because passive limits are not "crossing" the spread
                assert fill_price > 100.0, "Some slippage should apply"

    def test_no_spread_pct_ignores_floor(self) -> None:
        """When spread_pct is None, no floor is applied."""
        sim1 = ExecutionSimulator(random_seed=42)
        sim2 = ExecutionSimulator(random_seed=42)

        _, price_without_spread, _ = sim1.fill_order(
            proposal_price=100.0,
            current_price=100.0,
            order_type="MARKET",
            atr=0.1,
            atr_pct=0.001,
            side="BUY",
            spread_pct=None,  # No spread provided
        )

        _, price_with_spread, _ = sim2.fill_order(
            proposal_price=100.0,
            current_price=100.0,
            order_type="MARKET",
            atr=0.1,
            atr_pct=0.001,
            side="BUY",
            spread_pct=1.0,  # 1% spread
        )

        # With spread floor, price should be higher
        assert price_with_spread > price_without_spread
