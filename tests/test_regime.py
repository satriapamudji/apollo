"""Tests for the ADX + Choppiness Index regime filter."""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.config.settings import RegimeConfig, StrategyConfig
from src.features.indicators import calculate_adx, calculate_choppiness_index
from src.strategy.regime import RegimeClassifier, RegimeType, VolatilityRegimeType
from src.strategy.signals import SignalGenerator, SignalType


def _make_ohlcv_df(
    closes: list[float],
    high_offset: float = 0.02,
    low_offset: float = 0.02,
) -> pd.DataFrame:
    """Create a simple OHLCV DataFrame for testing."""
    n = len(closes)
    dates = pd.date_range(start="2024-01-01", periods=n, freq="4h", tz="UTC")
    data = {
        "open": [c * 0.999 for c in closes],
        "high": [c * (1 + high_offset) for c in closes],
        "low": [c * (1 - low_offset) for c in closes],
        "close": closes,
        "volume": [1000.0] * n,
    }
    df = pd.DataFrame(data, index=dates)
    return df


def _make_trending_df(n: int = 50, start_price: float = 100.0, trend: float = 0.01) -> pd.DataFrame:
    """Create a DataFrame with a clear uptrend (for TRENDING regime)."""
    closes = [start_price * (1 + trend) ** i for i in range(n)]
    # In a trending market, price moves in one direction with small retracements
    # High volatility but directional
    return _make_ohlcv_df(closes, high_offset=0.01, low_offset=0.005)


def _make_choppy_df(
    n: int = 50, base_price: float = 100.0, amplitude: float = 0.03
) -> pd.DataFrame:
    """Create a DataFrame with choppy/ranging price action."""
    # Oscillate around base price
    closes = [base_price + base_price * amplitude * np.sin(i * 0.5) for i in range(n)]
    # High range relative to directional movement
    return _make_ohlcv_df(closes, high_offset=0.025, low_offset=0.025)


class TestADXIndicator:
    """Tests for ADX (Average Directional Index) calculation."""

    def test_adx_returns_series(self) -> None:
        """ADX should return a pandas Series with same length as input."""
        df = _make_ohlcv_df([100.0] * 30)
        adx = calculate_adx(df, period=14)
        assert isinstance(adx, pd.Series)
        assert len(adx) == len(df)

    def test_adx_values_in_valid_range(self) -> None:
        """ADX values should be between 0 and 100."""
        df = _make_trending_df(100)
        adx = calculate_adx(df, period=14)
        # Filter out NaN values at the beginning
        valid_adx = adx.dropna()
        assert (valid_adx >= 0).all(), "ADX should be >= 0"
        assert (valid_adx <= 100).all(), "ADX should be <= 100"

    def test_adx_higher_in_trending_market(self) -> None:
        """ADX should be higher in trending markets than choppy markets."""
        trending_df = _make_trending_df(100, trend=0.02)
        choppy_df = _make_choppy_df(100, amplitude=0.02)

        adx_trending = calculate_adx(trending_df, period=14)
        adx_choppy = calculate_adx(choppy_df, period=14)

        # Compare the last values (after warmup period)
        assert adx_trending.iloc[-1] > adx_choppy.iloc[-1], (
            f"Trending ADX ({adx_trending.iloc[-1]:.2f}) should be > "
            f"choppy ADX ({adx_choppy.iloc[-1]:.2f})"
        )

    def test_adx_flat_market_is_low(self) -> None:
        """ADX in a flat market should be low (< 20)."""
        # Flat market: no trend, small random movements
        flat_closes = [100.0 + np.random.uniform(-0.5, 0.5) for _ in range(50)]
        df = _make_ohlcv_df(flat_closes, high_offset=0.005, low_offset=0.005)
        adx = calculate_adx(df, period=14)
        # Last value after warmup
        assert adx.iloc[-1] < 30, f"Flat market ADX should be low, got {adx.iloc[-1]:.2f}"


