# Task 32 - Strategy Packaging System (strategies/<name>/strategy.md) + Safe Overrides

## Goal
Introduce a strategy packaging system so each strategy declares:
- required data inputs (series + intervals),
- typed parameters (defaults + constraints),
- signal logic description,
- risk and execution assumptions.

`config.yaml` should reference the strategy by name and apply overrides safely (validated).

## Why
As soon as we have multiple strategies, “parameters in YAML” alone becomes unmaintainable:
- no explicit input requirements -> silent missing-data failures
- no constraints -> invalid configs slip through
- no reproducible strategy spec -> hard to compare runs over time

## Deliverables

### 1) Define a machine-readable strategy.md schema
Use Markdown with a YAML front matter block, for example:
```md
---
name: trend_following_v1
version: 1
requires:
  bars:
    - series: trade
      interval: 4h
  derived:
    - interval: 1d
      from: 4h
  funding: true
parameters:
  ema_fast: {type: int, default: 8, min: 2, max: 50}
  ema_slow: {type: int, default: 21, min: 5, max: 100}
  atr_period: {type: int, default: 14, min: 5, max: 50}
  entry_style: {type: str, default: breakout, enum: [breakout, pullback]}
assumptions:
  bar_time: close_time
  stop_model: intrabar_high_low
  funding_model: discrete_settlement_events
---
```

### 2) Implement a loader + validator
Implement:
- loading `strategies/<name>/strategy.md`
- validating required datasets are present before a run starts
- validating overrides from `config.yaml`:
  - reject unknown keys
  - enforce types and min/max/enum constraints

### 3) Provide an example package for trend_following_v1
Create:
- `strategies/trend_following_v1/strategy.md`

This should reflect existing YAML fields:
- EMA/ATR periods
- breakout entry constraints
- trailing/time stops
- scoring factor weights
- risk assumptions (max_positions=1, leverage bounds)

### 4) Record strategy spec hash in backtest runs
Backtest output (and optional ledger) should store:
- strategy name + version
- hash of `strategy.md`
- final resolved parameter set

## Acceptance Criteria
- Backtest refuses to run if required inputs are missing from the dataset (clear error).
- Overrides are type-checked and constrained (invalid overrides fail fast).
- `trend_following_v1` has a complete `strategy.md` package and can run end-to-end.

## Files to Modify
- `strategies/` (new folder + strategy.md files)
- `src/strategy/` (optional shared validation utilities)
- `src/backtester/runner.py` and/or new backtest loader modules
- `src/config/settings.py` (only if needed to map overrides)
- `tests/` (strategy spec parsing + override validation tests)

## Notes
- Keep the first schema minimal and extensible; avoid overfitting to one strategy.
- This packaging system should work for both backtest and live (event-driven) runtimes.

