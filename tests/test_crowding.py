"""Tests for crowding data and factor modules."""

import shutil
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from src.data.crowding import CrowdingCache, CrowdingData
from src.strategy.scoring import (
    CrowdingFactor,
    FundingVolatilityFactor,
    OIExpansionFactor,
    TakerImbalanceFactor,
)


class TestCrowdingData:
    """Tests for CrowdingData dataclass."""

    def test_to_dict_and_from_dict(self) -> None:
        """Test serialization round-trip."""
        data = CrowdingData(
            symbol="BTCUSDT",
            timestamp=datetime(2026, 1, 6, 12, 0, 0, tzinfo=timezone.utc),
            open_interest=1000.0,
            open_interest_value=50000.0,
            long_short_ratio=0.7,
            long_account_ratio=0.6,
            short_account_ratio=0.4,
            taker_buy_ratio=0.55,
            taker_sell_ratio=0.45,
            crowding_score=0.8,
        )

        serialized = data.to_dict()
        restored = CrowdingData.from_dict(serialized)

        assert restored.symbol == data.symbol
        assert restored.open_interest == data.open_interest
        assert restored.long_short_ratio == data.long_short_ratio
        assert restored.crowding_score == data.crowding_score

    def test_default_values(self) -> None:
        """Test default values are applied."""
        data = CrowdingData(
            symbol="ETHUSDT",
            timestamp=datetime.now(timezone.utc),
            open_interest=500.0,
        )

        assert data.long_short_ratio == 0.5
        assert data.crowding_score == 0.5
        assert data.funding_volatility_score == 0.5
        assert data.oi_expansion_score == 0.5


class TestCrowdingCache:
    """Tests for CrowdingCache."""

    @pytest.fixture
    def temp_cache_dir(self) -> Path:
        """Create a temporary cache directory."""
        root = Path.cwd() / ".pytest_tmp_crowding" / uuid4().hex
        root.mkdir(parents=True, exist_ok=True)
        try:
            yield root
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_save_and_load_history(self, temp_cache_dir: Path) -> None:
        """Test saving and loading cached data."""
        cache = CrowdingCache(cache_dir=str(temp_cache_dir))

        # Save some data
        data1 = CrowdingData(
            symbol="BTCUSDT",
            timestamp=datetime(2026, 1, 6, 10, 0, 0, tzinfo=timezone.utc),
            open_interest=1000.0,
        )
        data2 = CrowdingData(
            symbol="BTCUSDT",
            timestamp=datetime(2026, 1, 6, 11, 0, 0, tzinfo=timezone.utc),
            open_interest=1100.0,
        )

        cache.save(data1)
        cache.save(data2)

        # Load history
        history = cache.load_history("BTCUSDT", limit=10)

        assert len(history) == 2
        assert history[0].open_interest == 1000.0
        assert history[1].open_interest == 1100.0

    def test_load_nonexistent_symbol(self, temp_cache_dir: Path) -> None:
        """Test loading history for symbol with no data."""
        cache = CrowdingCache(cache_dir=str(temp_cache_dir))

        history = cache.load_history("NOSYMBOL", limit=10)

        assert history == []

    def test_load_history_limit(self, temp_cache_dir: Path) -> None:
        """Test that history respects limit parameter."""
        cache = CrowdingCache(cache_dir=str(temp_cache_dir))

        # Save 5 data points
        for i in range(5):
            data = CrowdingData(
                symbol="BTCUSDT",
                timestamp=datetime(2026, 1, 6, 10 + i, 0, 0, tzinfo=timezone.utc),
                open_interest=1000.0 + i * 100,
            )
            cache.save(data)

        # Load with limit
        history = cache.load_history("BTCUSDT", limit=3)

        assert len(history) == 3
        # Should get the last 3 entries
        assert history[-1].open_interest == 1400.0


