"""Funding rate provider for backtesting.

Provides funding rates either from historical data or a constant rate for stress testing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd


@dataclass(frozen=True)
class FundingRateInfo:
    """Information about a funding rate at a specific time."""

    rate: float  # Funding rate as decimal (e.g., 0.0001 = 0.01%)
    timestamp: datetime  # When this rate applies
    mark_price: float | None  # Mark price at funding time


class FundingRateProvider:
    """Provides funding rates for backtesting.

    Two modes:
    - Historical: Load from CSV and interpolate for exact timestamps
    - Constant: Use configurable constant rate for stress testing
    """

    FUNDING_INTERVAL_HOURS = 8

    def __init__(
        self,
        funding_data: pd.DataFrame | None = None,
        constant_rate: float | None = None,
    ) -> None:
        """Initialize the funding rate provider.

        Args:
            funding_data: DataFrame with funding history, indexed by timestamp.
                          Must have 'funding_rate' column (optional: 'mark_price').
            constant_rate: If provided, always return this rate (stress testing mode).
        """
        self._funding_data = funding_data
        self._constant_rate = constant_rate

        if funding_data is not None and not funding_data.empty:
            # Ensure we have the required column
            if "funding_rate" not in funding_data.columns:
                self._funding_data = None
            else:
                # Resample to funding intervals for efficient lookup
                self._resampled = funding_data[["funding_rate"]].resample(
                    f"{self.FUNDING_INTERVAL_HOURS}h"
                ).last()
        else:
            self._resampled = None

    def get_rate(self, timestamp: datetime) -> FundingRateInfo:
        """Get the funding rate that applies at the given timestamp.

        Binance funding occurs every 8 hours at 00:00, 08:00, 16:00 UTC.
        The rate applies for the next 8 hours until the next funding time.

        Args:
            timestamp: The timestamp to get the funding rate for.

        Returns:
            FundingRateInfo with the rate and metadata.
        """
        if self._constant_rate is not None:
            return FundingRateInfo(
                rate=self._constant_rate,
                timestamp=timestamp,
                mark_price=None,
            )

        if self._resampled is None or self._resampled.empty:
            # No data available, return zero
            return FundingRateInfo(rate=0.0, timestamp=timestamp, mark_price=None)

        # Find the funding interval that applies at this timestamp
        # Funding rate is determined by the most recent funding event
        ts = pd.Timestamp(timestamp).tz_localize(None)

        # Find the last funding time before or at this timestamp
        applicable = self._resampled[self._resampled.index <= ts]

        if applicable.empty:
            # No funding data before this time, use the first available rate
            applicable = self._resampled.iloc[:1]

        last_funding = applicable.index[-1]
        rate = applicable["funding_rate"].iloc[-1]

        # Handle NaN rates
        if pd.isna(rate):
            rate = 0.0

        # Get mark price if available
        mark_price = None
        if self._funding_data is not None and "mark_price" in self._funding_data.columns:
            mark_data = self._funding_data.loc[self._funding_data.index == last_funding]
            if not mark_data.empty:
                mark_price = float(mark_data["mark_price"].iloc[0])

        return FundingRateInfo(
            rate=float(rate),
            timestamp=last_funding.to_pydatetime(),
            mark_price=mark_price,
        )

    def calculate_funding_cost(
        self,
        position_notional: float,
        leverage: int,
        funding_rate: float,
        hours_held: float,
    ) -> float:
        """Calculate funding cost for a position held for a given duration.

        Funding is paid/received every 8 hours. This calculates the total
        funding cost based on how many funding periods were held.

        Args:
            position_notional: The notional value of the position (price * quantity).
            leverage: The leverage used (affects exposure).
            funding_rate: The funding rate as decimal.
            hours_held: Number of hours the position was held.

        Returns:
            Total funding cost (positive = you pay, negative = you receive).
        """
        # Funding payment = position_notional * leverage * funding_rate * (hours / 8)
        # This is because funding is paid every 8 hours
        periods = hours_held / self.FUNDING_INTERVAL_HOURS
        return position_notional * leverage * funding_rate * periods

    def is_historical_mode(self) -> bool:
        """Return True if using historical funding data."""
        return self._constant_rate is None and self._resampled is not None

    def is_constant_mode(self) -> bool:
        """Return True if using constant rate mode."""
        return self._constant_rate is not None

    def is_no_data_mode(self) -> bool:
        """Return True if no funding data is available."""
        return self._constant_rate is None and self._resampled is None
