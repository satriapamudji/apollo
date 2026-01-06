# Task 02 — Historical Data Pipeline (Candles + Optional Funding)

## Goal
Create a reproducible way to build the datasets the backtester needs (and keep them updated).

## Why
Right now the repo has a couple CSVs. That’s not enough for strategy validation or regression testing.

## Deliverables
- Add a CLI tool (e.g. `python -m src.tools.download_klines`) that:
  - downloads Binance Futures klines for `{symbol, interval, start, end}`
  - writes to `data/market/{symbol}_{interval}.csv` with a single header row
  - enforces rate limits and retries safely
- (Optional) Funding history downloader to `data/funding/{symbol}.csv` if/when the strategy starts modeling funding cost.

## Acceptance Criteria
- Tool can backfill a full year of 4h candles without manual babysitting.
- CSV schema matches `src/backtester/data.py:load_ohlcv_csv` expectations.

