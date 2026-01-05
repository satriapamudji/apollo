"""Scoring engine for trend-following signals."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


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
    trend_score: float
    volatility_score: float
    entry_quality: float
    funding_penalty: float
    news_modifier: float
    composite: float


class ScoringEngine:
    """Compute composite scores for trade proposals."""

    def __init__(self) -> None:
        self.weights = {
            "trend": 0.35,
            "volatility": 0.15,
            "entry_quality": 0.25,
            "funding": 0.10,
            "news": 0.15,
        }

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
    ) -> CompositeScore:
        trend_score = self._trend_score(
            direction=direction,
            price=price,
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            ema_fast_3bars_ago=ema_fast_3bars_ago,
            atr=atr,
        )
        volatility_score = self._volatility_score(price=price, atr=atr)
        entry_quality = self._entry_quality(entry_distance_atr=entry_distance_atr)
        funding_penalty = self._funding_penalty(direction, funding_rate)
        news_modifier = self._news_modifier(news_risk)

        composite = (
            trend_score * self.weights["trend"]
            + volatility_score * self.weights["volatility"]
            + entry_quality * self.weights["entry_quality"]
            + funding_penalty * self.weights["funding"]
            + news_modifier * self.weights["news"]
        )
        return CompositeScore(
            trend_score=trend_score,
            volatility_score=volatility_score,
            entry_quality=entry_quality,
            funding_penalty=funding_penalty,
            news_modifier=news_modifier,
            composite=_clamp(composite, 0.0, 1.0),
        )

    def _trend_score(
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

        return (
            trend_alignment * 0.5
            + slope_strength * 0.3
            + price_position * 0.2
        )

    def _volatility_score(self, price: float, atr: float) -> float:
        if price <= 0 or atr <= 0:
            return 0.0
        atr_percent = (atr / price) * 100
        if 2.0 <= atr_percent <= 5.0:
            return 1.0
        if atr_percent < 2.0:
            return _clamp(atr_percent / 2.0, 0.0, 1.0)
        return _clamp(1.0 - (atr_percent - 5.0) / 5.0, 0.0, 1.0)

    def _entry_quality(self, entry_distance_atr: float) -> float:
        if entry_distance_atr < 0:
            return 0.0
        if 0.5 <= entry_distance_atr <= 1.0:
            return 1.0
        if entry_distance_atr < 0.5:
            return _clamp(entry_distance_atr / 0.5, 0.0, 1.0)
        return _clamp(1.0 - (entry_distance_atr - 1.0) / 1.0, 0.0, 1.0)

    def _funding_penalty(self, direction: SignalDirection, funding_rate: float) -> float:
        funding_pct = _funding_percent(funding_rate)
        if direction == "LONG" and funding_pct > 0.03:
            return _clamp(1.0 - min(abs(funding_pct) / 0.1, 1.0), 0.0, 1.0)
        if direction == "SHORT" and funding_pct < -0.03:
            return _clamp(1.0 - min(abs(funding_pct) / 0.1, 1.0), 0.0, 1.0)
        return 1.0

    def _news_modifier(self, news_risk: NewsRiskLevel) -> float:
        if news_risk == "HIGH":
            return 0.0
        if news_risk == "MEDIUM":
            return 0.5
        return 1.0
