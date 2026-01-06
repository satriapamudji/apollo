# Task 31 - Event-Driven Multi-Symbol Backtester Core (Unified Event Stream)

## Goal
Refactor the backtester into an event-driven replay engine that supports multi-symbol portfolios by consuming a single deterministic, time-ordered event stream:
- `CANDLE_CLOSE` (BarEvent) at bar close time
- `FUNDING_UPDATE` (FundingEvent) at settlement time
- (optional) `SPREAD_SNAPSHOT` (SpreadEvent)
- (optional) `UNIVERSE_UPDATED` (UniverseEvent)

## Why
The current backtester is a single-symbol bar loop. It can “work” but does not scale and diverges from the live system’s event sourcing architecture (`src/ledger/*`). Multi-symbol backtests require a unified event stream to avoid hidden ordering bugs and lookahead.

## Deliverables

### 1) Event model and deterministic ordering
Implement a stable ordering for events that share the same timestamp (example priority):
1) funding events
2) bar close events
3) spread events
4) strategy decisions -> proposals
5) risk gating
6) execution simulation

Tie-break deterministically by `(timestamp, priority, symbol, interval/series, sequence)`.

### 2) DatasetReader + EventMux (heap merge)
Implement:
- `DatasetReader`: yields per-symbol iterators for each event series.
- `EventMux`: merges iterators into one ordered stream (heap merge).

### 3) StrategyRunner interface and adapter for current strategy
Define a strategy runtime interface that:
- consumes events
- maintains indicator state
- emits `TradeProposed` decisions with scores and stops

Adapt current `SignalGenerator`/`RiskEngine` usage to this interface.

### 4) Portfolio selection with max_positions support
Implement a portfolio selector that:
- can run multi-symbol streams
- chooses top-K proposals at each decision point
- supports `risk.max_positions=1` (current default) while being future-proof for K>1.

### 5) ExecutionModel interface (ideal + realistic)
Standardize execution models behind an interface so later tasks can add spread-aware and microstructure-aware models without rewriting the engine.

### 6) Optional: backtest event ledger
Emit a backtest event log (JSONL) similar to live `EventLedger` so a run can be replayed/debugged with full causality:
- dataset id
- strategy config hash
- RNG seed
- every proposal/approval/fill/position update

## Acceptance Criteria
- Multi-symbol backtest runs deterministically given the same dataset + seed.
- Event ordering is explicit and covered by tests (EventMux tie-break rules).
- `risk.max_positions=1` is enforced correctly even when multiple symbols signal simultaneously.
- Existing single-symbol CLI remains available (either as a wrapper or compatibility mode).

## Files to Modify
- `src/backtester/` (new replay engine modules)
- `src/backtester/runner.py`
- `src/backtester/engine.py` (may be replaced or wrapped)
- `src/ledger/` (optional reuse for logging; keep changes minimal)
- `tests/` (EventMux ordering + determinism tests)

## Notes
- Keep the first implementation “bar close only” (no intrabar events) to preserve current strategy assumptions, then iterate.
- Record all deterministic inputs (dataset id, config hash, seed) so results are reproducible and auditable.

