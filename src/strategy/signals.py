"""Signal generation for the trend-following strategy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Literal
from uuid import uuid4

import pandas as pd

from src.config.settings import RegimeConfig, StrategyConfig
from src.features.pipeline import FeaturePipeline
from src.ledger.state import Position
from src.strategy.regime import RegimeClassification, RegimeClassifier
from src.strategy.scoring import CompositeScore, ScoringEngine


class SignalType(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    EXIT = "EXIT"
    NONE = "NONE"


TrendDirection = Literal["UPTREND", "DOWNTREND", "NO_TREND"]


@dataclass(frozen=True)
class Signal:
    symbol: str
    signal_type: SignalType
    score: CompositeScore | None
    price: float
    atr: float
    entry_price: float | None = None
    stop_price: float | None = None
    take_profit: float | None = None
    reason: str | None = None
    trade_id: str | None = None
    timestamp: datetime | None = None
    regime: RegimeClassification | None = None
    entry_extension: float | None = None
    volume_ratio: float | None = None


class SignalGenerator:
    """Generate entry and exit signals from market data."""

    def __init__(self, config: StrategyConfig, regime_config: RegimeConfig | None = None) -> None:
        self.config = config
        self.pipeline = FeaturePipeline(config.indicators)
        self.regime_config = regime_config or RegimeConfig()
        self.regime_classifier = RegimeClassifier(self.regime_config)
        # Wire scoring config from strategy config
        self.scoring = ScoringEngine(config=config.scoring)

    def generate(
        self,
        symbol: str,
        daily_df: pd.DataFrame,
        fourh_df: pd.DataFrame,
        funding_rate: float,
        news_risk: Literal["HIGH", "MEDIUM", "LOW"],
        open_position: Position | None = None,
        current_time: datetime | None = None,
    ) -> Signal:
        daily = self.pipeline.compute(daily_df)
        fourh = self.pipeline.compute(fourh_df)

        trend = self._determine_trend(daily)
        latest_4h = fourh.iloc[-1]
        atr = float(latest_4h["atr"]) if not pd.isna(latest_4h["atr"]) else 0.0
        price = float(latest_4h["close"])

        # Determine market regime
        adx = float(latest_4h["adx"]) if not pd.isna(latest_4h["adx"]) else 0.0
        chop = float(latest_4h["chop"]) if not pd.isna(latest_4h["chop"]) else 50.0
        regime = self.regime_classifier.classify(adx, chop)

        if open_position:
            exit_signal = self._check_exit(
                open_position, trend, latest_4h, atr, current_time=current_time
            )
            if exit_signal:
                return exit_signal

        if open_position or trend == "NO_TREND":
            return Signal(
                symbol=symbol,
                signal_type=SignalType.NONE,
                score=None,
                price=price,
                atr=atr,
                timestamp=(
                    latest_4h.name.to_pydatetime() if hasattr(latest_4h, "name") else current_time
                ),
                regime=regime,
            )

        # Block entries in choppy market regime
        if regime.blocks_entry:
            return Signal(
                symbol=symbol,
                signal_type=SignalType.NONE,
                score=None,
                price=price,
                atr=atr,
                timestamp=(
                    latest_4h.name.to_pydatetime() if hasattr(latest_4h, "name") else current_time
                ),
                regime=regime,
                reason="CHOPPY_REGIME",
            )

        if self.config.entry.style == "pullback":
            entry_signal = self._pullback_entry(
                symbol,
                trend,
                daily,
                fourh,
                funding_rate,
                news_risk,
                regime,
                current_time=current_time,
            )
        else:
            entry_signal = self._breakout_entry(
                symbol,
                trend,
                daily,
                fourh,
                funding_rate,
                news_risk,
                regime,
                current_time=current_time,
            )

        return entry_signal

    def _determine_trend(self, daily: pd.DataFrame) -> TrendDirection:
        latest = daily.iloc[-1]
        ema_fast = latest["ema_fast"]
        ema_slow = latest["ema_slow"]
        price = latest["close"]
        ema_fast_prev = daily["ema_fast"].iloc[-4] if len(daily) >= 4 else ema_fast

        if ema_fast > ema_slow and price > ema_slow and ema_fast > ema_fast_prev:
            return "UPTREND"
        if ema_fast < ema_slow and price < ema_slow and ema_fast < ema_fast_prev:
            return "DOWNTREND"
        return "NO_TREND"

    def _pullback_entry(
        self,
        symbol: str,
        trend: TrendDirection,
        daily: pd.DataFrame,
        fourh: pd.DataFrame,
        funding_rate: float,
        news_risk: Literal["HIGH", "MEDIUM", "LOW"],
        regime: RegimeClassification,
        current_time: datetime | None = None,
    ) -> Signal:
        latest = fourh.iloc[-1]
        prev = fourh.iloc[-2] if len(fourh) >= 2 else latest
        price = float(latest["close"])
        atr = float(latest["atr"]) if not pd.isna(latest["atr"]) else 0.0

        # Get volume ratio if available
        volume_ratio: float | None = None
        if "volume_ratio" in fourh.columns and not pd.isna(latest.get("volume_ratio")):
            volume_ratio = float(latest["volume_ratio"])

        ema_fast = float(latest["ema_fast"])
        ema_slow = float(latest["ema_slow"])
        rsi = float(latest["rsi"])

        direction = None
        if trend == "UPTREND":
            pulled_back = float(prev["close"]) <= float(prev["ema_slow"])
            close_recovery = price > ema_fast
            if pulled_back and close_recovery and rsi > 40:
                direction = "LONG"
        elif trend == "DOWNTREND":
            pulled_back = float(prev["close"]) >= float(prev["ema_slow"])
            close_recovery = price < ema_fast
            if pulled_back and close_recovery and rsi < 60:
                direction = "SHORT"

        if not direction:
            return Signal(
                symbol=symbol,
                signal_type=SignalType.NONE,
                score=None,
                price=price,
                atr=atr,
                regime=regime,
                volume_ratio=volume_ratio,
            )

        # Volume confirmation gate for pullbacks
        # Require volume on recovery bar > previous 3 bars average
        if self.config.entry.volume_confirmation_enabled and "volume" in fourh.columns:
            if len(fourh) >= 4:
                current_volume = float(latest["volume"]) if not pd.isna(latest["volume"]) else 0.0
                prev_3_avg = float(fourh["volume"].iloc[-4:-1].mean())
                if prev_3_avg > 0 and current_volume <= prev_3_avg:
                    signal_time = (
                        latest.name.to_pydatetime() if hasattr(latest, "name") else current_time
                    )
                    return Signal(
                        symbol=symbol,
                        signal_type=SignalType.NONE,
                        score=None,
                        price=price,
                        atr=atr,
                        reason=f"Pullback volume too low: {current_volume:.0f} <= {prev_3_avg:.0f} (prev 3 avg)",
                        timestamp=signal_time,
                        regime=regime,
                        volume_ratio=volume_ratio,
                    )

        entry_distance = abs(price - ema_slow) / atr if atr > 0 else 0.0
        score = self._score_entry(
            direction, daily, price, atr, entry_distance, funding_rate, news_risk, volume_ratio
        )
        signal_time = latest.name.to_pydatetime() if hasattr(latest, "name") else current_time
        return self._build_entry_signal(
            symbol, direction, price, atr, score, signal_time, entry_distance, regime, volume_ratio
        )

    def _breakout_entry(
        self,
        symbol: str,
        trend: TrendDirection,
        daily: pd.DataFrame,
        fourh: pd.DataFrame,
        funding_rate: float,
        news_risk: Literal["HIGH", "MEDIUM", "LOW"],
        regime: RegimeClassification,
        current_time: datetime | None = None,
    ) -> Signal:
        latest = fourh.iloc[-1]
        price = float(latest["close"])
        atr = float(latest["atr"]) if not pd.isna(latest["atr"]) else 0.0

        # Get volume ratio if available
        volume_ratio: float | None = None
        if "volume_ratio" in fourh.columns and not pd.isna(latest.get("volume_ratio")):
            volume_ratio = float(latest["volume_ratio"])

        direction = None
        breakout_level = None
        if len(fourh) >= 21:
            prior = fourh.iloc[-21:-1]
            high_20 = prior["high"].max()
            low_20 = prior["low"].min()
            if trend == "UPTREND" and price > high_20:
                direction = "LONG"
                breakout_level = high_20
            elif trend == "DOWNTREND" and price < low_20:
                direction = "SHORT"
                breakout_level = low_20

        if not direction:
            return Signal(
                symbol=symbol,
                signal_type=SignalType.NONE,
                score=None,
                price=price,
                atr=atr,
                regime=regime,
                volume_ratio=volume_ratio,
            )

        # Calculate actual entry distance in ATR units
        entry_distance = abs(price - breakout_level) / atr if atr > 0 else 0.0

        # Reject over-extended breakouts
        max_extension = self.config.entry.max_breakout_extension_atr
        if entry_distance > max_extension:
            signal_time = latest.name.to_pydatetime() if hasattr(latest, "name") else current_time
            return Signal(
                symbol=symbol,
                signal_type=SignalType.NONE,
                score=None,
                price=price,
                atr=atr,
                reason=f"Breakout over-extended: {entry_distance:.2f} ATR > {max_extension} ATR",
                entry_extension=entry_distance,
                timestamp=signal_time,
                regime=regime,
                volume_ratio=volume_ratio,
            )

        # Volume confirmation gate for breakouts
        if self.config.entry.volume_confirmation_enabled:
            volume_threshold = self.config.entry.volume_breakout_threshold
            if volume_ratio is None or volume_ratio < volume_threshold:
                signal_time = (
                    latest.name.to_pydatetime() if hasattr(latest, "name") else current_time
                )
                return Signal(
                    symbol=symbol,
                    signal_type=SignalType.NONE,
                    score=None,
                    price=price,
                    atr=atr,
                    reason=f"Volume too low: {volume_ratio:.2f}x < {volume_threshold}x threshold"
                    if volume_ratio is not None
                    else "No volume data available",
                    entry_extension=entry_distance,
                    timestamp=signal_time,
                    regime=regime,
                    volume_ratio=volume_ratio,
                )

        score = self._score_entry(
            direction, daily, price, atr, entry_distance, funding_rate, news_risk, volume_ratio
        )
        signal_time = latest.name.to_pydatetime() if hasattr(latest, "name") else current_time
        return self._build_entry_signal(
            symbol, direction, price, atr, score, signal_time, entry_distance, regime, volume_ratio
        )

    def _score_entry(
        self,
        direction: Literal["LONG", "SHORT"],
        daily: pd.DataFrame,
        price: float,
        atr: float,
        entry_distance: float,
        funding_rate: float,
        news_risk: Literal["HIGH", "MEDIUM", "LOW"],
        volume_ratio: float | None = None,
    ) -> CompositeScore:
        daily_latest = daily.iloc[-1]
        ema_fast = float(daily_latest["ema_fast"])
        ema_slow = float(daily_latest["ema_slow"])
        ema_fast_prev = float(daily["ema_fast"].iloc[-4]) if len(daily) >= 4 else ema_fast
        return self.scoring.compute(
            direction=direction,
            price=price,
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            ema_fast_3bars_ago=ema_fast_prev,
            atr=atr,
            entry_distance_atr=entry_distance,
            funding_rate=funding_rate,
            news_risk=news_risk,
            volume_ratio=volume_ratio,
        )

    def _build_entry_signal(
        self,
        symbol: str,
        direction: Literal["LONG", "SHORT"],
        price: float,
        atr: float,
        score: CompositeScore,
        timestamp: datetime | None,
        entry_extension: float | None = None,
        regime: RegimeClassification | None = None,
        volume_ratio: float | None = None,
    ) -> Signal:
        # Use scoring config threshold, fallback to entry config for backward compatibility
        score_threshold = (
            self.config.scoring.threshold
            if self.config.scoring.enabled
            else self.config.entry.score_threshold
        )
        if score.composite < score_threshold:
            return Signal(
                symbol=symbol,
                signal_type=SignalType.NONE,
                score=score,
                price=price,
                atr=atr,
                entry_extension=entry_extension,
                regime=regime,
                volume_ratio=volume_ratio,
            )
        stop_distance = self.config.exit.atr_stop_multiplier * atr if atr > 0 else 0.0
        stop_price = price - stop_distance if direction == "LONG" else price + stop_distance
        # No fixed take profit - trailing stop and partial TP handled by execution engine
        signal_type = SignalType.LONG if direction == "LONG" else SignalType.SHORT
        return Signal(
            symbol=symbol,
            signal_type=signal_type,
            score=score,
            price=price,
            atr=atr,
            entry_price=price,
            stop_price=stop_price,
            take_profit=None,  # Trailing stop + partial TP instead of fixed TP
            reason="ENTRY_SIGNAL",
            trade_id=str(uuid4()),
            timestamp=timestamp or datetime.now(timezone.utc),
            entry_extension=entry_extension,
            regime=regime,
            volume_ratio=volume_ratio,
        )

    def _check_exit(
        self,
        position: Position,
        trend: TrendDirection,
        latest_4h: pd.Series,
        atr: float,
        current_time: datetime | None = None,
    ) -> Signal | None:
        now = current_time or datetime.now(timezone.utc)
        held_days = (now - position.opened_at).total_seconds() / 86400
        price = float(latest_4h["close"])

        if position.side == "LONG" and trend != "UPTREND":
            return Signal(
                symbol=position.symbol,
                signal_type=SignalType.EXIT,
                score=None,
                price=price,
                atr=atr,
                reason="TREND_INVALIDATION",
                trade_id=position.trade_id,
                timestamp=now,
            )
        if position.side == "SHORT" and trend != "DOWNTREND":
            return Signal(
                symbol=position.symbol,
                signal_type=SignalType.EXIT,
                score=None,
                price=price,
                atr=atr,
                reason="TREND_INVALIDATION",
                trade_id=position.trade_id,
                timestamp=now,
            )

        if atr > 0 and held_days >= self.config.exit.time_stop_days:
            profit_atr = abs(price - position.entry_price) / atr
            if profit_atr < self.config.exit.time_stop_min_profit_atr:
                return Signal(
                    symbol=position.symbol,
                    signal_type=SignalType.EXIT,
                    score=None,
                    price=price,
                    atr=atr,
                    reason="TIME_STOP",
                    trade_id=position.trade_id,
                    timestamp=now,
                )
        return None
