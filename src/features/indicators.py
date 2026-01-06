"""Technical indicator calculations."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd


def calculate_ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential moving average."""
    return series.ewm(span=period, adjust=False).mean()


def calculate_atr(df: pd.DataFrame, period: int) -> pd.Series:
    """Average True Range."""
    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev_close = close.shift(1)
    true_range = pd.concat(
        [
            (high - low).abs(),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.rolling(window=period, min_periods=period).mean()


def calculate_rsi(series: pd.Series, period: int) -> pd.Series:
    """Relative Strength Index."""
    delta = series.diff()
    gains = delta.where(delta > 0, 0.0)
    losses = -delta.where(delta < 0, 0.0)
    avg_gain = gains.rolling(window=period, min_periods=period).mean()
    avg_loss = losses.rolling(window=period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, pd.NA)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)


def _smooth(series: pd.Series, period: int, alpha: float | None = None) -> pd.Series:
    """Smooth series using Wilder's smoothing (EMA with alpha=1/period)."""
    if alpha is None:
        alpha = 1.0 / period
    return series.ewm(alpha=alpha, adjust=False).mean()


def calculate_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average Directional Index - measures trend strength (0-100).

    ADX > 25 = trending, ADX < 20 = ranging/choppy.
    Returns a Series with the same index as input.
    """
    high = df["high"]
    low = df["low"]
    close = df["close"]

    # True Range
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    # +DM and -DM
    plus_dm = high.diff()
    minus_dm = -low.diff()

    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

    # Smooth using Wilder's method
    tr_smooth = _smooth(tr, period)
    plus_di_smooth = _smooth(plus_dm, period) / tr_smooth * 100
    minus_di_smooth = _smooth(minus_dm, period) / tr_smooth * 100

    # DX and ADX
    dx = (plus_di_smooth - minus_di_smooth).abs() / (plus_di_smooth + minus_di_smooth) * 100
    dx = dx.replace([np.inf, -np.inf], 0).fillna(0)

    # ADX is smoothed DX
    adx = _smooth(dx, period)
    adx = adx.fillna(0)

    return adx


def calculate_choppiness_index(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Choppiness Index - measures market choppiness (0-100).

    CHOP < 38.2 = trending, CHOP > 61.8 = choppy/ranging.
    Formula: 100 * LOG10(SUM(ATR, n) / (Highest High - Lowest Low)) / LOG10(n)
    Returns a Series with the same index as input.
    """
    high = df["high"]
    low = df["low"]
    close = df["close"]

    # True Range for ATR calculation
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    # Sum of ATR over period
    atr_sum = tr.rolling(window=period, min_periods=period).sum()

    # Highest High - Lowest Low over period
    hh = high.rolling(window=period, min_periods=period).max()
    ll = low.rolling(window=period, min_periods=period).min()
    range_sum = hh - ll

    # Choppiness Index
    log_n = math.log10(period)

    chop = pd.Series(index=df.index, dtype=float)
    valid_mask = (range_sum > 0) & (atr_sum > 0)
    chop_values = 100 * np.log10(atr_sum.loc[valid_mask] / range_sum.loc[valid_mask]) / log_n
    chop.loc[valid_mask] = chop_values
    chop = chop.fillna(50)  # Neutral value for invalid periods

    return chop


def calculate_volume_sma(volume: pd.Series, period: int) -> pd.Series:
    """Simple moving average of volume.

    Args:
        volume: Volume series
        period: Lookback period for SMA

    Returns:
        Series with volume SMA values
    """
    return volume.rolling(window=period, min_periods=period).mean()


def calculate_volume_ratio(volume: pd.Series, period: int) -> pd.Series:
    """Current volume as ratio of SMA (1.0 = average, 2.0 = 2x average).

    Args:
        volume: Volume series
        period: Lookback period for SMA calculation

    Returns:
        Series with volume ratio values (current volume / SMA)
    """
    sma = calculate_volume_sma(volume, period)
    return volume / sma.replace(0, pd.NA)
