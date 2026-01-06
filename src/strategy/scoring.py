"""Scoring engine for trend-following signals with modular factor architecture."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from src.config.settings import ScoringConfig


SignalDirection = Literal["LONG", "SHORT"]
NewsRiskLevel = Literal["HIGH", "MEDIUM", "LOW"]


def _clamp(value: float, min_val: float, max_val: float) -> float:
    return max(min_val, min(max_val, value))


def _normalize(value: float, min_val: float, max_val: float) -> float:
    if max_val == min_val:
        return 0.0
    return _clamp((value - min_val) / (max_val - min_val), 0.0, 1.0)


def _funding_percent(funding_rate: float) -> float:
    """Return funding rate in percent units."""
    if abs(funding_rate) <= 1:
        return funding_rate * 100
    return funding_rate


@dataclass(frozen=True)
class CompositeScore:
    """Composite score with per-factor breakdown."""

    trend_score: float
    volatility_score: float
    entry_quality: float
    funding_penalty: float
    news_modifier: float
    liquidity_score: float
    crowding_score: float
    funding_volatility_score: float
    oi_expansion_score: float
    taker_imbalance_score: float
    volume_score: float
    composite: float


class TrendFactor:
    """Factor for EMA trend alignment and momentum."""

    def compute(
        self,
        direction: SignalDirection,
        price: float,
        ema_fast: float,
        ema_slow: float,
        ema_fast_3bars_ago: float,
        atr: float,
    ) -> float:
        if direction == "LONG":
            trend_alignment = 1.0 if ema_fast > ema_slow else 0.0
            price_position = 1.0 if price > ema_slow else 0.5
        else:
            trend_alignment = 1.0 if ema_fast < ema_slow else 0.0
            price_position = 1.0 if price < ema_slow else 0.5

        slope_strength = 0.0
        if atr > 0:
            slope_strength = _normalize(abs(ema_fast - ema_fast_3bars_ago) / atr, 0, 0.5)

        return trend_alignment * 0.5 + slope_strength * 0.3 + price_position * 0.2


class VolatilityFactor:
    """Factor for volatility regime and ATR-based scoring."""

    def compute(self, price: float, atr: float, atr_percent: float | None = None) -> float:
        """Compute volatility score.

        Args:
            price: Current price
            atr: ATR value
            atr_percent: Optional pre-computed ATR as percentage of price
        """
        if price <= 0 or atr <= 0:
            return 0.0

        if atr_percent is None:
            atr_percent = (atr / price) * 100

        # Sweet spot: 2-5% ATR% for trend-following
        if 2.0 <= atr_percent <= 5.0:
            return 1.0
        if atr_percent < 2.0:
            return _clamp(atr_percent / 2.0, 0.0, 1.0)
        return _clamp(1.0 - (atr_percent - 5.0) / 5.0, 0.0, 1.0)


class EntryQualityFactor:
    """Factor for entry distance quality relative to ATR."""

    def compute(self, entry_distance_atr: float) -> float:
        """Compute entry quality score based on distance from breakout level.

        Args:
            entry_distance_atr: Entry distance in ATR units (how far past breakout level)
        """
        if entry_distance_atr < 0:
            return 0.0
        # Sweet spot: 0.5-1.0 ATR from breakout level
        if 0.5 <= entry_distance_atr <= 1.0:
            return 1.0
        if entry_distance_atr < 0.5:
            return _clamp(entry_distance_atr / 0.5, 0.0, 1.0)
        return _clamp(1.0 - (entry_distance_atr - 1.0) / 1.0, 0.0, 1.0)


class FundingFactor:
    """Factor for funding rate alignment."""

    def compute(self, direction: SignalDirection, funding_rate: float) -> float:
        """Compute funding penalty score.

        Args:
            direction: Trade direction
            funding_rate: Current funding rate
        """
        funding_pct = _funding_percent(funding_rate)
        # Penalize adverse funding (paying for longs in positive funding, etc.)
        if direction == "LONG" and funding_pct > 0.03:
            return _clamp(1.0 - min(abs(funding_pct) / 0.1, 1.0), 0.0, 1.0)
        if direction == "SHORT" and funding_pct < -0.03:
            return _clamp(1.0 - min(abs(funding_pct) / 0.1, 1.0), 0.0, 1.0)
        return 1.0


class NewsFactor:
    """Factor for news sentiment modifier."""

    def compute(self, news_risk: NewsRiskLevel) -> float:
        """Compute news risk modifier.

        Args:
            news_risk: News risk level
        """
        if news_risk == "HIGH":
            return 0.0
        if news_risk == "MEDIUM":
            return 0.5
        return 1.0


class LiquidityFactor:
    """Factor for liquidity/spread scoring."""

    def __init__(self, max_spread_pct: float = 0.3) -> None:
        self.max_spread_pct = max_spread_pct

    def compute(self, spread_pct: float | None = None) -> float:
        """Compute liquidity score based on spread.

        Args:
            spread_pct: Current spread as percentage (optional, defaults to max score)
        """
        if spread_pct is None:
            return 1.0  # No spread data available, assume acceptable
        return _clamp(1.0 - (spread_pct / self.max_spread_pct), 0.0, 1.0)


class CrowdingFactor:
    """Factor for position crowding based on OI and long/short ratios.

    Penalizes when everyone is on the same side of the trade.
    """

    def __init__(self, extreme_threshold: float = 0.8) -> None:
        self.extreme_threshold = extreme_threshold

    def compute(self, long_short_ratio: float, crowding_score: float | None = None) -> float:
        """Compute crowding penalty score.

        Args:
            long_short_ratio: Global long/short ratio (0.0-1.0, normalized)
            crowding_score: Pre-computed crowding score from CrowdingData (0.0=crowded, 1.0=not)

        Returns:
            Score from 0.0 (crowded) to 1.0 (not crowded)
        """
        # Use pre-computed crowding score if available
        if crowding_score is not None:
            return crowding_score

        # Normal range is between (1 - extreme_threshold) and extreme_threshold
        # For extreme_threshold=0.8, normal is 0.2 to 0.8
        normal_low = 1.0 - self.extreme_threshold
        normal_high = self.extreme_threshold

        if normal_low <= long_short_ratio <= normal_high:
            return 1.0  # Normal range

        # Extreme: penalize based on how extreme
        if long_short_ratio > normal_high:
            # Too many longs (ratio > 0.8 means 80%+ are long)
            excess = (long_short_ratio - normal_high) / (1.0 - normal_high)
        else:
            # Too many shorts (ratio < 0.2 means 80%+ are short)
            excess = (normal_low - long_short_ratio) / normal_low

        # Penalize more aggressively so extreme inputs are meaningfully < 1.0.
        return _clamp(1.0 - excess * 0.6, 0.0, 1.0)


class FundingVolatilityFactor:
    """Factor for funding rate volatility.

    Penalizes when funding rate is unstable (can signal regime change).
    """

    def compute(self, funding_volatility_score: float | None = None) -> float:
        """Compute funding volatility score.

        Args:
            funding_volatility_score: Pre-computed volatility score (0.0=stable, 1.0=volatile)

        Returns:
            Score from 0.0 (volatile) to 1.0 (stable)
        """
        if funding_volatility_score is not None:
            return funding_volatility_score
        return 1.0  # Default to stable if no data


class OIExpansionFactor:
    """Factor for open interest expansion/contraction.

    Penalizes during rapid OI expansion (crowding) or contraction (exit).
    """

    def __init__(self, expansion_threshold: float = 0.1) -> None:
        self.expansion_threshold = expansion_threshold

    def compute(self, oi_expansion_score: float | None = None) -> float:
        """Compute OI expansion score.

        Args:
            oi_expansion_score: Pre-computed expansion score (0.0=contraction, 0.5=neutral, 1.0=expansion)

        Returns:
            Score from 0.0 (problematic) to 1.0 (healthy)
        """
        if oi_expansion_score is not None:
            return oi_expansion_score
        return 0.5  # Neutral if no data


class TakerImbalanceFactor:
    """Factor for taker buy/sell imbalance.

    Uses taker activity as a sentiment indicator.
    """

    def __init__(self, imbalance_threshold: float = 0.6) -> None:
        self.imbalance_threshold = imbalance_threshold

    def compute(
        self,
        taker_buy_ratio: float,
        taker_sell_ratio: float,
        taker_imbalance_score: float | None = None,
    ) -> float:
        """Compute taker imbalance score.

        Args:
            taker_buy_ratio: Taker buy ratio
            taker_sell_ratio: Taker sell ratio
            taker_imbalance_score: Pre-computed imbalance score (0.0=selling, 0.5=balanced, 1.0=buying)

        Returns:
            Score from 0.0 to 1.0
        """
        if taker_imbalance_score is not None:
            return taker_imbalance_score

        # Compute from raw ratios
        total = taker_buy_ratio + taker_sell_ratio
        if total > 0:
            buy_ratio = taker_buy_ratio / total
        else:
            buy_ratio = 0.5

        return buy_ratio


class VolumeFactor:
    """Factor for volume confirmation on entries.

    Scores based on volume ratio (current volume / SMA):
    - volume_ratio >= 2.0 → score 1.0 (strong volume)
    - volume_ratio 1.5-2.0 → score 0.7 (good volume)
    - volume_ratio 1.0-1.5 → score 0.4 (weak volume)
    - volume_ratio < 1.0 → score 0.0 (no volume confirmation)
    """

    def compute(self, volume_ratio: float | None = None) -> float:
        """Compute volume score based on volume ratio.

        Args:
            volume_ratio: Current volume as ratio of SMA (1.0 = average, 2.0 = 2x average)

        Returns:
            Score from 0.0 (no volume) to 1.0 (strong volume)
        """
        if volume_ratio is None:
            return 0.5  # Neutral if no volume data

        if volume_ratio >= 2.0:
            return 1.0
        if volume_ratio >= 1.5:
            return 0.7
        if volume_ratio >= 1.0:
            return 0.4
        return 0.0


class ScoringEngine:
    """Compute composite scores for trade proposals using modular factor architecture."""

    def __init__(
        self,
        config: ScoringConfig | None = None,
        spread_pct: float | None = None,
    ) -> None:
        self.config = config or ScoringConfig()
        self.weights = self.config.factors

        # Initialize modular factor instances
        self.trend_factor = TrendFactor()
        self.volatility_factor = VolatilityFactor()
        self.entry_quality_factor = EntryQualityFactor()
        self.funding_factor = FundingFactor()
        self.news_factor = NewsFactor()
        self.liquidity_factor = LiquidityFactor()
        self.crowding_factor = CrowdingFactor()
        self.funding_volatility_factor = FundingVolatilityFactor()
        self.oi_expansion_factor = OIExpansionFactor()
        self.taker_imbalance_factor = TakerImbalanceFactor()
        self.volume_factor = VolumeFactor()

        # Store spread for liquidity scoring
        self._spread_pct = spread_pct

    def compute(
        self,
        direction: SignalDirection,
        price: float,
        ema_fast: float,
        ema_slow: float,
        ema_fast_3bars_ago: float,
        atr: float,
        entry_distance_atr: float,
        funding_rate: float,
        news_risk: NewsRiskLevel,
        atr_percent: float | None = None,
        # Crowding data parameters
        crowding_data: dict[str, float] | None = None,
        # Volume data parameter
        volume_ratio: float | None = None,
    ) -> CompositeScore:
        """Compute composite score from all factors.

        Args:
            direction: Trade direction
            price: Current price
            ema_fast: Fast EMA value
            ema_slow: Slow EMA value
            ema_fast_3bars_ago: Fast EMA 3 bars ago
            atr: ATR value
            entry_distance_atr: Entry distance in ATR units
            funding_rate: Current funding rate
            news_risk: News risk level
            atr_percent: Optional pre-computed ATR as percentage of price
            crowding_data: Optional dict with crowding metrics:
                - long_short_ratio
                - crowding_score
                - funding_volatility_score
                - oi_expansion_score
                - taker_buy_ratio
                - taker_sell_ratio
                - taker_imbalance_score
            volume_ratio: Volume ratio (current volume / SMA)
        """
        trend_score = self.trend_factor.compute(
            direction=direction,
            price=price,
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            ema_fast_3bars_ago=ema_fast_3bars_ago,
            atr=atr,
        )
        volatility_score = self.volatility_factor.compute(
            price=price, atr=atr, atr_percent=atr_percent
        )
        entry_quality = self.entry_quality_factor.compute(entry_distance_atr=entry_distance_atr)
        funding_penalty = self.funding_factor.compute(direction, funding_rate)
        news_modifier = self.news_factor.compute(news_risk)
        liquidity_score = self.liquidity_factor.compute(self._spread_pct)

        # Compute crowding factors
        if crowding_data:
            crowding_score = self.crowding_factor.compute(
                crowding_data.get("long_short_ratio", 0.5),
                crowding_data.get("crowding_score"),
            )
            funding_volatility_score = self.funding_volatility_factor.compute(
                crowding_data.get("funding_volatility_score"),
            )
            oi_expansion_score = self.oi_expansion_factor.compute(
                crowding_data.get("oi_expansion_score"),
            )
            taker_imbalance_score = self.taker_imbalance_factor.compute(
                crowding_data.get("taker_buy_ratio", 0.5),
                crowding_data.get("taker_sell_ratio", 0.5),
                crowding_data.get("taker_imbalance_score"),
            )
        else:
            # Default scores when no crowding data available
            crowding_score = self.crowding_factor.compute(0.5)
            funding_volatility_score = self.funding_volatility_factor.compute()
            oi_expansion_score = self.oi_expansion_factor.compute()
            taker_imbalance_score = self.taker_imbalance_factor.compute(0.5, 0.5)

        # Compute volume score
        volume_score = self.volume_factor.compute(volume_ratio)

        composite = (
            trend_score * self.weights.trend
            + volatility_score * self.weights.volatility
            + entry_quality * self.weights.entry_quality
            + funding_penalty * self.weights.funding
            + news_modifier * self.weights.news
            + liquidity_score * self.weights.liquidity
            + volume_score * self.weights.volume
            + crowding_score * 0.0  # Reserved for future weighting
            + funding_volatility_score * 0.0
            + oi_expansion_score * 0.0
            + taker_imbalance_score * 0.0
        )

        return CompositeScore(
            trend_score=trend_score,
            volatility_score=volatility_score,
            entry_quality=entry_quality,
            funding_penalty=funding_penalty,
            news_modifier=news_modifier,
            liquidity_score=liquidity_score,
            crowding_score=crowding_score,
            funding_volatility_score=funding_volatility_score,
            oi_expansion_score=oi_expansion_score,
            taker_imbalance_score=taker_imbalance_score,
            volume_score=volume_score,
            composite=_clamp(composite, 0.0, 1.0),
        )

    @property
    def score_threshold(self) -> float:
        """Get the configured score threshold."""
        return self.config.threshold
