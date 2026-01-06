"""Spread modeling for backtesting execution realism.

This module provides spread data structures and models for incorporating
bid-ask spread costs into backtest execution simulation.

Two approaches are supported:
1. Historical spreads: Real spread data collected via the spread collector tool
2. Conservative model: Deterministic ATR-scaled spread estimation for historical backtests

IMPORTANT: The conservative model produces MODELED spreads, not real market data.
When using modeled spreads, results should be interpreted with appropriate caution.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Protocol

import pandas as pd

from src.backtester.execution_sim import VolatilityRegime, detect_volatility_regime

if TYPE_CHECKING:
    pass


@dataclass(frozen=True)
class SpreadSnapshot:
    """A point-in-time snapshot of bid-ask spread.

    Attributes:
        timestamp: UTC timestamp of the snapshot
        symbol: Trading symbol (e.g., 'BTCUSDT')
        bid: Best bid price
        ask: Best ask price
        bid_qty: Quantity at best bid (optional)
        ask_qty: Quantity at best ask (optional)
        is_modeled: True if this is a modeled spread, False if from real data
    """

    timestamp: datetime
    symbol: str
    bid: float
    ask: float
    bid_qty: float | None = None
    ask_qty: float | None = None
    is_modeled: bool = False

    @property
    def mid(self) -> float:
        """Calculate mid price."""
        return (self.bid + self.ask) / 2

    @property
    def spread_pct(self) -> float:
        """Calculate spread as percentage of mid price.

        Returns:
            Spread percentage (e.g., 0.05 means 0.05%)
        """
        mid = self.mid
        if mid <= 0:
            return 0.0
        return ((self.ask - self.bid) / mid) * 100

    @property
    def spread_decimal(self) -> float:
        """Calculate spread as decimal fraction.

        Returns:
            Spread decimal (e.g., 0.0005 means 0.05%)
        """
        return self.spread_pct / 100


class SpreadModel(Protocol):
    """Protocol for spread data providers.

    Implementations can provide either historical spreads from collected data
    or modeled spreads using deterministic estimation.
    """

    def get_spread(
        self,
        symbol: str,
        timestamp: datetime,
        price: float,
        atr: float,
    ) -> SpreadSnapshot:
        """Get spread snapshot for a given symbol and time.

        Args:
            symbol: Trading symbol
            timestamp: Point in time to get spread for
            price: Current market price (for model-based estimation)
            atr: Average True Range (for volatility-adjusted models)

        Returns:
            SpreadSnapshot with bid/ask prices
        """
        ...


class ConservativeSpreadModel:
    """Conservative deterministic spread model for historical backtests.

    This model estimates spreads based on ATR and volatility regime, providing
    a realistic floor for execution costs when historical spread data is unavailable.

    Model assumptions (documented for transparency):
    - Base spread represents typical calm-market conditions
    - Spread scales with ATR (higher volatility = wider spreads)
    - Volatility regime multipliers match execution_sim.py conventions
    - Model tends toward conservative (wider) spreads to avoid overfitting

    Formula:
        spread_pct = (base_spread_pct + atr_pct * atr_scale) * regime_multiplier

    Regime multipliers:
        - LOW volatility (ATR < 0.5%): 0.5x
        - NORMAL volatility (ATR 0.5-1.5%): 1.0x
        - HIGH volatility (ATR > 1.5%): 2.0x
    """

    def __init__(
        self,
        base_spread_pct: float = 0.01,
        atr_scale: float = 0.5,
    ) -> None:
        """Initialize the conservative spread model.

        Args:
            base_spread_pct: Base spread in calm market conditions (default: 0.01%)
            atr_scale: How much ATR affects spread (default: 0.5, meaning
                      spread increases by 0.5% for each 1% of ATR)
        """
        self.base_spread_pct = base_spread_pct
        self.atr_scale = atr_scale

    def get_spread(
        self,
        symbol: str,
        timestamp: datetime,
        price: float,
        atr: float,
    ) -> SpreadSnapshot:
        """Generate a modeled spread snapshot.

        Args:
            symbol: Trading symbol
            timestamp: Point in time
            price: Current market price
            atr: Average True Range in price units

        Returns:
            SpreadSnapshot with modeled bid/ask prices
        """
        if price <= 0:
            # Edge case: invalid price
            return SpreadSnapshot(
                timestamp=timestamp,
                symbol=symbol,
                bid=0.0,
                ask=0.0,
                is_modeled=True,
            )

        # Calculate ATR as percentage of price
        atr_pct = (atr / price) * 100 if atr > 0 else 0.0

        # Detect volatility regime
        atr_decimal = atr / price if price > 0 else 0.0
        regime = detect_volatility_regime(atr_decimal)

        # Regime multipliers (consistent with execution_sim.py)
        regime_multiplier = {
            VolatilityRegime.LOW: 0.5,
            VolatilityRegime.NORMAL: 1.0,
            VolatilityRegime.HIGH: 2.0,
        }[regime]

        # Calculate spread percentage
        spread_pct = (self.base_spread_pct + atr_pct * self.atr_scale) * regime_multiplier

        # Convert to bid/ask prices
        # spread_pct is percentage of mid, so half-spread on each side
        half_spread_decimal = (spread_pct / 100) / 2
        bid = price * (1 - half_spread_decimal)
        ask = price * (1 + half_spread_decimal)

        return SpreadSnapshot(
            timestamp=timestamp,
            symbol=symbol,
            bid=bid,
            ask=ask,
            is_modeled=True,
        )


class HistoricalSpreadProvider:
    """Provides historical spreads from collected data.

    Uses real spread snapshots collected via the spread collector tool.
    Falls back to None if no data is available for the requested time.
    """

    def __init__(self, spread_data: pd.DataFrame) -> None:
        """Initialize with historical spread data.

        Args:
            spread_data: DataFrame with columns [timestamp, symbol, bid, ask]
                        indexed by timestamp (datetime with UTC timezone).
                        Optional columns: bid_qty, ask_qty
        """
        self._data = spread_data
        self._has_qty = "bid_qty" in spread_data.columns and "ask_qty" in spread_data.columns

        # Ensure index is datetime
        if not isinstance(self._data.index, pd.DatetimeIndex):
            if "timestamp" in self._data.columns:
                self._data = self._data.set_index("timestamp")

    def get_spread(
        self,
        symbol: str,
        timestamp: datetime,
        price: float,
        atr: float,
    ) -> SpreadSnapshot | None:
        """Get the closest historical spread snapshot.

        Uses forward-fill logic: returns the most recent spread at or before
        the requested timestamp to avoid lookahead bias.

        Args:
            symbol: Trading symbol
            timestamp: Point in time to get spread for
            price: Current market price (unused for historical data)
            atr: Average True Range (unused for historical data)

        Returns:
            SpreadSnapshot if data available, None otherwise
        """
        if self._data.empty:
            return None

        # Ensure timestamp is timezone-aware
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)

        # Filter by symbol if column exists
        if "symbol" in self._data.columns:
            symbol_data = self._data[self._data["symbol"] == symbol]
        else:
            symbol_data = self._data

        if symbol_data.empty:
            return None

        # Find the most recent spread at or before timestamp (no lookahead)
        mask = symbol_data.index <= timestamp
        if not mask.any():
            return None

        row = symbol_data.loc[mask].iloc[-1]

        bid_qty = float(row["bid_qty"]) if self._has_qty and pd.notna(row.get("bid_qty")) else None
        ask_qty = float(row["ask_qty"]) if self._has_qty and pd.notna(row.get("ask_qty")) else None

        return SpreadSnapshot(
            timestamp=timestamp,
            symbol=symbol,
            bid=float(row["bid"]),
            ask=float(row["ask"]),
            bid_qty=bid_qty,
            ask_qty=ask_qty,
            is_modeled=False,
        )


class HybridSpreadProvider:
    """Combines historical data with model fallback.

    Uses historical spreads when available, falls back to conservative model
    when historical data is missing for a given timestamp.
    """

    def __init__(
        self,
        historical_provider: HistoricalSpreadProvider | None = None,
        model: ConservativeSpreadModel | None = None,
    ) -> None:
        """Initialize hybrid provider.

        Args:
            historical_provider: Provider for real spread data (optional)
            model: Fallback model when historical data unavailable (optional)
        """
        self._historical = historical_provider
        self._model = model or ConservativeSpreadModel()

    def get_spread(
        self,
        symbol: str,
        timestamp: datetime,
        price: float,
        atr: float,
    ) -> SpreadSnapshot:
        """Get spread, preferring historical data with model fallback.

        Args:
            symbol: Trading symbol
            timestamp: Point in time
            price: Current market price
            atr: Average True Range

        Returns:
            SpreadSnapshot from historical data or model
        """
        # Try historical first
        if self._historical is not None:
            historical_spread = self._historical.get_spread(symbol, timestamp, price, atr)
            if historical_spread is not None:
                return historical_spread

        # Fall back to model
        return self._model.get_spread(symbol, timestamp, price, atr)
