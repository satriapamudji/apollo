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
        raise ValueError("CSV must contain timestamp/open_time, open, high, low, close, volume columns")
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
