"""Tests for volume indicators and volume confirmation logic."""

import pandas as pd
import pytest

from src.features.indicators import calculate_volume_ratio, calculate_volume_sma
from src.strategy.scoring import ScoringEngine, VolumeFactor


class TestVolumeIndicators:
    """Tests for volume indicator calculations."""

    def test_calculate_volume_sma_basic(self) -> None:
        """Test basic volume SMA calculation."""
        volume = pd.Series([100, 200, 300, 400, 500])
        sma = calculate_volume_sma(volume, period=3)

        # First 2 values should be NaN (min_periods=3)
        assert pd.isna(sma.iloc[0])
        assert pd.isna(sma.iloc[1])

        # Third value should be mean of first 3: (100+200+300)/3 = 200
        assert sma.iloc[2] == 200.0

        # Fourth value: (200+300+400)/3 = 300
        assert sma.iloc[3] == 300.0

        # Fifth value: (300+400+500)/3 = 400
        assert sma.iloc[4] == 400.0

    def test_calculate_volume_sma_with_longer_period(self) -> None:
        """Test volume SMA with period longer than data."""
        volume = pd.Series([100, 200, 300])
        sma = calculate_volume_sma(volume, period=5)

        # All values should be NaN since we don't have 5 bars
        assert sma.isna().all()

    def test_calculate_volume_ratio_basic(self) -> None:
        """Test basic volume ratio calculation."""
        # Create volume data where current is 2x the SMA
        volume = pd.Series([100, 100, 100, 100, 200])
        ratio = calculate_volume_ratio(volume, period=4)

        # First 3 values should be NaN (min_periods=4 for SMA)
        assert pd.isna(ratio.iloc[0])
        assert pd.isna(ratio.iloc[1])
        assert pd.isna(ratio.iloc[2])

        # Fourth value: 100 / avg(100,100,100,100) = 100/100 = 1.0
        assert ratio.iloc[3] == 1.0

        # Fifth value: 200 / avg(100,100,100,200) = 200/125 = 1.6
        # The rolling SMA includes the current bar
        assert ratio.iloc[4] == pytest.approx(1.6, rel=0.01)

    def test_calculate_volume_ratio_handles_zero_sma(self) -> None:
        """Test volume ratio handles zero SMA gracefully."""
        volume = pd.Series([0, 0, 0, 0, 100])
        ratio = calculate_volume_ratio(volume, period=4)

        # When SMA is 0, the ratio should be NaN (not infinity)
        # The 4th value: 0 / 0 = NaN
        assert pd.isna(ratio.iloc[3])


class TestVolumeFactor:
    """Tests for the VolumeFactor scoring class."""

    def test_volume_factor_strong_volume(self) -> None:
        """Test volume factor with strong volume (>= 2x)."""
        factor = VolumeFactor()
        assert factor.compute(2.0) == 1.0
        assert factor.compute(3.0) == 1.0
        assert factor.compute(10.0) == 1.0

    def test_volume_factor_good_volume(self) -> None:
        """Test volume factor with good volume (1.5-2x)."""
        factor = VolumeFactor()
        assert factor.compute(1.5) == 0.7
        assert factor.compute(1.75) == 0.7
        assert factor.compute(1.99) == 0.7

    def test_volume_factor_weak_volume(self) -> None:
        """Test volume factor with weak volume (1.0-1.5x)."""
        factor = VolumeFactor()
        assert factor.compute(1.0) == 0.4
        assert factor.compute(1.25) == 0.4
        assert factor.compute(1.49) == 0.4

    def test_volume_factor_no_confirmation(self) -> None:
        """Test volume factor with no volume confirmation (<1x)."""
        factor = VolumeFactor()
        assert factor.compute(0.5) == 0.0
        assert factor.compute(0.99) == 0.0
        assert factor.compute(0.0) == 0.0

    def test_volume_factor_no_data(self) -> None:
        """Test volume factor returns neutral when no data available."""
        factor = VolumeFactor()
        assert factor.compute(None) == 0.5


class TestScoringEngineWithVolume:
    """Tests for ScoringEngine with volume_ratio parameter."""

    def test_scoring_includes_volume_score(self) -> None:
        """Test that scoring engine computes volume_score."""
        scoring = ScoringEngine()
        score = scoring.compute(
            direction="LONG",
            price=100.0,
            ema_fast=105.0,
            ema_slow=100.0,
            ema_fast_3bars_ago=102.0,
            atr=3.0,
            entry_distance_atr=0.8,
            funding_rate=0.0,
            news_risk="LOW",
            volume_ratio=2.0,
        )

        # Check volume_score is computed correctly
        assert score.volume_score == 1.0

    def test_scoring_with_no_volume_data(self) -> None:
        """Test scoring engine handles None volume_ratio."""
        scoring = ScoringEngine()
        score = scoring.compute(
            direction="LONG",
            price=100.0,
            ema_fast=105.0,
            ema_slow=100.0,
            ema_fast_3bars_ago=102.0,
            atr=3.0,
            entry_distance_atr=0.8,
            funding_rate=0.0,
            news_risk="LOW",
            volume_ratio=None,
        )

        # Neutral score when no volume data
        assert score.volume_score == 0.5

    def test_scoring_volume_weight_affects_composite(self) -> None:
        """Test that volume weight affects composite score."""
        from src.config.settings import FactorWeightsConfig, ScoringConfig

        # Create config with non-zero volume weight
        weights = FactorWeightsConfig(
            trend=0.30,
            volatility=0.15,
            entry_quality=0.20,
            funding=0.10,
            news=0.15,
            liquidity=0.0,
            volume=0.10,  # 10% weight for volume
        )
        config = ScoringConfig(factors=weights)
        scoring = ScoringEngine(config=config)

        # Score with high volume
        score_high_vol = scoring.compute(
            direction="LONG",
            price=100.0,
            ema_fast=105.0,
            ema_slow=100.0,
            ema_fast_3bars_ago=102.0,
            atr=3.0,
            entry_distance_atr=0.8,
            funding_rate=0.0,
            news_risk="LOW",
            volume_ratio=2.0,
        )

        # Score with low volume
        score_low_vol = scoring.compute(
            direction="LONG",
            price=100.0,
            ema_fast=105.0,
            ema_slow=100.0,
            ema_fast_3bars_ago=102.0,
            atr=3.0,
            entry_distance_atr=0.8,
            funding_rate=0.0,
            news_risk="LOW",
            volume_ratio=0.5,
        )

        # High volume should result in higher composite
        assert score_high_vol.composite > score_low_vol.composite
        assert score_high_vol.volume_score == 1.0
        assert score_low_vol.volume_score == 0.0
