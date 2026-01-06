# 02_HistoricalDataPipeline.md

## Task Summary
Task 02 — Historical Data Pipeline (Candles + Optional Funding)

## What Was Implemented
A CLI tool (`src/tools/download_klines.py`) for downloading Binance Futures klines for backtesting.

### Features
- Downloads OHLCV data from Binance Futures API (`/fapi/v1/klines`)
- Supports configurable symbol, interval, start/end dates
- Writes to `data/market/{symbol}_{interval}.csv` with single header row
- Conservative rate limiting (1000 req/min) with exponential backoff retries
- Batch processing (max 1500 klines per request) for large date ranges
- Optional funding rate history downloader (`--funding` flag)
- Progress tracking during download

### CLI Usage
```bash
# Download 4h candles for BTCUSDT
python -m src.tools.download_klines --symbol BTCUSDT --interval 4h --start 2024-01-01

# With funding rates
python -m src.tools.download_klines --symbol BTCUSDT --interval 4h --start 2024-01-01 --funding

# Custom output directory
python -m src.tools.download_klines --symbol BTCUSDT --interval 4h --start 2024-01-01 --output-dir ./data/market
```

### CSV Output Format
```
timestamp,open,high,low,close,volume
1754006400000,115697.30,116000.00,114239.00,115595.70,42604.847
...
```

## Changes Made During Verification
1. **Fixed mypy type errors** (`src/tools/download_klines.py`):
   - Added `cast` import from `typing`
   - Typed `params` dicts as `dict[str, str | int]` for both klines and funding endpoints
   - Cast `response.json()` return to `list[list[Any]]` for type correctness

## Verification Results
- ✅ `ruff check src/tools/download_klines.py` — All checks passed
- ✅ `mypy src/tools/download_klines.py` — No issues found
- ✅ `python -m src.tools.download_klines --help` — CLI works correctly
- ✅ CSV schema compatible with `src/backtester/data.py:load_ohlcv_csv`

## Files
- **Implementation**: `src/tools/download_klines.py`
- **Task spec**: `tasks/02_archive/task-02.md`
