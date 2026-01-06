"""Dataset reader for multi-symbol event-driven backtesting.

Provides per-symbol iterators that yield BarEvent and FundingEvent objects
from historical CSV data. Used by EventMux for deterministic replay.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from src.backtester.data import load_funding_csv, load_symbol_interval
from src.backtester.events import BarEvent, FundingEvent


class SymbolDataIterator:
    """Iterator yielding BarEvent objects for a single symbol.

    Provides lookahead-free iteration over bar data. Each call to __next__
    returns the next BarEvent in chronological order.
    """

    def __init__(
        self,
        symbol: str,
        interval: str,
        df: pd.DataFrame,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> None:
        """Initialize the iterator.

        Args:
            symbol: Trading symbol (e.g., 'BTCUSDT')
            interval: Candle interval (e.g., '4h')
            df: DataFrame with OHLCV data indexed by close_time
            start_time: Optional filter - skip bars before this time
            end_time: Optional filter - skip bars after this time
        """
        self.symbol = symbol
        self.interval = interval
        self._df = df
        self._start_time = start_time
        self._end_time = end_time
        self._index = 0
        self._sequence = 0

        # Apply time filters
        if start_time is not None:
            self._df = self._df[self._df.index >= start_time]
        if end_time is not None:
            self._df = self._df[self._df.index <= end_time]

    def __iter__(self) -> Iterator[BarEvent]:
        return self

    def __next__(self) -> BarEvent:
        if self._index >= len(self._df):
            raise StopIteration

        row = self._df.iloc[self._index]
        timestamp = self._df.index[self._index]

        # Convert to datetime with UTC
        if isinstance(timestamp, pd.Timestamp):
            bar_time = timestamp.to_pydatetime()
        else:
            bar_time = timestamp
        if bar_time.tzinfo is None:
            bar_time = bar_time.replace(tzinfo=timezone.utc)

        # Get open_time if available
        open_time: datetime | None = None
        if "open_time" in row.index and pd.notna(row["open_time"]):
            ot = row["open_time"]
            if isinstance(ot, pd.Timestamp):
                open_time = ot.to_pydatetime()
            else:
                open_time = ot
            if open_time is not None and open_time.tzinfo is None:
                open_time = open_time.replace(tzinfo=timezone.utc)

        event = BarEvent(
            symbol=self.symbol,
            interval=self.interval,
            timestamp=bar_time,
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row["volume"]),
            open_time=open_time,
            sequence=self._sequence,
        )

        self._index += 1
        self._sequence += 1
        return event

    def peek(self) -> BarEvent | None:
        """Peek at the next event without consuming it."""
        if self._index >= len(self._df):
            return None

        # Temporarily get next without advancing
        current_idx = self._index
        current_seq = self._sequence
        try:
            return next(self)
        finally:
            self._index = current_idx
            self._sequence = current_seq


class FundingDataIterator:
    """Iterator yielding FundingEvent objects for a single symbol."""

    def __init__(
        self,
        symbol: str,
        df: pd.DataFrame,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> None:
        """Initialize the iterator.

        Args:
            symbol: Trading symbol
            df: DataFrame with funding data indexed by timestamp
            start_time: Optional filter - skip events before this time
            end_time: Optional filter - skip events after this time
        """
        self.symbol = symbol
        self._df = df
        self._start_time = start_time
        self._end_time = end_time
        self._index = 0
        self._sequence = 0

        # Apply time filters
        if start_time is not None:
            self._df = self._df[self._df.index >= start_time]
        if end_time is not None:
            self._df = self._df[self._df.index <= end_time]

    def __iter__(self) -> Iterator[FundingEvent]:
        return self

    def __next__(self) -> FundingEvent:
        if self._index >= len(self._df):
            raise StopIteration

        row = self._df.iloc[self._index]
        timestamp = self._df.index[self._index]

        # Convert to datetime with UTC
        if isinstance(timestamp, pd.Timestamp):
            funding_time = timestamp.to_pydatetime()
        else:
            funding_time = timestamp
        if funding_time.tzinfo is None:
            funding_time = funding_time.replace(tzinfo=timezone.utc)

        # Get mark_price if available
        mark_price: float | None = None
        if "mark_price" in row.index and pd.notna(row["mark_price"]):
            mark_price = float(row["mark_price"])

        event = FundingEvent(
            symbol=self.symbol,
            funding_time=funding_time,
            rate=float(row["funding_rate"]),
            mark_price=mark_price,
            sequence=self._sequence,
        )

        self._index += 1
        self._sequence += 1
        return event

    def peek(self) -> FundingEvent | None:
        """Peek at the next event without consuming it."""
        if self._index >= len(self._df):
            return None

        current_idx = self._index
        current_seq = self._sequence
        try:
            return next(self)
        finally:
            self._index = current_idx
            self._sequence = current_seq


class DatasetReader:
    """Multi-symbol dataset reader for event-driven backtesting.

    Loads historical data for multiple symbols and provides iterators
    for bar and funding events. Used by EventMux for deterministic
    cross-symbol event replay.

    Example:
        reader = DatasetReader(
            data_path="./data/market",
            symbols=["BTCUSDT", "ETHUSDT"],
            interval="4h",
        )
        for symbol, bar_iter in reader.bar_iterators():
            for bar in bar_iter:
                process_bar(bar)
    """

    def __init__(
        self,
        data_path: str | Path,
        symbols: list[str],
        interval: str,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        funding_path: str | Path | None = None,
    ) -> None:
        """Initialize the dataset reader.

        Args:
            data_path: Directory containing OHLCV CSVs (symbol_interval.csv)
            symbols: List of symbols to load
            interval: Candle interval (e.g., '4h')
            start_time: Optional start filter for all data
            end_time: Optional end filter for all data
            funding_path: Directory containing funding CSVs (default: data_path/../funding)
        """
        self.data_path = Path(data_path)
        self.symbols = symbols
        self.interval = interval
        self.start_time = start_time
        self.end_time = end_time
        self.funding_path = Path(funding_path) if funding_path else self.data_path.parent

        # Cache loaded dataframes
        self._bar_data: dict[str, pd.DataFrame] = {}
        self._funding_data: dict[str, pd.DataFrame] = {}

        # Load all data upfront
        self._load_all_data()

    def _load_all_data(self) -> None:
        """Load all symbol data into memory."""
        for symbol in self.symbols:
            # Load bar data
            try:
                df = load_symbol_interval(self.data_path, symbol, self.interval)
                self._bar_data[symbol] = df
            except FileNotFoundError:
                # Skip symbols without data
                continue

            # Load funding data
            try:
                funding_df = load_funding_csv(symbol, self.funding_path)
                if not funding_df.empty:
                    self._funding_data[symbol] = funding_df
            except (FileNotFoundError, ValueError):
                # Funding data is optional
                pass

    def bar_iterators(self) -> dict[str, SymbolDataIterator]:
        """Get bar iterators for all loaded symbols.

        Returns:
            Dictionary mapping symbol to SymbolDataIterator
        """
        result: dict[str, SymbolDataIterator] = {}
        for symbol, df in self._bar_data.items():
            result[symbol] = SymbolDataIterator(
                symbol=symbol,
                interval=self.interval,
                df=df.copy(),
                start_time=self.start_time,
                end_time=self.end_time,
            )
        return result

    def funding_iterators(self) -> dict[str, FundingDataIterator]:
        """Get funding iterators for all loaded symbols.

        Returns:
            Dictionary mapping symbol to FundingDataIterator
        """
        result: dict[str, FundingDataIterator] = {}
        for symbol, df in self._funding_data.items():
            result[symbol] = FundingDataIterator(
                symbol=symbol,
                df=df.copy(),
                start_time=self.start_time,
                end_time=self.end_time,
            )
        return result

    def get_bar_data(self, symbol: str) -> pd.DataFrame | None:
        """Get raw bar data for a symbol.

        Returns None if symbol not loaded.
        """
        return self._bar_data.get(symbol)

    def get_funding_data(self, symbol: str) -> pd.DataFrame | None:
        """Get raw funding data for a symbol.

        Returns None if symbol not loaded or no funding data available.
        """
        return self._funding_data.get(symbol)

    @property
    def loaded_symbols(self) -> list[str]:
        """List of symbols with successfully loaded bar data."""
        return list(self._bar_data.keys())

    def get_time_range(self) -> tuple[datetime | None, datetime | None]:
        """Get the overall time range of loaded data.

        Returns:
            Tuple of (earliest_time, latest_time) across all symbols
        """
        min_time: datetime | None = None
        max_time: datetime | None = None

        for df in self._bar_data.values():
            if df.empty:
                continue

            first_ts = df.index[0]
            last_ts = df.index[-1]

            # Convert to datetime
            if isinstance(first_ts, pd.Timestamp):
                first_dt = first_ts.to_pydatetime()
            else:
                first_dt = first_ts
            if isinstance(last_ts, pd.Timestamp):
                last_dt = last_ts.to_pydatetime()
            else:
                last_dt = last_ts

            if first_dt.tzinfo is None:
                first_dt = first_dt.replace(tzinfo=timezone.utc)
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)

            if min_time is None or first_dt < min_time:
                min_time = first_dt
            if max_time is None or last_dt > max_time:
                max_time = last_dt

        return min_time, max_time
