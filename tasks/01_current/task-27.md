# Task 27 - Backtest Funding Model Rework (Event-Based, Correct Sign, No Leverage Multiplier)

## Goal
Make funding in the backtester **decision-grade** by applying funding as **discrete settlement cashflows** (event-based), with correct payer/receiver direction and without leverage-multiplying.

## Why
Current backtest funding logic materially misprices returns:
- Funding is applied **every bar** using *total hours since entry* (cumulative overcount).
- Funding is also applied again on exit paths (double-counting risk).
- Funding is multiplied by `leverage` (incorrect for perp funding cashflow; leverage affects margin, not notional exposure).
- Funding direction is not conditioned on position side (LONG/SHORT).

If funding is wrong, any multi-day hold strategy has unreliable PnL and risk stats.

## Current Problem (Observed)
- `src/backtester/engine.py` applies `calculate_funding_cost(...)` each bar using `hrs_since_entry` and also passes funding into `_close_position(...)` (risk of repeated deduction).
- `src/backtester/funding.py` hardcodes 8h cadence and uses `position_notional * leverage * rate * periods`.

## Deliverables

### 1) Replace “hours held” accrual with discrete settlement events
Implement funding as cashflow **only** at settlement timestamps:
- Use historical funding CSV rows as the authoritative settlement schedule (no “assume 8h” in historical mode).
- Track `last_funding_time_applied` per open position so each settlement is applied once.

Recommended approach (minimal refactor, compatible with current engine loop):
- In `FundingRateProvider`, add an iterator:
  - `iter_funding_events(start, end) -> Iterable[FundingRateInfo]` yielding settlement times in `(start, end]`.
- In the bar loop, on each bar close:
  - apply all funding events whose `funding_time <= bar_close_time` and `> last_funding_time_applied`.

### 2) Correct payer/receiver direction and remove leverage multiplier
At settlement time `t`:
- `notional_t = abs(qty) * mark_price_t` (prefer mark price at `t`; fallback to bar close if mark unavailable)
- If `funding_rate > 0`: LONG pays, SHORT receives.
- If `funding_rate < 0`: SHORT pays, LONG receives.

Canonical cashflow:
- LONG: `cashflow = notional_t * funding_rate`
- SHORT: `cashflow = -notional_t * funding_rate`
- Equity update: `equity -= cashflow` (positive cashflow means you paid)

**No leverage multiplier**.

### 3) Remove double-counting paths
Decide on one accounting approach and enforce it consistently:
- Preferred: treat funding purely as equity cashflows at settlement times; trade close PnL should not subtract funding again.
- Still record `Trade.funding_cost` for reporting, but it must equal the **sum of applied settlement cashflows during the holding period** (not recomputed from duration).

### 4) Make funding cadence “data-driven”
- If historical funding data is present: cadence is whatever the CSV contains (supports cadence changes).
- If `constant_funding_rate` mode is used: keep a documented approximation (default 8h schedule at 00/08/16 UTC), and generate synthetic settlement times deterministically.

### 5) Tests and invariants
Add focused unit tests for funding:
- LONG + positive rate reduces equity; SHORT increases equity by the same magnitude.
- Negative rate flips the payer/receiver.
- Funding applied exactly once per settlement even if multiple bars occur after settlement.
- No leverage scaling.
- Historical “irregular” settlement schedule (e.g., two consecutive hourly events) is handled.

## Acceptance Criteria
- Backtest funding is applied only at settlement timestamps; no per-bar “duration accrual”.
- `total_funding_paid` equals the sum of settlement cashflows applied (and matches trade-level totals).
- No path applies funding twice (equity curve and trade PnL are consistent).
- Unit tests cover sign, cadence, and “apply-once” behavior.

## Files to Modify
- `src/backtester/funding.py`
- `src/backtester/engine.py`
- `src/backtester/reporting.py` (if needed for metrics display)
- `tests/` (new tests for funding behavior)

## Notes
- Use mark price where possible for notional-at-settlement; if not available, document the fallback and record it in backtest metadata.
- Keep the implementation deterministic and seed-independent (funding should never depend on RNG).

