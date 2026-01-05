"""Feature pipeline for market data."""

from __future__ import annotations

from typing import Any

import pandas as pd

from src.config.settings import IndicatorConfig
from src.features.indicators import calculate_atr, calculate_ema, calculate_rsi


class FeaturePipeline:
    """Compute indicators for incoming OHLCV data."""

    def __init__(self, indicators: IndicatorConfig) -> None:
        self._indicators = indicators

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        """Return a new DataFrame with indicator columns appended."""
        result = df.copy()
        result["ema_fast"] = calculate_ema(result["close"], self._indicators.ema_fast)
        result["ema_slow"] = calculate_ema(result["close"], self._indicators.ema_slow)
        result["atr"] = calculate_atr(result, self._indicators.atr_period)
        result["rsi"] = calculate_rsi(result["close"], self._indicators.rsi_period)
        return result

    def latest_features(self, df: pd.DataFrame) -> dict[str, Any]:
        """Return latest row of computed features as a dict."""
        computed = self.compute(df)
        latest = computed.iloc[-1].to_dict()
        return latest
