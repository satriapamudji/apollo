"""Tests for backtester execution simulation models."""

import pytest

from src.backtester.execution_sim import (
    ExecutionSimulator,
    VolatilityRegime,
    detect_volatility_regime,
    estimate_fill_probability,
    estimate_slippage,
)


class TestDetectVolatilityRegime:
    """Tests for volatility regime detection."""

    def test_low_volatility(self) -> None:
        """ATR% < 0.5% should be LOW regime."""
        assert detect_volatility_regime(0.001) == VolatilityRegime.LOW
        assert detect_volatility_regime(0.004) == VolatilityRegime.LOW
        assert detect_volatility_regime(0.0049) == VolatilityRegime.LOW

    def test_normal_volatility(self) -> None:
        """ATR% between 0.5% and 1.5% should be NORMAL regime."""
        assert detect_volatility_regime(0.005) == VolatilityRegime.NORMAL
        assert detect_volatility_regime(0.01) == VolatilityRegime.NORMAL
        assert detect_volatility_regime(0.014) == VolatilityRegime.NORMAL

    def test_high_volatility(self) -> None:
        """ATR% > 1.5% should be HIGH regime."""
        assert detect_volatility_regime(0.015) == VolatilityRegime.HIGH
        assert detect_volatility_regime(0.02) == VolatilityRegime.HIGH
        assert detect_volatility_regime(0.05) == VolatilityRegime.HIGH


class TestEstimateSlippage:
    """Tests for slippage estimation."""

    def test_base_slippage(self) -> None:
        """Test base slippage at 2 bps."""
        # At price 100, ATR 1 (1%), LOW regime, LIMIT order
        slippage = estimate_slippage(
            atr=1.0,
            price=100.0,
            order_type="LIMIT",
            volatility_regime=VolatilityRegime.LOW,
            base_bps=2.0,
        )
        # Base: 2 bps = 0.0002, ATR component: 1%/100 * 1 = 0.0001, LOW multiplier: 0.5
        expected = (0.0002 + 0.0001) * 0.5
        assert abs(slippage - expected) < 0.000001

    def test_market_order_penalty(self) -> None:
        """Market orders should have higher slippage than limit orders."""
        limit_slippage = estimate_slippage(
            atr=1.0,
            price=100.0,
            order_type="LIMIT",
            volatility_regime=VolatilityRegime.NORMAL,
            base_bps=2.0,
        )
        market_slippage = estimate_slippage(
            atr=1.0,
            price=100.0,
            order_type="MARKET",
            volatility_regime=VolatilityRegime.NORMAL,
            base_bps=2.0,
        )
        # Market should have +0.0003 penalty
        assert market_slippage - limit_slippage == pytest.approx(0.0003)

    def test_volatility_multipliers(self) -> None:
        """Higher volatility regimes should increase slippage."""
        low_slippage = estimate_slippage(
            atr=1.0,
            price=100.0,
            order_type="LIMIT",
            volatility_regime=VolatilityRegime.LOW,
            base_bps=2.0,
        )
        high_slippage = estimate_slippage(
            atr=1.0,
            price=100.0,
            order_type="LIMIT",
            volatility_regime=VolatilityRegime.HIGH,
            base_bps=2.0,
        )
        # HIGH should be 4x LOW (2.0 / 0.5 = 4)
        assert high_slippage > low_slippage
        assert high_slippage / low_slippage == pytest.approx(4.0, rel=0.01)

    def test_atr_scaling(self) -> None:
        """Higher ATR should increase slippage."""
        low_atr_slippage = estimate_slippage(
            atr=0.5,
            price=100.0,
            order_type="LIMIT",
            volatility_regime=VolatilityRegime.NORMAL,
            base_bps=2.0,
            atr_scale=1.0,
        )
        high_atr_slippage = estimate_slippage(
            atr=2.0,
            price=100.0,
            order_type="LIMIT",
            volatility_regime=VolatilityRegime.NORMAL,
            base_bps=2.0,
            atr_scale=1.0,
        )
        # Higher ATR = higher slippage
        assert high_atr_slippage > low_atr_slippage


