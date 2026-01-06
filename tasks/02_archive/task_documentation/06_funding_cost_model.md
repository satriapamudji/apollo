# Task 06 — Funding Cost Model Implementation

## Summary
Implemented explicit funding cost tracking for both live trading and backtesting, making funding an explicit part of the strategy and PnL rather than a hand-waved afterthought.

## What Was Changed

### New Files
- **`src/backtester/funding.py`** - New `FundingRateProvider` class for:
  - Historical funding rate lookup from CSV
  - Constant funding rate mode for stress testing
  - Funding cost calculation helpers

### Modified Files

#### `src/backtester/data.py`
- Added `load_funding_csv()` function to load historical funding data from CSV
- Handles missing files gracefully (returns empty DataFrame)
- Parses timestamp, symbol, funding_rate, mark_price columns

#### `src/backtester/engine.py`
- Added `funding_cost` field to `Trade` dataclass
- Added `total_funding_paid` and `pnl_with_funding` fields to `BacktestResult`
- Modified `Backtester.run()` to accept `funding_data` and `constant_funding_rate` parameters
- Added funding tracking during position holding (every bar calculates funding accrual)
- Updated `_close_position()` to include funding cost in net PnL

#### `src/backtester/reporting.py`
- Added funding metrics to `compute_metrics()`:
  - `total_funding_paid`
  - `pnl_with_funding`
  - `pnl_without_funding`
- Added `funding_cost` and `pnl_without_funding` columns to `trades.csv` export
- Updated `print_summary()` to show funding breakdown section

#### `src/backtester/runner.py`
- Added `--funding-data` CLI argument for funding CSV directory
- Added `--constant-funding-rate` CLI argument for stress testing
- Loads funding data and passes to backtester

#### `src/strategy/universe.py`
- Added `emit_funding_update()` method to `UniverseSelector` class
- Emits `FUNDING_UPDATE` events with estimated carry for different position sizes

#### `src/monitoring/thinking_log.py`
- Updated `log_signal()` to include:
  - `funding_rate_pct`
  - `funding_currency` (long_pays_short/short_pays_long/neutral)
  - `estimated_funding_carry` (per 8h and daily for 1k/10k/100k positions)
- Updated `log_risk()` with same funding fields

## Features

### Live Mode
- `FUNDING_UPDATE` events emitted on 8-hour schedule
- Thinking log includes estimated funding carry for position sizing decisions
- Live ledger can reconstruct "what funding was assumed when this trade was taken"

### Backtest Mode
- Loads historical funding from CSV (optional)
- Supports constant funding rate for stress testing
- Tracks funding costs during position holding periods
- Report shows PnL with and without funding

## Usage

### Backtest with historical funding:
```bash
backtest --symbol BTCUSDT --funding-data ./data
```

### Backtest with constant funding rate (stress test):
```bash
backtest --symbol BTCUSDT --constant-funding-rate 0.0001
```

### Report Output:
```
FUNDING BREAKDOWN
  PnL (no funding):   $X,XXX.XX
  Total Funding Paid: ($XXX.XX)
  PnL (with funding): $X,XXX.XX
```
