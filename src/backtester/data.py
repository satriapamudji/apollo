"""Historical data loading utilities."""

from __future__ import annotations

from pathlib import Path

import pandas as pd


def load_ohlcv_csv(path: str | Path) -> pd.DataFrame:
    """Load OHLCV data from CSV into a normalized DataFrame.

    The timestamp column is interpreted as the candle CLOSE time (not open time),
    consistent with live trading conventions. The backtester filters this data
    during iteration to prevent lookahead bias.
    """
    df = pd.read_csv(path)
    columns = {c.lower(): c for c in df.columns}
    required = ["open", "high", "low", "close", "volume"]
    ts_key = "timestamp" if "timestamp" in columns else "open_time"
    if ts_key not in columns or not all(col in columns for col in required):
        raise ValueError(
            "CSV must contain timestamp/open_time, open, high, low, close, volume columns"
        )
    rename_map = {columns[ts_key]: "timestamp"}
    rename_map.update({columns[c]: c for c in required})
    df = df.rename(columns=rename_map)
    for col in required:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    ts = df["timestamp"]
    numeric_ts = pd.to_numeric(ts, errors="coerce")
    if numeric_ts.notna().any():
        valid = numeric_ts.notna()
        df = df.loc[valid].copy()
        numeric_ts = numeric_ts[valid]
        unit = "ms" if numeric_ts.max() > 1_000_000_000_000 else "s"
        df["timestamp"] = pd.to_datetime(numeric_ts, unit=unit, utc=True)
    else:
        df["timestamp"] = pd.to_datetime(ts, utc=True)
    df = df.dropna(subset=required)
    df = df.set_index("timestamp").sort_index()
    return df[required]


def load_symbol_interval(data_path: str | Path, symbol: str, interval: str) -> pd.DataFrame:
    filename = f"{symbol}_{interval}.csv"
    path = Path(data_path) / filename
    return load_ohlcv_csv(path)


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
        return pd.DataFrame(columns=["timestamp", "symbol", "funding_rate", "mark_price"])

    df = pd.read_csv(path)
    columns = {c.lower(): c for c in df.columns}

    # Required columns
    required = ["funding_rate"]
    ts_key = "timestamp" if "timestamp" in columns else "open_time"

    if ts_key not in columns or not all(col in columns for col in required):
        return pd.DataFrame(columns=["timestamp", "symbol", "funding_rate", "mark_price"])

    rename_map = {columns[ts_key]: "timestamp"}
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
        ts = df["timestamp"]
        numeric_ts = pd.to_numeric(ts, errors="coerce")
        if numeric_ts.notna().any():
            valid = numeric_ts.notna()
            df = df.loc[valid].copy()
            numeric_ts = numeric_ts[valid]
            unit = "ms" if numeric_ts.max() > 1_000_000_000_000 else "s"
            df["timestamp"] = pd.to_datetime(numeric_ts, unit=unit, utc=True)
        else:
            df["timestamp"] = pd.to_datetime(ts, utc=True)

    df = df.dropna(subset=["funding_rate"])
    df = df.set_index("timestamp").sort_index()

    # Ensure required columns exist
    for col in ["symbol", "funding_rate", "mark_price"]:
        if col not in df.columns:
            df[col] = None

    return df[["symbol", "funding_rate", "mark_price"]]
