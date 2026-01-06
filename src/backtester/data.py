"""Historical data loading utilities."""

from __future__ import annotations

import warnings
from pathlib import Path

import pandas as pd


def _interval_to_ms(interval: str) -> int:
    """Convert interval string (e.g., '4h', '1d') to milliseconds."""
    multipliers = {
        "m": 60 * 1000,
        "h": 60 * 60 * 1000,
        "d": 24 * 60 * 60 * 1000,
        "w": 7 * 24 * 60 * 60 * 1000,
    }
    unit = interval[-1].lower()
    value = int(interval[:-1])
    if unit not in multipliers:
        raise ValueError(f"Unknown interval unit: {unit}")
    return value * multipliers[unit]


def _convert_timestamp_series(df: pd.DataFrame, col: str) -> None:
    """Convert a timestamp column in place to datetime with UTC timezone."""
    ts = df[col]
    numeric_ts = pd.to_numeric(ts, errors="coerce")
    numeric_series = pd.Series(numeric_ts)
    if numeric_series.notna().any():
        max_val = float(numeric_series.max())
        unit = "ms" if max_val > 1_000_000_000_000 else "s"
        df[col] = pd.to_datetime(numeric_series, unit=unit, utc=True)
    else:
        df[col] = pd.to_datetime(ts, utc=True)


def load_ohlcv_csv(path: str | Path, interval: str | None = None) -> pd.DataFrame:
    """Load OHLCV data from CSV into a normalized DataFrame.

    Canonical schema (preferred):
        Required columns: open_time, close_time, open, high, low, close, volume
        Index: close_time (bar-close convention for replay)

    Legacy support:
        - If only 'timestamp' exists: treated as close_time
        - If only 'open_time' exists: requires 'interval' parameter to derive close_time

    Args:
        path: Path to the CSV file
        interval: Interval string (e.g., '4h', '1d'). Required if CSV has only open_time.

    Returns:
        DataFrame with columns [open_time, open, high, low, close, volume]
        indexed by close_time (datetime, UTC).

    Raises:
        ValueError: If schema is ambiguous or required columns are missing.
    """
    df = pd.read_csv(path)
    columns_lower = {c.lower(): c for c in df.columns}
    required_ohlcv = ["open", "high", "low", "close", "volume"]

    # Check OHLCV columns
    if not all(col in columns_lower for col in required_ohlcv):
        raise ValueError(
            f"CSV must contain open, high, low, close, volume columns. Found: {list(df.columns)}"
        )

    # Rename OHLCV to lowercase
    rename_map = {columns_lower[c]: c for c in required_ohlcv}

    # Determine timestamp schema
    has_close_time = "close_time" in columns_lower
    has_open_time = "open_time" in columns_lower
    has_timestamp = "timestamp" in columns_lower

    if has_close_time:
        # Canonical or near-canonical schema
        rename_map[columns_lower["close_time"]] = "close_time"
        if has_open_time:
            rename_map[columns_lower["open_time"]] = "open_time"
        df = df.rename(columns=rename_map)

        # Convert numeric columns
        for col in required_ohlcv:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        # Convert close_time
        _convert_timestamp_series(df, "close_time")

        # Handle open_time: derive if missing
        if "open_time" not in df.columns:
            if interval is None:
                warnings.warn(
                    "close_time present but open_time missing. "
                    "Deriving open_time requires interval; setting to NaT.",
                    UserWarning,
                    stacklevel=2,
                )
                df["open_time"] = pd.NaT
            else:
                interval_ms = _interval_to_ms(interval)
                # open_time = close_time - interval (plus 1ms since close_time is inclusive)
                df["open_time"] = df["close_time"] - pd.Timedelta(milliseconds=interval_ms - 1)
        else:
            _convert_timestamp_series(df, "open_time")

    elif has_timestamp:
        # Legacy schema: 'timestamp' treated as close_time
        warnings.warn(
            f"Legacy schema detected in {path}: 'timestamp' column found. "
            "Treating as close_time. Consider re-downloading with canonical schema.",
            UserWarning,
            stacklevel=2,
        )
        rename_map[columns_lower["timestamp"]] = "close_time"
        df = df.rename(columns=rename_map)

        for col in required_ohlcv:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        _convert_timestamp_series(df, "close_time")

        # Derive open_time if interval provided
        if interval is not None:
            interval_ms = _interval_to_ms(interval)
            df["open_time"] = df["close_time"] - pd.Timedelta(milliseconds=interval_ms - 1)
        else:
            df["open_time"] = pd.NaT

    elif has_open_time:
        # Legacy schema: only open_time, need to derive close_time
        if interval is None:
            raise ValueError(
                f"CSV {path} has only 'open_time' column. "
                "The 'interval' parameter is required to derive close_time. "
                "Pass interval='4h' or similar, or re-download with canonical schema."
            )

        warnings.warn(
            f"Legacy schema detected in {path}: only 'open_time' column found. "
            f"Deriving close_time using interval={interval}.",
            UserWarning,
            stacklevel=2,
        )

        rename_map[columns_lower["open_time"]] = "open_time"
        df = df.rename(columns=rename_map)

        for col in required_ohlcv:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        _convert_timestamp_series(df, "open_time")

        # Derive close_time: open_time + interval - 1ms (since close_time is end of candle)
        interval_ms = _interval_to_ms(interval)
        df["close_time"] = df["open_time"] + pd.Timedelta(milliseconds=interval_ms - 1)

    else:
        raise ValueError(
            f"CSV {path} must contain at least one of: close_time, timestamp, or open_time. "
            f"Found columns: {list(df.columns)}"
        )

    # Drop rows with missing OHLCV data
    df = df.dropna(subset=required_ohlcv)

    # Filter rows with valid close_time
    df = df[df["close_time"].notna()].copy()

    # Set index to close_time (bar-close convention for replay)
    df = df.set_index("close_time").sort_index()

    # Return standardized columns: open_time + OHLCV
    result = df[["open_time", "open", "high", "low", "close", "volume"]]
    return result


