"""Signal generation for the trend-following strategy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

import pandas as pd

from src.config.settings import StrategyConfig
from src.features.pipeline import FeaturePipeline
from src.ledger.state import Position
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


class SignalGenerator:
    """Generate entry and exit signals from market data."""

    def __init__(self, config: StrategyConfig) -> None:
        self.config = config
        self.pipeline = FeaturePipeline(config.indicators)
        self.scoring = ScoringEngine()

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
        )

        if self.config.entry.style == "pullback":
            entry_signal = self._pullback_entry(
                symbol,
                trend,
                daily,
                fourh,
                funding_rate,
                news_risk,
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
        current_time: datetime | None = None,
    ) -> Signal:
        latest = fourh.iloc[-1]
        prev = fourh.iloc[-2] if len(fourh) >= 2 else latest
        price = float(latest["close"])
        atr = float(latest["atr"]) if not pd.isna(latest["atr"]) else 0.0

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
            return Signal(symbol=symbol, signal_type=SignalType.NONE, score=None, price=price, atr=atr)

        entry_distance = abs(price - ema_slow) / atr if atr > 0 else 0.0
        score = self._score_entry(direction, daily, price, atr, entry_distance, funding_rate, news_risk)
        signal_time = (
            latest.name.to_pydatetime() if hasattr(latest, "name") else current_time
        )
        return self._build_entry_signal(symbol, direction, price, atr, score, signal_time)

    def _breakout_entry(
        self,
        symbol: str,
        trend: TrendDirection,
        daily: pd.DataFrame,
        fourh: pd.DataFrame,
        funding_rate: float,
        news_risk: Literal["HIGH", "MEDIUM", "LOW"],
        current_time: datetime | None = None,
    ) -> Signal:
        latest = fourh.iloc[-1]
        price = float(latest["close"])
        atr = float(latest["atr"]) if not pd.isna(latest["atr"]) else 0.0

        direction = None
        if len(fourh) >= 21:
            prior = fourh.iloc[-21:-1]
            high_20 = prior["high"].max()
            low_20 = prior["low"].min()
            if trend == "UPTREND" and price > high_20:
                direction = "LONG"
            elif trend == "DOWNTREND" and price < low_20:
                direction = "SHORT"

        if not direction:
            return Signal(symbol=symbol, signal_type=SignalType.NONE, score=None, price=price, atr=atr)

        entry_distance = 0.8
        score = self._score_entry(direction, daily, price, atr, entry_distance, funding_rate, news_risk)
        signal_time = (
            latest.name.to_pydatetime() if hasattr(latest, "name") else current_time
        )
        return self._build_entry_signal(symbol, direction, price, atr, score, signal_time)

    def _score_entry(
        self,
        direction: Literal["LONG", "SHORT"],
        daily: pd.DataFrame,
        price: float,
        atr: float,
        entry_distance: float,
        funding_rate: float,
        news_risk: Literal["HIGH", "MEDIUM", "LOW"],
    ) -> CompositeScore:
        daily_latest = daily.iloc[-1]
        ema_fast = float(daily_latest["ema_fast"])
        ema_slow = float(daily_latest["ema_slow"])
        ema_fast_prev = (
            float(daily["ema_fast"].iloc[-4]) if len(daily) >= 4 else ema_fast
        )
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
        )

    def _build_entry_signal(
        self,
        symbol: str,
        direction: Literal["LONG", "SHORT"],
        price: float,
        atr: float,
        score: CompositeScore,
        timestamp: datetime | None,
    ) -> Signal:
        if score.composite < self.config.entry.score_threshold:
            return Signal(symbol=symbol, signal_type=SignalType.NONE, score=score, price=price, atr=atr)
        stop_distance = self.config.exit.atr_stop_multiplier * atr if atr > 0 else 0.0
        stop_price = price - stop_distance if direction == "LONG" else price + stop_distance
        take_profit = price + (3.0 * atr) if direction == "LONG" else price - (3.0 * atr)
        signal_type = SignalType.LONG if direction == "LONG" else SignalType.SHORT
        return Signal(
            symbol=symbol,
            signal_type=signal_type,
            score=score,
            price=price,
            atr=atr,
            entry_price=price,
            stop_price=stop_price,
            take_profit=take_profit,
            reason="ENTRY_SIGNAL",
            trade_id=str(uuid4()),
            timestamp=timestamp or datetime.now(timezone.utc),
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
