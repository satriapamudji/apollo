# Task 33 - Spread Proxy Dataset + Spread-Aware Execution Model

## Goal
Add a spread time series (or conservative spread model) and integrate it into backtest execution so:
- entries are rejected when spread exceeds `execution.max_spread_pct`,
- slippage has a realistic floor of half-spread,
- results record spread-at-entry diagnostics.

## Why
`config.yaml` defines `execution.max_spread_pct`, but the backtester currently has no spread concept, which makes “realistic execution” incomplete. Spread blowouts during volatility regimes can erase expected edge.

## Deliverables

### 1) Define a SpreadEvent contract
Minimum fields:
- `timestamp` (UTC)
- `symbol`
- `bid`, `ask`
- optional: `bid_qty`, `ask_qty`

Derived:
- `mid = (bid + ask)/2`
- `spread_pct = (ask - bid) / mid * 100`

### 2) Add a spread collector (forward collection) OR a deterministic spread model (historical backtests)
Choose one (or implement both with a switch):
- Collector: periodically sample `GET /fapi/v1/ticker/bookTicker` for selected symbols and store as dataset artifact.
- Model: if spread data is unavailable, use a conservative deterministic model (e.g., spread floor + ATR-scaled spread) and record assumptions in the dataset manifest.

### 3) Integrate spread gating and slippage floor into the execution model
Execution logic for entries:
- If `spread_pct > execution.max_spread_pct`: reject entry (count + log).
- Slippage should be at least half-spread (plus any additional model slippage).

### 4) Fix execution direction inference
`src/backtester/execution_sim.py` currently infers buy vs sell from `proposal_price >= current_price`. This fails for buy limits below market (common) and can flip slippage direction.

Update the simulator to take an explicit `side` (BUY/SELL or LONG/SHORT) and compute adverse slippage correctly.

### 5) Reporting + metrics
Record:
- `avg_spread_at_entry_pct`
- `spread_rejections`
- distribution of `spread_pct` at entry

## Acceptance Criteria
- Backtest enforces `execution.max_spread_pct` when spread data/model is enabled.
- Slippage direction is correct for buy vs sell limits (unit tested).
- Output artifacts include spread-at-entry diagnostics and rejection counts.

## Files to Modify
- `src/backtester/execution_sim.py`
- `src/backtester/engine.py` or new execution model modules (if Task 31 introduces interfaces)
- `src/tools/` (new spread collector/model tool)
- `src/backtester/reporting.py`
- `tests/` (spread gating + slippage direction tests)

## Notes
- If historical spread snapshots are unavailable, the conservative model must be explicitly documented and never presented as “real spreads”.
- This work aligns with (and partially supersedes) archived `tasks/02_archive/task-26.md` for live spread checks; keep naming consistent across live/backtest where possible.