class TestCrowdingCacheComputations:
    """Tests for crowding computation methods."""

    @pytest.fixture
    def cache(self) -> CrowdingCache:
        """Create a cache instance."""
        return CrowdingCache(cache_dir="./data/crowding")

    def _make_oi_data(
        self, symbol: str, oi_values: list[float]
    ) -> list[CrowdingData]:
        """Helper to create OI data list."""
        now = datetime.now(timezone.utc)
        return [
            CrowdingData(symbol=symbol, timestamp=now, open_interest=oi)
            for oi in oi_values
        ]

    def test_compute_oi_expansion_neutral(self, cache: CrowdingCache) -> None:
        """Test OI expansion with minimal changes."""
        history = self._make_oi_data("BTCUSDT", [1000.0, 1005.0, 1010.0])

        score = cache.compute_oi_expansion(history)

        # Small changes should result in near-neutral score
        assert 0.45 <= score <= 0.55

    def test_compute_oi_expansion_expansion(self, cache: CrowdingCache) -> None:
        """Test OI expansion with increasing OI."""
        history = self._make_oi_data("BTCUSDT", [1000.0, 1150.0, 1300.0])

        score = cache.compute_oi_expansion(history)

        # Significant expansion should give high score
        assert score > 0.7

    def test_compute_oi_expansion_contraction(self, cache: CrowdingCache) -> None:
        """Test OI expansion with decreasing OI."""
        history = self._make_oi_data("BTCUSDT", [1300.0, 1150.0, 1000.0])

        score = cache.compute_oi_expansion(history)

        # Significant contraction should give low score
        assert score < 0.3

    def test_compute_oi_expansion_empty_history(self, cache: CrowdingCache) -> None:
        """Test OI expansion with empty history."""
        score = cache.compute_oi_expansion([])
        assert score == 0.5

    def test_compute_funding_volatility_stable(self, cache: CrowdingCache) -> None:
        """Test funding volatility with stable rates."""
        base_rate = 0.0001
        # Use exactly the same rate for all to ensure stability
        history = [
            CrowdingData(
                symbol="BTCUSDT",
                timestamp=datetime.now(timezone.utc),
                funding_rate=base_rate,
            )
            for _ in range(8)
        ]

        score = cache.compute_funding_volatility(history)

        # Stable rates (no variance) should give zero volatility
        assert score == 0.0

    def test_compute_funding_volatility_volatile(self, cache: CrowdingCache) -> None:
        """Test funding volatility with varying rates.

        Uses a wider range of funding rates to get a more noticeable volatility.
        """
        # Create rates that vary significantly: 0.0001, 0.0003, 0.0005, etc.
        history = [
            CrowdingData(
                symbol="BTCUSDT",
                timestamp=datetime.now(timezone.utc),
                funding_rate=0.0001 * (1 + i * 2),  # 0.0001, 0.0003, 0.0005, 0.0007, ...
            )
            for i in range(8)
        ]

        score = cache.compute_funding_volatility(history)

        # With significantly varying rates, volatility should be noticeable
        # (The exact threshold depends on the implementation, so we just check it's > 0)
        assert score > 0.0

    def test_compute_crowding_score_normal(self, cache: CrowdingCache) -> None:
        """Test crowding score with normal ratios."""
        data = CrowdingData(
            symbol="BTCUSDT",
            timestamp=datetime.now(timezone.utc),
            open_interest=1000.0,
            long_short_ratio=0.6,  # Not extreme
        )

        score = cache.compute_crowding_score(data, [])

        # Normal ratio should give good score
        assert score > 0.7

    def test_compute_crowding_score_extreme(self, cache: CrowdingCache) -> None:
        """Test crowding score with extreme long ratio."""
        data = CrowdingData(
            symbol="BTCUSDT",
            timestamp=datetime.now(timezone.utc),
            open_interest=1000.0,
            long_short_ratio=0.9,  # Extreme long
        )

        score = cache.compute_crowding_score(data, [])

        # Extreme ratio should penalize score
        assert score < 0.7

    def test_compute_taker_imbalance_score(self, cache: CrowdingCache) -> None:
        """Test taker imbalance computation."""
        # Balanced
        score_balanced = cache.compute_taker_imbalance_score(0.5, 0.5)
        assert score_balanced == 0.5

        # More buying
        score_buy = cache.compute_taker_imbalance_score(0.7, 0.3)
        assert score_buy == 0.7

        # More selling
        score_sell = cache.compute_taker_imbalance_score(0.3, 0.7)
        assert score_sell == 0.3


