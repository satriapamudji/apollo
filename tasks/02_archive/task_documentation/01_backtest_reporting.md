# 01_backtest_reporting.md

## Task Summary

**Task:** Backtest Reporting That Actually Answers "Is This Profitable?"

**Original Goal:** Turn the backtest output from 4 numbers into a decision-grade report with distributional stats and costs that dominate in crypto (fees + funding + slippage).

## What Was Already Implemented

The core functionality was already present in the codebase:

### CLI Arguments (`src/backtester/runner.py`)
- `--initial-equity` - Starting equity for backtest
- `--fee-pct` - Trading fee percentage
- `--slippage-pct` - Slippage percentage for ideal execution
- `--out-dir` - Output directory for report artifacts

### Data Structures (`src/backtester/engine.py`)
- `Trade` dataclass with all required fields (timestamp, side, entry, exit, qty, pnl, holding time)
- `EquityPoint` dataclass (timestamp, equity, drawdown)
- `BacktestResult` dataclass with execution metrics

### Reporting Module (`src/backtester/reporting.py`)
- `compute_metrics()` - Calculates expectancy, profit factor, avg R-multiple, max consecutive losses, monthly returns
- `write_trade_csv()` - Writes trade list to CSV
- `write_equity_csv()` - Writes equity curve to CSV
- `write_summary_json()` - Writes summary metrics to JSON
- `generate_report()` - Generates all report artifacts
- `print_summary()` - Console output

## What Was Added

**Test coverage for the reporting module** (`tests/test_backtester_reporting.py`)

Added 26 tests covering:

1. **compute_metrics() tests** (10 tests):
   - Empty trades, all winning, all losing
   - Expectancy calculation formula verification
   - Profit factor, avg R-multiple, max consecutive losses
   - Monthly returns aggregation
   - Avg/largest win/loss calculations

2. **write_trade_csv() tests** (3 tests):
   - CSV header field verification
   - Trade data correctness
   - Timestamp ISO format

3. **write_equity_csv() tests** (3 tests):
   - CSV header field verification
   - Equity point data correctness
   - Multiple equity points

4. **write_summary_json() tests** (2 tests):
   - Valid JSON output
   - All metrics present

5. **generate_report() tests** (3 tests):
   - Output directory creation
   - All files generated
   - Deterministic report verification

6. **Edge case tests** (5 tests):
   - Single trade (win/lose)
   - Profit factor infinity/zero cases
   - Empty monthly returns

## Changes Made

### Created Files
- `tests/test_backtester_reporting.py` - 26 comprehensive tests for reporting module

### Modified Files
- None (all functionality was already implemented)

## Verification

- **Linting:** `ruff check tests/test_backtester_reporting.py` - All checks pass
- **Tests:** `pytest tests/test_backtester_reporting.py -v` - 26/26 pass
- **Full suite:** `pytest tests/` - 47/47 pass (excluding 2 pre-existing test failures)
- **CLI test:**
  ```bash
  backtest --symbol BTCUSDT --out-dir data/backtests/test-run
  ```
  Generates:
  - `trades.csv` (10 trades with all required fields)
  - `equity.csv` (12 equity points)
  - `summary.json` (comprehensive metrics)

## Acceptance Criteria Status

| Criteria | Status |
|----------|--------|
| `backtest ... --out-dir data/backtests/<run-id>` creates artifacts | ✅ |
| Trade list CSV (timestamp, side, entry, exit, qty, pnl, holding time) | ✅ |
| Equity curve CSV (timestamp, equity, drawdown) | ✅ |
| Summary JSON (metrics) | ✅ |
| Expectancy per trade | ✅ |
| Profit factor | ✅ |
| Avg R-multiple | ✅ |
| Max consecutive losses | ✅ |
| Monthly returns table | ✅ |
| Report is deterministic (same inputs => identical outputs) | ✅ |
| CLI args: --initial-equity, --fee-pct, --slippage-pct, --out-dir | ✅ |

## Notes

- The `generated_at` timestamp in `summary.json` is intentionally excluded from determinism checks as it naturally differs between runs
- Pre-existing test failures in `test_backtester_execution.py` (2 tests) are unrelated to this task
