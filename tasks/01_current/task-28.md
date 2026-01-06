# Task 28 - Candle Timestamp Contract + Data Normalization/Validation

## Goal
Make historical bar data unambiguous and consistent by enforcing a canonical schema with **both `open_time` and `close_time`**, and by updating loaders/tools to prevent accidental time shifts and lookahead.

## Why
The backtester currently mixes timestamp semantics:
- `src/backtester/data.py` treats `timestamp` as **candle close time**, but will fall back to `open_time` if `timestamp` is missing.
- `data/market/BTCUSDT_4h.csv` uses `open_time`, while `data/market/ETHUSDT_4h.csv` uses `timestamp` (likely close-time-like).

This can silently shift bars by an entire interval (e.g., 4h) and corrupt daily resampling, signal timing, and stop/exit behavior.

## Deliverables

### 1) Define a canonical bar schema (CSV-first, dataset-friendly)
Minimum required columns:
- `open_time` (ms since epoch, UTC)
- `close_time` (ms since epoch, UTC)
- `open`, `high`, `low`, `close`, `volume`

Optional columns (allowed but not required):
- `trades`, `taker_buy_base`, `taker_buy_quote`, `quote_volume`

Replay contract:
- Event time for bar replay is `close_time` (bar-close convention).

### 2) Update the kline downloader to emit the canonical schema
Update `src/tools/download_klines.py` to write both:
- `open_time = kline[0]`
- `close_time = kline[6]` (Binance kline close time)

Do not write ambiguous `timestamp` in newly generated files.

### 3) Update the loader to be strict-by-default and safe with legacy files
Update `src/backtester/data.py`:
- Prefer `close_time` as the index for replay.
- Support legacy inputs:
  - If only `timestamp` exists: treat as close time and map to `close_time`.
  - If only `open_time` exists: require the caller to provide `interval` so `close_time` can be derived (`open_time + interval_ms`), or fail fast with a clear error.
- Always retain both `open_time` and `close_time` columns in the returned DataFrame (even if index is `close_time`).

Update `load_symbol_interval(...)` to pass `interval` into the loader so legacy `open_time` files can be normalized deterministically.

### 4) Add a non-destructive normalization/validation tool
Create a tool that:
- Validates required columns and monotonic time ordering.
- Detects duplicates, missing intervals, and inconsistent spacing.
- Outputs a normalized copy (do not overwrite originals) to a target folder, e.g.:
  - `data/market_normalized/<symbol>_<interval>.csv`

### 5) Daily resampling: explicitly avoid lookahead
Verify/adjust daily aggregation to ensure the “daily bar” used at a 4h timestamp only includes data up to that timestamp (bar-close aligned).

## Acceptance Criteria
- New downloads contain `open_time` and `close_time` columns and load without warnings.
- Loader refuses ambiguous legacy schemas unless it can deterministically infer `close_time`.
- Running a backtest with `data/market/BTCUSDT_4h.csv` no longer suffers a 4h timestamp shift.
- Validator flags any non-monotonic or inconsistent time series.

## Files to Modify
- `src/tools/download_klines.py`
- `src/backtester/data.py`
- `src/backtester/engine.py` (only if daily slicing contract needs adjustment)
- `src/tools/` (new validator/normalizer tool)
- `tests/` (schema + timestamp contract tests)

## Notes
- Keep everything UTC and timezone-aware.
- Never silently reinterpret a timestamp column without emitting an explicit warning or failing fast.

