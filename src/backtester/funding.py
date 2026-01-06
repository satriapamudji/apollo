"""Funding rate provider for backtesting.

Provides funding rates either from historical data or a constant rate for stress testing.

Event-based funding model:
- Funding is applied as discrete cashflows at settlement timestamps only
- No per-bar duration accrual
- Correct payer/receiver direction based on position side
- No leverage multiplier (funding is based on notional, not margin)
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

import pandas as pd

PositionSide = Literal["LONG", "SHORT"]


@dataclass(frozen=True)
class FundingRateInfo:
    """Information about a funding rate at a specific settlement time."""

    rate: float  # Funding rate as decimal (e.g., 0.0001 = 0.01%)
    funding_time: datetime  # Settlement time when funding is exchanged
    mark_price: float | None  # Mark price at funding time (for notional calculation)


class FundingRateProvider:
    """Provides funding rates for backtesting with event-based settlement.

    Two modes:
    - Historical: Use actual settlement times from CSV data
    - Constant: Generate synthetic settlements at 00:00, 08:00, 16:00 UTC

    Funding is applied as discrete cashflows at settlement timestamps only.
    No leverage multiplier - funding is based on notional exposure, not margin.
    """

    FUNDING_INTERVAL_HOURS = 8
    # Standard Binance funding times (hours UTC)
    FUNDING_HOURS_UTC = [0, 8, 16]

    def __init__(
        self,
        funding_data: pd.DataFrame | None = None,
        constant_rate: float | None = None,
    ) -> None:
        """Initialize the funding rate provider.

        Args:
            funding_data: DataFrame with funding history, indexed by timestamp.
                          Must have 'funding_rate' column (optional: 'mark_price').
            constant_rate: If provided, use this rate at synthetic 8h intervals.
        """
        self._funding_data = funding_data
        self._constant_rate = constant_rate
        self._funding_times: list[datetime] = []
        self._funding_rates: dict[datetime, float] = {}
        self._mark_prices: dict[datetime, float | None] = {}

        if funding_data is not None and not funding_data.empty:
            if "funding_rate" not in funding_data.columns:
                self._funding_data = None
            else:
                # Extract actual funding times from the data
                self._build_funding_index(funding_data)

    def _build_funding_index(self, funding_data: pd.DataFrame) -> None:
        """Build index of funding settlement times from historical data."""
        for idx in funding_data.index:
            # Convert to datetime
            if isinstance(idx, pd.Timestamp):
                ts = idx.to_pydatetime()
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
            else:
                ts = idx
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)

            rate = funding_data.loc[idx, "funding_rate"]
            if pd.isna(rate):
                continue

            self._funding_times.append(ts)
            self._funding_rates[ts] = float(rate)

            # Store mark price if available
            if "mark_price" in funding_data.columns:
                mark = funding_data.loc[idx, "mark_price"]
                self._mark_prices[ts] = float(mark) if not pd.isna(mark) else None
            else:
                self._mark_prices[ts] = None

        # Sort funding times for efficient iteration
        self._funding_times.sort()

    def iter_funding_events(
        self,
        start: datetime,
        end: datetime,
    ) -> Iterator[FundingRateInfo]:
        """Yield funding settlement events in the half-open interval (start, end].

        This is the primary method for event-based funding application.
        Returns funding events that occurred AFTER start and up to (including) end.

        Args:
            start: Start of interval (exclusive)
            end: End of interval (inclusive)

        Yields:
            FundingRateInfo for each settlement in the interval
        """
        # Normalize to UTC for comparison
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)

        if self._constant_rate is not None:
            # Generate synthetic settlements for constant rate mode
            yield from self._generate_synthetic_settlements(start, end)
        elif self._funding_times:
            # Use historical funding data
            for funding_time in self._funding_times:
                if funding_time > start and funding_time <= end:
                    yield FundingRateInfo(
                        rate=self._funding_rates[funding_time],
                        funding_time=funding_time,
                        mark_price=self._mark_prices.get(funding_time),
                    )

    def _generate_synthetic_settlements(
        self,
        start: datetime,
        end: datetime,
    ) -> Iterator[FundingRateInfo]:
        """Generate synthetic funding settlements for constant rate mode.

        Settlements occur at 00:00, 08:00, 16:00 UTC each day.
        """
        if self._constant_rate is None:
            return

        # Find first settlement time after start
        current = start.replace(minute=0, second=0, microsecond=0)

        # Move to the next funding hour
        while current.hour not in self.FUNDING_HOURS_UTC:
            current += timedelta(hours=1)

        # If current is at or before start, move to next funding time
        if current <= start:
            # Find next funding hour
            hour_idx = self.FUNDING_HOURS_UTC.index(current.hour)
            if hour_idx < len(self.FUNDING_HOURS_UTC) - 1:
                next_hour = self.FUNDING_HOURS_UTC[hour_idx + 1]
                current = current.replace(hour=next_hour)
            else:
                # Move to next day, first funding hour
                current = (current + timedelta(days=1)).replace(hour=self.FUNDING_HOURS_UTC[0])

        # Generate settlements until end
        while current <= end:
            yield FundingRateInfo(
                rate=self._constant_rate,
                funding_time=current,
                mark_price=None,
            )

            # Move to next funding time
            hour_idx = self.FUNDING_HOURS_UTC.index(current.hour)
            if hour_idx < len(self.FUNDING_HOURS_UTC) - 1:
                next_hour = self.FUNDING_HOURS_UTC[hour_idx + 1]
                current = current.replace(hour=next_hour)
            else:
                # Move to next day, first funding hour
                current = (current + timedelta(days=1)).replace(hour=self.FUNDING_HOURS_UTC[0])

    def calculate_funding_cashflow(
        self,
        notional: float,
        funding_rate: float,
        position_side: PositionSide,
    ) -> float:
        """Calculate funding cashflow at a settlement time.

        Funding direction:
        - If funding_rate > 0: LONG pays, SHORT receives
        - If funding_rate < 0: SHORT pays, LONG receives

        Returns:
            Cashflow amount (positive = position pays, negative = position receives)
            This value should be SUBTRACTED from equity.

        Note: No leverage multiplier. Funding is based on notional exposure only.
        """
        # LONG: cashflow = notional * rate (pays on positive rate)
        # SHORT: cashflow = -notional * rate (receives on positive rate)
        if position_side == "LONG":
            return notional * funding_rate
        else:
            return -notional * funding_rate

    def get_rate(self, timestamp: datetime) -> FundingRateInfo:
        """Get the funding rate that applies at the given timestamp.

        This is a convenience method for looking up the rate at a specific time.
        For event-based processing, use iter_funding_events() instead.

        Args:
            timestamp: The timestamp to get the funding rate for.

        Returns:
            FundingRateInfo with the rate and metadata.
        """
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)

        if self._constant_rate is not None:
            return FundingRateInfo(
                rate=self._constant_rate,
                funding_time=timestamp,
                mark_price=None,
            )

        if not self._funding_times:
            # No data available, return zero
            return FundingRateInfo(rate=0.0, funding_time=timestamp, mark_price=None)

        # Find the most recent funding time at or before this timestamp
        applicable_time = None
        for funding_time in reversed(self._funding_times):
            if funding_time <= timestamp:
                applicable_time = funding_time
                break

        if applicable_time is None:
            # No funding data before this time, use the first available
            applicable_time = self._funding_times[0]

        return FundingRateInfo(
            rate=self._funding_rates.get(applicable_time, 0.0),
            funding_time=applicable_time,
            mark_price=self._mark_prices.get(applicable_time),
        )

    def is_historical_mode(self) -> bool:
        """Return True if using historical funding data."""
        return self._constant_rate is None and len(self._funding_times) > 0

    def is_constant_mode(self) -> bool:
        """Return True if using constant rate mode."""
        return self._constant_rate is not None

    def is_no_data_mode(self) -> bool:
        """Return True if no funding data is available."""
        return self._constant_rate is None and len(self._funding_times) == 0

    @property
    def funding_times(self) -> list[datetime]:
        """Return list of all funding settlement times (historical mode only)."""
        return self._funding_times.copy()