class TestCrowdingFactor:
    """Tests for CrowdingFactor."""

    def test_normal_ratio(self) -> None:
        """Test with normal long/short ratio."""
        factor = CrowdingFactor(extreme_threshold=0.8)
        score = factor.compute(long_short_ratio=0.6)
        assert score == 1.0

    def test_extreme_long(self) -> None:
        """Test with extreme long ratio (0.99 means 99% of accounts are long).

        The extreme_threshold=0.8 means anything above 0.8 or below 0.2 is extreme.
        0.99 > 0.8 is extreme, so score should be < 1.0.
        """
        factor = CrowdingFactor(extreme_threshold=0.8)
        # 0.99 is normalized, meaning 99% of accounts are long
        # This exceeds the 0.8 threshold, so it should be penalized
        score = factor.compute(long_short_ratio=0.99)
        assert score < 1.0, "0.99 should be penalized as extreme"
        # Penalized score should still be > 0 (not fully crowded)
        assert score > 0.0

    def test_extreme_short(self) -> None:
        """Test with extreme short ratio (0.01 means 1% longs, 99% shorts)."""
        factor = CrowdingFactor(extreme_threshold=0.8)
        score = factor.compute(long_short_ratio=0.01)
        assert score < 1.0
        assert score > 0.0

    def test_with_precomputed_score(self) -> None:
        """Test when pre-computed crowding score is available."""
        factor = CrowdingFactor(extreme_threshold=0.8)
        score = factor.compute(long_short_ratio=0.6, crowding_score=0.3)
        # Should use the pre-computed score
        assert score == 0.3


class TestFundingVolatilityFactor:
    """Tests for FundingVolatilityFactor."""

    def test_with_precomputed_score(self) -> None:
        """Test when pre-computed score is available."""
        factor = FundingVolatilityFactor()
        score = factor.compute(funding_volatility_score=0.4)
        assert score == 0.4

    def test_without_data(self) -> None:
        """Test when no data is available."""
        factor = FundingVolatilityFactor()
        score = factor.compute()
        assert score == 1.0  # Default to stable


class TestOIExpansionFactor:
    """Tests for OIExpansionFactor."""

    def test_with_precomputed_score(self) -> None:
        """Test when pre-computed score is available."""
        factor = OIExpansionFactor()
        score = factor.compute(oi_expansion_score=0.7)
        assert score == 0.7

    def test_without_data(self) -> None:
        """Test when no data is available."""
        factor = OIExpansionFactor()
        score = factor.compute()
        assert score == 0.5  # Default to neutral


class TestTakerImbalanceFactor:
    """Tests for TakerImbalanceFactor."""

    def test_balanced(self) -> None:
        """Test with balanced taker activity."""
        factor = TakerImbalanceFactor()
        score = factor.compute(taker_buy_ratio=0.5, taker_sell_ratio=0.5)
        assert score == 0.5

    def test_buy_pressure(self) -> None:
        """Test with buying pressure."""
        factor = TakerImbalanceFactor()
        score = factor.compute(taker_buy_ratio=0.7, taker_sell_ratio=0.3)
        assert score == 0.7

    def test_sell_pressure(self) -> None:
        """Test with selling pressure."""
        factor = TakerImbalanceFactor()
        score = factor.compute(taker_buy_ratio=0.3, taker_sell_ratio=0.7)
        assert score == 0.3

    def test_with_precomputed_score(self) -> None:
        """Test when pre-computed score is available."""
        factor = TakerImbalanceFactor()
        score = factor.compute(
            taker_buy_ratio=0.5,
            taker_sell_ratio=0.5,
            taker_imbalance_score=0.8,
        )
        assert score == 0.8


class TestCrowdingConfig:
    """Tests for CrowdingConfig."""

    def test_default_values(self) -> None:
        """Test default configuration values."""
        from src.config.settings import CrowdingConfig

        config = CrowdingConfig()

        assert config.enabled is True
        assert config.long_short_extreme == 0.8
        assert config.oi_expansion_threshold == 0.1
        assert config.taker_imbalance_threshold == 0.6
        assert config.funding_volatility_window == 8
        assert config.oi_window == 3
        assert config.cache_dir == "./data/crowding"

    def test_custom_values(self) -> None:
        """Test custom configuration values."""
        from src.config.settings import CrowdingConfig

        config = CrowdingConfig(
            enabled=False,
            long_short_extreme=0.9,
            oi_expansion_threshold=0.2,
            funding_volatility_window=16,
        )

        assert config.enabled is False
        assert config.long_short_extreme == 0.9
        assert config.funding_volatility_window == 16
