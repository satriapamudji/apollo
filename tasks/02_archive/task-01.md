# Task 01 — Backtest Reporting That Actually Answers “Is This Profitable?”

## Goal
Turn the backtest output from 4 numbers into a decision-grade report.

## Why
“Trades / total return / win rate / max DD” is insufficient to judge robustness. We need distributional stats and costs that dominate in crypto (fees + funding + slippage).

## Deliverables
- Extend `src/backtester/runner.py` to optionally output:
  - trade list CSV (timestamp, side, entry, exit, qty, pnl, holding time)
  - equity curve CSV (timestamp, equity, drawdown)
  - summary JSON (metrics)
- Add metrics at minimum:
  - expectancy per trade (avg win * win% − avg loss * loss%)
  - profit factor, avg R-multiple, max consecutive losses
  - monthly returns table
- Add CLI args: `--initial-equity`, `--fee-pct`, `--slippage-pct`, `--out-dir`

## Acceptance Criteria
- `backtest ... --out-dir data/backtests/<run-id>` creates the artifacts above.
- Report is deterministic (same inputs => identical outputs).