class TestEstimateFillProbability:
    """Tests for fill probability estimation."""

    def test_aggressive_limit_high_prob(self) -> None:
        """Aggressive limits (within 5 bps) should have high fill probability."""
        prob = estimate_fill_probability(
            limit_distance_bps=3.0,
            holding_time_bars=1,
            volatility=0.01,
        )
        assert prob > 0.7

    def test_passive_limit_low_prob(self) -> None:
        """Passive limits (20+ bps) should have low fill probability."""
        prob = estimate_fill_probability(
            limit_distance_bps=25.0,
            holding_time_bars=1,
            volatility=0.01,
        )
        assert prob < 0.5

    def test_time_bonus(self) -> None:
        """Longer holding time should increase fill probability."""
        prob_1_bar = estimate_fill_probability(
            limit_distance_bps=10.0,
            holding_time_bars=1,
            volatility=0.01,
        )
        prob_5_bars = estimate_fill_probability(
            limit_distance_bps=10.0,
            holding_time_bars=5,
            volatility=0.01,
        )
        assert prob_5_bars > prob_1_bar

    def test_volatility_bonus(self) -> None:
        """High volatility should increase fill probability."""
        low_vol_prob = estimate_fill_probability(
            limit_distance_bps=10.0,
            holding_time_bars=1,
            volatility=0.005,  # 0.5%
        )
        high_vol_prob = estimate_fill_probability(
            limit_distance_bps=10.0,
            holding_time_bars=1,
            volatility=0.025,  # 2.5%
        )
        assert high_vol_prob > low_vol_prob

    def test_probability_bounds(self) -> None:
        """Fill probability should always be between 0 and 1."""
        # Very passive limit, short time
        prob_min = estimate_fill_probability(
            limit_distance_bps=100.0,
            holding_time_bars=1,
            volatility=0.005,
        )
        assert 0.0 <= prob_min <= 1.0

        # Very aggressive limit, long time, high volatility
        prob_max = estimate_fill_probability(
            limit_distance_bps=1.0,
            holding_time_bars=10,
            volatility=0.03,
        )
        assert 0.0 <= prob_max <= 1.0


class TestExecutionSimulator:
    """Tests for the execution simulator."""

    def test_reproducibility_with_seed(self) -> None:
        """Same seed should produce same results."""
        sim1 = ExecutionSimulator(random_seed=42)
        sim2 = ExecutionSimulator(random_seed=42)

        # Run multiple times and compare
        for _ in range(100):
            filled1, price1, _ = sim1.fill_order(
                proposal_price=100.0,
                current_price=101.0,
                order_type="LIMIT",
                atr=1.0,
                atr_pct=0.01,
            )
            filled2, price2, _ = sim2.fill_order(
                proposal_price=100.0,
                current_price=101.0,
                order_type="LIMIT",
                atr=1.0,
                atr_pct=0.01,
            )
            assert filled1 == filled2
            assert price1 == price2

    def test_different_seeds_different_results(self) -> None:
        """Different seeds should produce different random sequences."""
        sim1 = ExecutionSimulator(random_seed=42)
        sim2 = ExecutionSimulator(random_seed=123)

        results1 = []
        results2 = []

        for _ in range(100):
            filled1, _, _ = sim1.fill_order(
                proposal_price=100.0,
                current_price=101.0,
                order_type="LIMIT",
                atr=1.0,
                atr_pct=0.01,
            )
            filled2, _, _ = sim2.fill_order(
                proposal_price=100.0,
                current_price=101.0,
                order_type="LIMIT",
                atr=1.0,
                atr_pct=0.01,
            )
            results1.append(filled1)
            results2.append(filled2)

        # At least some results should differ
        assert results1 != results2

    def test_market_orders_always_fill(self) -> None:
        """Market orders should always fill."""
        sim = ExecutionSimulator(random_seed=42)
        for _ in range(100):
            filled, _, _ = sim.fill_order(
                proposal_price=100.0,
                current_price=101.0,
                order_type="MARKET",
                atr=1.0,
                atr_pct=0.01,
            )
            assert filled

    def test_partial_fill_possible(self) -> None:
        """Partial fills should sometimes occur when orders fill."""
        sim = ExecutionSimulator(random_seed=42)
        partial_count = 0
        fill_count = 0

        for _ in range(1000):
            filled, _, is_partial = sim.fill_order(
                proposal_price=100.0,
                current_price=101.0,
                order_type="LIMIT",
                atr=1.0,
                atr_pct=0.01,
            )
            if filled:
                fill_count += 1
                if is_partial:
                    partial_count += 1

        # Should have some fills and some partials
        assert fill_count > 0
        assert partial_count > 0
        # Partial rate should be around 50% of fills
        assert 0.3 < (partial_count / fill_count) < 0.7

    def test_reset_seed(self) -> None:
        """Reset seed should restart the random sequence."""
        sim = ExecutionSimulator(random_seed=42)

        # Run 10 times
        results1 = []
        for _ in range(10):
            filled, _, _ = sim.fill_order(
                proposal_price=100.0,
                current_price=101.0,
                order_type="LIMIT",
                atr=1.0,
                atr_pct=0.01,
            )
            results1.append(filled)

        # Reset seed and run again
        sim.reset_seed(42)
        results2 = []
        for _ in range(10):
            filled, _, _ = sim.fill_order(
                proposal_price=100.0,
                current_price=101.0,
                order_type="LIMIT",
                atr=1.0,
                atr_pct=0.01,
            )
            results2.append(filled)

        # Results should be identical after reset
        assert results1 == results2