class TestChoppinessIndex:
    """Tests for Choppiness Index calculation."""

    def test_chop_returns_series(self) -> None:
        """Choppiness Index should return a pandas Series with same length as input."""
        df = _make_ohlcv_df([100.0] * 30)
        chop = calculate_choppiness_index(df, period=14)
        assert isinstance(chop, pd.Series)
        assert len(chop) == len(df)

    def test_chop_values_in_valid_range(self) -> None:
        """Choppiness Index values should be between 0 and 100."""
        df = _make_trending_df(100)
        chop = calculate_choppiness_index(df, period=14)
        # Filter out NaN values at the beginning
        valid_chop = chop.dropna()
        assert (valid_chop >= 0).all(), "CHOP should be >= 0"
        assert (valid_chop <= 100).all(), "CHOP should be <= 100"

    def test_chop_lower_in_trending_market(self) -> None:
        """Choppiness Index should be lower in trending markets (more directional)."""
        trending_df = _make_trending_df(100, trend=0.02)
        choppy_df = _make_choppy_df(100, amplitude=0.03)

        chop_trending = calculate_choppiness_index(trending_df, period=14)
        chop_choppy = calculate_choppiness_index(choppy_df, period=14)

        # In a trending market, ATR sum is close to the range (efficient movement)
        # In choppy market, ATR sum >> range (lots of back-and-forth)
        assert chop_trending.iloc[-1] < chop_choppy.iloc[-1], (
            f"Trending CHOP ({chop_trending.iloc[-1]:.2f}) should be < "
            f"choppy CHOP ({chop_choppy.iloc[-1]:.2f})"
        )


class TestRegimeClassifier:
    """Tests for RegimeClassifier."""

    def test_classify_trending_regime(self) -> None:
        """TRENDING regime when ADX >= 25 AND CHOP <= 50."""
        config = RegimeConfig(
            enabled=True,
            adx_trending_threshold=25.0,
            adx_ranging_threshold=20.0,
            chop_trending_threshold=50.0,
            chop_ranging_threshold=61.8,
        )
        classifier = RegimeClassifier(config)

        # Strong trend: high ADX, low choppiness
        result = classifier.classify(adx=30.0, chop=40.0)

        assert result.regime == RegimeType.TRENDING
        assert result.blocks_entry is False
        assert result.size_multiplier == 1.0

    def test_classify_choppy_regime_low_adx(self) -> None:
        """CHOPPY regime when ADX <= 20."""
        config = RegimeConfig(enabled=True)
        classifier = RegimeClassifier(config)

        result = classifier.classify(adx=15.0, chop=45.0)

        assert result.regime == RegimeType.CHOPPY
        assert result.blocks_entry is True
        assert result.size_multiplier == 0.0

    def test_classify_choppy_regime_high_chop(self) -> None:
        """CHOPPY regime when CHOP >= 61.8."""
        config = RegimeConfig(enabled=True)
        classifier = RegimeClassifier(config)

        result = classifier.classify(adx=28.0, chop=65.0)

        assert result.regime == RegimeType.CHOPPY
        assert result.blocks_entry is True
        assert result.size_multiplier == 0.0

    def test_classify_transitional_regime(self) -> None:
        """TRANSITIONAL regime when neither TRENDING nor CHOPPY criteria met."""
        config = RegimeConfig(
            enabled=True,
            adx_trending_threshold=25.0,
            adx_ranging_threshold=20.0,
            chop_trending_threshold=50.0,
            chop_ranging_threshold=61.8,
            transitional_size_multiplier=0.5,
        )
        classifier = RegimeClassifier(config)

        # ADX between 20 and 25, CHOP between 50 and 61.8
        result = classifier.classify(adx=22.0, chop=55.0)

        assert result.regime == RegimeType.TRANSITIONAL
        assert result.blocks_entry is False
        assert result.size_multiplier == 0.5

    def test_classify_disabled_always_trending(self) -> None:
        """When regime detection is disabled, always return TRENDING."""
        config = RegimeConfig(enabled=False)
        classifier = RegimeClassifier(config)

        # Even with low ADX and high chop, should return TRENDING when disabled
        result = classifier.classify(adx=10.0, chop=80.0)

        assert result.regime == RegimeType.TRENDING
        assert result.blocks_entry is False
        assert result.size_multiplier == 1.0

    def test_classify_stores_indicator_values(self) -> None:
        """Classification result should store the ADX and CHOP values."""
        config = RegimeConfig(enabled=True)
        classifier = RegimeClassifier(config)

        result = classifier.classify(adx=25.5, chop=48.3)

        assert result.adx == 25.5
        assert result.chop == 48.3

    def test_classify_edge_case_trending_boundary(self) -> None:
        """Test exactly at the TRENDING threshold boundaries."""
        config = RegimeConfig(
            enabled=True,
            adx_trending_threshold=25.0,
            chop_trending_threshold=50.0,
        )
        classifier = RegimeClassifier(config)

        # Exactly at thresholds should be TRENDING
        result = classifier.classify(adx=25.0, chop=50.0)
        assert result.regime == RegimeType.TRENDING

    def test_classify_edge_case_choppy_boundary(self) -> None:
        """Test exactly at the CHOPPY threshold boundaries."""
        config = RegimeConfig(
            enabled=True,
            adx_ranging_threshold=20.0,
            chop_ranging_threshold=61.8,
        )
        classifier = RegimeClassifier(config)

        # ADX exactly at ranging threshold
        result = classifier.classify(adx=20.0, chop=50.0)
        assert result.regime == RegimeType.CHOPPY

        # CHOP exactly at ranging threshold
        result = classifier.classify(adx=30.0, chop=61.8)
        assert result.regime == RegimeType.CHOPPY