def load_symbol_interval(data_path: str | Path, symbol: str, interval: str) -> pd.DataFrame:
    """Load OHLCV data for a symbol and interval.

    Args:
        data_path: Directory containing market data CSVs
        symbol: Trading symbol (e.g., 'BTCUSDT')
        interval: Candle interval (e.g., '4h', '1d')

    Returns:
        DataFrame with OHLCV data indexed by close_time
    """
    filename = f"{symbol}_{interval}.csv"
    path = Path(data_path) / filename
    return load_ohlcv_csv(path, interval=interval)


def load_funding_csv(symbol: str, data_path: str | Path = "./data") -> pd.DataFrame:
    """Load funding rate history from CSV.

    The CSV should have columns: timestamp, symbol, funding_rate, mark_price
    - timestamp: milliseconds since epoch (funding timestamp)
    - funding_rate: funding rate as decimal (e.g., 0.0001 = 0.01%)
    - mark_price: mark price at funding time

    Returns empty DataFrame if file not found.
    """
    funding_dir = Path(data_path) / "funding"
    path = funding_dir / f"{symbol}.csv"

    if not path.exists():
        return pd.DataFrame(columns=["timestamp", "symbol", "funding_rate", "mark_price"])  # type: ignore[call-overload]

    df = pd.read_csv(path)
    columns = {c.lower(): c for c in df.columns}

    # Required columns
    required = ["funding_rate"]
    ts_key = "timestamp" if "timestamp" in columns else "open_time"

    if ts_key not in columns or not all(col in columns for col in required):
        return pd.DataFrame(columns=["timestamp", "symbol", "funding_rate", "mark_price"])  # type: ignore[call-overload]

    rename_map: dict[str, str] = {columns[ts_key]: "timestamp"}
    if "symbol" in columns:
        rename_map[columns["symbol"]] = "symbol"
    if "funding_rate" in columns:
        rename_map[columns["funding_rate"]] = "funding_rate"
    if "mark_price" in columns:
        rename_map[columns["mark_price"]] = "mark_price"

    df = df.rename(columns=rename_map)

    # Ensure numeric columns
    for col in ["funding_rate", "mark_price"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Handle timestamp conversion
    if "timestamp" in df.columns:
        _convert_timestamp_series(df, "timestamp")

    df = df.dropna(subset=["funding_rate"])
    df = df.set_index("timestamp").sort_index()

    # Ensure required columns exist
    for col in ["symbol", "funding_rate", "mark_price"]:
        if col not in df.columns:
            df[col] = None

    result = df[["symbol", "funding_rate", "mark_price"]]
    return result  # type: ignore[return-value]


def load_spread_csv(symbol: str, data_path: str | Path = "./data") -> pd.DataFrame:
    """Load historical spread data from CSV.

    The CSV should have columns: timestamp, symbol, bid, ask
    Optional columns: bid_qty, ask_qty

    Args:
        symbol: Trading symbol (e.g., 'BTCUSDT')
        data_path: Base data directory containing spreads/ subdirectory

    Returns:
        DataFrame indexed by timestamp (datetime, UTC) with columns:
        - symbol: Trading symbol
        - bid: Best bid price
        - ask: Best ask price
        - bid_qty: Quantity at best bid (optional)
        - ask_qty: Quantity at best ask (optional)

        Returns empty DataFrame if file not found.
    """
    spread_dir = Path(data_path) / "spreads"
    path = spread_dir / f"{symbol}_spreads.csv"

    if not path.exists():
        return pd.DataFrame(columns=["timestamp", "symbol", "bid", "ask"])  # type: ignore[call-overload]

    df = pd.read_csv(path)
    columns = {c.lower(): c for c in df.columns}

    # Required columns
    required = ["bid", "ask"]
    ts_key = "timestamp" if "timestamp" in columns else None

    if ts_key is None or not all(col in columns for col in required):
        return pd.DataFrame(columns=["timestamp", "symbol", "bid", "ask"])  # type: ignore[call-overload]

    # Build rename map for standardization
    rename_map: dict[str, str] = {columns[ts_key]: "timestamp"}
    if "symbol" in columns:
        rename_map[columns["symbol"]] = "symbol"
    if "bid" in columns:
        rename_map[columns["bid"]] = "bid"
    if "ask" in columns:
        rename_map[columns["ask"]] = "ask"
    if "bid_qty" in columns:
        rename_map[columns["bid_qty"]] = "bid_qty"
    if "ask_qty" in columns:
        rename_map[columns["ask_qty"]] = "ask_qty"

    df = df.rename(columns=rename_map)

    # Ensure numeric columns
    for col in ["bid", "ask", "bid_qty", "ask_qty"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Handle timestamp conversion
    if "timestamp" in df.columns:
        _convert_timestamp_series(df, "timestamp")

    df = df.dropna(subset=["bid", "ask"])
    df = df.set_index("timestamp").sort_index()

    # Ensure required columns exist
    if "symbol" not in df.columns:
        df["symbol"] = symbol

    # Return standardized columns
    output_cols = ["symbol", "bid", "ask"]
    if "bid_qty" in df.columns:
        output_cols.append("bid_qty")
    if "ask_qty" in df.columns:
        output_cols.append("ask_qty")

    result = df[output_cols]
    return result  # type: ignore[return-value]
