"""Feature pipeline for market data."""

from __future__ import annotations

from typing import Any

import pandas as pd

from src.config.settings import IndicatorConfig
from src.features.indicators import (
    calculate_adx,
    calculate_atr,
    calculate_choppiness_index,
    calculate_ema,
    calculate_rsi,
    calculate_volume_ratio,
    calculate_volume_sma,
)


class FeaturePipeline:
    """Compute indicators for incoming OHLCV data."""

    def __init__(self, indicators: IndicatorConfig) -> None:
        self._indicators = indicators

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        """Return a new DataFrame with indicator columns appended."""
        required_cols = {"open", "high", "low", "close"}
        missing = required_cols.difference(df.columns)
        if missing:
            raise ValueError(f"missing_columns: {sorted(missing)}")
        result = df.copy()
        result["ema_fast"] = calculate_ema(result["close"], self._indicators.ema_fast)
        result["ema_slow"] = calculate_ema(result["close"], self._indicators.ema_slow)
        result["atr"] = calculate_atr(result, self._indicators.atr_period)
        result["rsi"] = calculate_rsi(result["close"], self._indicators.rsi_period)
        # Regime indicators
        result["adx"] = calculate_adx(result, self._indicators.adx_period)
        result["chop"] = calculate_choppiness_index(result, self._indicators.chop_period)
        # Volume indicators
        if "volume" in result.columns:
            result["volume_sma"] = calculate_volume_sma(
                result["volume"], self._indicators.volume_sma_period
            )
            result["volume_ratio"] = calculate_volume_ratio(
                result["volume"], self._indicators.volume_sma_period
            )
        return result

    def latest_features(self, df: pd.DataFrame) -> dict[str, Any]:
        """Return latest row of computed features as a dict."""
        computed = self.compute(df)
        latest = computed.iloc[-1].to_dict()
        return latest