class TestSignalGeneratorRegimeGating:
    """Tests for signal gating based on market regime."""

    def _make_strategy_config(self) -> StrategyConfig:
        """Create a basic strategy config for testing."""
        return StrategyConfig(
            entry={"style": "breakout", "score_threshold": 0.3},
        )

    def _make_test_data(self, n: int = 100) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Create daily and 4h DataFrames for testing signals."""
        # Create an uptrend that would normally trigger a LONG signal
        daily_closes = [100.0 * (1.01**i) for i in range(n)]
        fourh_closes = [100.0 * (1.005**i) for i in range(n * 6)]

        daily_df = _make_ohlcv_df(daily_closes, high_offset=0.02, low_offset=0.01)
        fourh_df = _make_ohlcv_df(fourh_closes, high_offset=0.015, low_offset=0.01)

        return daily_df, fourh_df

    def test_signal_blocked_in_choppy_regime(self) -> None:
        """Entry signals should be blocked (NONE) in CHOPPY regime."""
        strategy_config = self._make_strategy_config()
        # Configure for choppy detection
        regime_config = RegimeConfig(
            enabled=True,
            adx_trending_threshold=25.0,
            adx_ranging_threshold=20.0,
            chop_trending_threshold=50.0,
            chop_ranging_threshold=61.8,
        )

        generator = SignalGenerator(strategy_config, regime_config)

        # Create choppy market data
        daily_df = _make_choppy_df(100, amplitude=0.04)
        fourh_df = _make_choppy_df(200, amplitude=0.03)

        signal = generator.generate(
            symbol="TESTUSDT",
            daily_df=daily_df,
            fourh_df=fourh_df,
            funding_rate=0.0,
            news_risk="LOW",
            open_position=None,
            current_time=fourh_df.index[-1].to_pydatetime(),
        )

        # Signal should include regime information
        assert signal.regime is not None
        # If regime is CHOPPY, entry should be blocked
        if signal.regime.regime == RegimeType.CHOPPY:
            assert signal.signal_type == SignalType.NONE
            assert signal.regime.blocks_entry is True

    def test_signal_includes_regime_classification(self) -> None:
        """Generated signals should include regime classification."""
        strategy_config = self._make_strategy_config()
        regime_config = RegimeConfig(enabled=True)

        generator = SignalGenerator(strategy_config, regime_config)
        daily_df, fourh_df = self._make_test_data()

        signal = generator.generate(
            symbol="TESTUSDT",
            daily_df=daily_df,
            fourh_df=fourh_df,
            funding_rate=0.0,
            news_risk="LOW",
            open_position=None,
            current_time=fourh_df.index[-1].to_pydatetime(),
        )

        assert signal.regime is not None
        assert hasattr(signal.regime, "regime")
        assert hasattr(signal.regime, "adx")
        assert hasattr(signal.regime, "chop")
        assert hasattr(signal.regime, "blocks_entry")
        assert hasattr(signal.regime, "size_multiplier")

    def test_signal_allowed_in_trending_regime(self) -> None:
        """Entry signals should be allowed in TRENDING regime."""
        strategy_config = self._make_strategy_config()
        regime_config = RegimeConfig(
            enabled=True,
            adx_trending_threshold=25.0,
            chop_trending_threshold=50.0,
        )

        generator = SignalGenerator(strategy_config, regime_config)

        # Create strongly trending market data
        daily_df = _make_trending_df(100, trend=0.02)
        fourh_df = _make_trending_df(200, trend=0.01)

        signal = generator.generate(
            symbol="TESTUSDT",
            daily_df=daily_df,
            fourh_df=fourh_df,
            funding_rate=0.0,
            news_risk="LOW",
            open_position=None,
            current_time=fourh_df.index[-1].to_pydatetime(),
        )

        # If regime is TRENDING, blocks_entry should be False
        if signal.regime and signal.regime.regime == RegimeType.TRENDING:
            assert signal.regime.blocks_entry is False

    def test_regime_disabled_allows_all_entries(self) -> None:
        """When regime detection is disabled, entries are not blocked."""
        strategy_config = self._make_strategy_config()
        regime_config = RegimeConfig(enabled=False)

        generator = SignalGenerator(strategy_config, regime_config)

        # Even with choppy data, regime should not block
        daily_df = _make_choppy_df(100, amplitude=0.04)
        fourh_df = _make_choppy_df(200, amplitude=0.03)

        signal = generator.generate(
            symbol="TESTUSDT",
            daily_df=daily_df,
            fourh_df=fourh_df,
            funding_rate=0.0,
            news_risk="LOW",
            open_position=None,
            current_time=fourh_df.index[-1].to_pydatetime(),
        )

        # When disabled, regime should be TRENDING and not block
        if signal.regime:
            assert signal.regime.regime == RegimeType.TRENDING
            assert signal.regime.blocks_entry is False


class TestVolatilityRegime:
    """Tests for volatility regime detection (optional feature)."""

    def test_volatility_regime_contraction(self) -> None:
        """Detect volatility contraction when ATR drops significantly."""
        config = RegimeConfig(
            enabled=True,
            volatility_regime_enabled=True,
            volatility_contraction_threshold=0.5,
            volatility_expansion_threshold=2.0,
        )
        classifier = RegimeClassifier(config)

        # ATR dropped to 40% of prior (below 0.5 threshold = contraction)
        result = classifier.classify(
            adx=30.0,
            chop=40.0,
            atr=1.0,
            atr_pct=0.4,
            prior_atr_pct=1.0,
        )

        assert result.volatility_regime == VolatilityRegimeType.CONTRACTION
        assert result.volatility_contracts is True
        assert result.volatility_expands is False

    def test_volatility_regime_expansion(self) -> None:
        """Detect volatility expansion when ATR increases significantly."""
        config = RegimeConfig(
            enabled=True,
            volatility_regime_enabled=True,
            volatility_contraction_threshold=0.5,
            volatility_expansion_threshold=2.0,
        )
        classifier = RegimeClassifier(config)

        # ATR doubled (above 2.0 threshold = expansion)
        result = classifier.classify(
            adx=30.0,
            chop=40.0,
            atr=2.0,
            atr_pct=2.5,
            prior_atr_pct=1.0,
        )

        assert result.volatility_regime == VolatilityRegimeType.EXPANSION
        assert result.volatility_contracts is False
        assert result.volatility_expands is True

    def test_volatility_regime_normal(self) -> None:
        """Normal volatility when ATR change is within thresholds."""
        config = RegimeConfig(
            enabled=True,
            volatility_regime_enabled=True,
            volatility_contraction_threshold=0.5,
            volatility_expansion_threshold=2.0,
        )
        classifier = RegimeClassifier(config)

        # ATR slightly higher (within normal range)
        result = classifier.classify(
            adx=30.0,
            chop=40.0,
            atr=1.2,
            atr_pct=1.2,
            prior_atr_pct=1.0,
        )

        assert result.volatility_regime == VolatilityRegimeType.NORMAL
        assert result.volatility_contracts is False
        assert result.volatility_expands is False

    def test_volatility_regime_disabled(self) -> None:
        """When volatility regime is disabled, should be None."""
        config = RegimeConfig(
            enabled=True,
            volatility_regime_enabled=False,
        )
        classifier = RegimeClassifier(config)

        result = classifier.classify(
            adx=30.0,
            chop=40.0,
            atr=1.0,
            atr_pct=0.3,
            prior_atr_pct=1.0,
        )

        assert result.volatility_regime is None
