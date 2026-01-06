"""Market regime detection using ADX, Choppiness Index, and volatility regime."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import pandas as pd

from src.config.settings import RegimeConfig


class RegimeType(str, Enum):
    """Market regime types."""

    TRENDING = "TRENDING"
    CHOPPY = "CHOPPY"
    TRANSITIONAL = "TRANSITIONAL"


class VolatilityRegimeType(str, Enum):
    """Volatility regime types."""

    CONTRACTION = "CONTRACTION"
    NORMAL = "NORMAL"
    EXPANSION = "EXPANSION"


@dataclass(frozen=True)
class RegimeClassification:
    """Result of regime classification."""

    regime: RegimeType
    adx: float
    chop: float
    blocks_entry: bool
    size_multiplier: float
    volatility_regime: VolatilityRegimeType | None = None
    volatility_contracts: bool = False
    volatility_expands: bool = False


class RegimeClassifier:
    """Classify market regime using ADX, Choppiness Index, and volatility regime."""

    def __init__(self, config: RegimeConfig) -> None:
        self.config = config

    def classify(
        self,
        adx: float,
        chop: float,
        atr: float | None = None,
        atr_pct: float | None = None,
        prior_atr_pct: float | None = None,
    ) -> RegimeClassification:
        """Classify the current market regime.

        Args:
            adx: Average Directional Index value (0-100)
            chop: Choppiness Index value (0-100)
            atr: Current ATR value (optional)
            atr_pct: Current ATR as percentage of price (optional)
            prior_atr_pct: Prior ATR as percentage of price (optional, for regime detection)

        Returns:
            RegimeClassification with regime type and entry rules
        """
        if not self.config.enabled:
            return RegimeClassification(
                regime=RegimeType.TRENDING,
                adx=adx,
                chop=chop,
                blocks_entry=False,
                size_multiplier=1.0,
            )

        # TRENDING: ADX >= adx_trending_threshold AND CHOP <= chop_trending_threshold
        trending = adx >= self.config.adx_trending_threshold and chop <= self.config.chop_trending_threshold

        # CHOPPY: ADX <= adx_ranging_threshold OR CHOP >= chop_ranging_threshold
        choppy = adx <= self.config.adx_ranging_threshold or chop >= self.config.chop_ranging_threshold

        if trending:
            regime = RegimeType.TRENDING
            blocks_entry = False
            size_multiplier = 1.0
        elif choppy:
            regime = RegimeType.CHOPPY
            blocks_entry = True
            size_multiplier = 0.0
        else:
            regime = RegimeType.TRANSITIONAL
            blocks_entry = False
            size_multiplier = self.config.transitional_size_multiplier

        # Volatility regime detection (if enabled and data available)
        volatility_regime = None
        volatility_contracts = False
        volatility_expands = False

        if self.config.volatility_regime_enabled and atr_pct is not None and prior_atr_pct is not None:
            volatility_ratio = atr_pct / prior_atr_pct if prior_atr_pct > 0 else 1.0

            if volatility_ratio <= self.config.volatility_contraction_threshold:
                volatility_regime = VolatilityRegimeType.CONTRACTION
                volatility_contracts = True
            elif volatility_ratio >= self.config.volatility_expansion_threshold:
                volatility_regime = VolatilityRegimeType.EXPANSION
                volatility_expands = True
            else:
                volatility_regime = VolatilityRegimeType.NORMAL

        return RegimeClassification(
            regime=regime,
            adx=adx,
            chop=chop,
            blocks_entry=blocks_entry,
            size_multiplier=size_multiplier,
            volatility_regime=volatility_regime,
            volatility_contracts=volatility_contracts,
            volatility_expands=volatility_expands,
        )

    def classify_from_dataframe(
        self,
        df: pd.DataFrame,
        atr_column: str = "atr",
        price_column: str = "close",
    ) -> RegimeClassification:
        """Classify regime from a DataFrame with indicator columns.

        Args:
            df: DataFrame with at least 'adx', 'chop', and ATR columns
            atr_column: Name of ATR column
            price_column: Name of price column

        Returns:
            RegimeClassification with regime type and entry rules
        """
        latest = df.iloc[-1]
        adx = float(latest["adx"]) if not pd.isna(latest.get("adx")) else 0.0
        chop = float(latest["chop"]) if not pd.isna(latest.get("chop")) else 50.0
        atr = float(latest[atr_column]) if not pd.isna(latest.get(atr_column)) else 0.0
        price = float(latest[price_column]) if not pd.isna(latest.get(price_column)) else 0.0

        atr_pct = (atr / price * 100) if price > 0 else None

        # Get prior ATR percentage for regime detection
        prior_atr_pct = None
        if len(df) >= 2 and self.config.volatility_regime_enabled:
            prior_atr = float(df[atr_column].iloc[-2]) if not pd.isna(df[atr_column].iloc[-2]) else atr
            prior_price = float(df[price_column].iloc[-2]) if not pd.isna(df[price_column].iloc[-2]) else price
            prior_atr_pct = (prior_atr / prior_price * 100) if prior_price > 0 else None

        return self.classify(adx=adx, chop=chop, atr=atr, atr_pct=atr_pct, prior_atr_pct=prior_atr_pct)
