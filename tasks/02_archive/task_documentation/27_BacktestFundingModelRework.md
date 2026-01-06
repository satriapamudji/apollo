# Task 27 — Backtest Funding Model Rework

## Summary
Reworked the backtester's funding cost model from a flawed per-bar cumulative accrual to a correct event-based discrete settlement system. The previous implementation had multiple bugs causing incorrect funding calculations and double-counting.

## Problems Fixed

### 1. Per-Bar Cumulative Overcount
Old code applied `calculate_funding_cost()` on EVERY bar using total `hrs_since_entry`, which grew cumulatively. A 24-hour position would accrue funding as if paying for 4+8+12+16+20+24 = 84 hours instead of 24 hours.

### 2. Double-Counting on Exits
Funding was calculated again in exit paths (stop/take-profit and signal exits), adding to previously accrued amounts.

### 3. Leverage Multiplier Bug
Old `funding.py:138` incorrectly multiplied by leverage: `position_notional * leverage * funding_rate * periods`. Perpetual funding is based on notional position size only, not leveraged notional.

### 4. No Position-Side Awareness
Didn't distinguish LONG/SHORT for payer/receiver direction. With positive funding rates, longs pay shorts; with negative rates, shorts pay longs.

## What Was Changed

### `src/backtester/funding.py` (Complete Rewrite)

**New Methods:**
- `iter_funding_events(start, end) -> Iterator[FundingRateInfo]` - Yields discrete funding settlement events in the half-open interval (start, end]
- `calculate_funding_cashflow(notional, rate, position_side) -> float` - Calculates funding with correct direction:
  - LONG + positive rate = pays (positive cashflow)
  - SHORT + positive rate = receives (negative cashflow)
  - LONG + negative rate = receives (negative cashflow)
  - SHORT + negative rate = pays (positive cashflow)
  - NO leverage multiplier applied

**New Internal Methods:**
- `_generate_synthetic_settlements()` - Generates standard 00:00/08:00/16:00 UTC settlement times for constant rate mode
- `_build_funding_index()` - Builds index from historical CSV with proper timezone handling

**Schema Changes:**
- `FundingRateInfo.timestamp` renamed to `FundingRateInfo.funding_time` for clarity
- All timestamps are timezone-aware (UTC)

### `src/backtester/engine.py` (Major Refactor)

**New State Tracking:**
- `last_funding_time: dict[str, datetime]` - Tracks last settlement per position
- `position_funding_accumulated: dict[str, float]` - Accumulates funding for trade-level reporting

**Removed:**
- All per-bar funding accrual code (old lines 150-200+)
- Funding calculation in `_close_position()` exit path

**Added:**
- Discrete funding application loop using `iter_funding_events()`
- Proper timezone handling for all datetime comparisons
- Funding applied at settlement times, updating equity immediately

**Trade Reporting:**
- `Trade.funding_cost` includes accumulated funding for the trade
- `Trade.net_pnl` = `gross_pnl - fees - funding_cost`
- `_close_position()` adds only `gross_pnl - fees` to equity (funding already applied at settlements)

### `tests/test_backtester_funding.py` (New File - 24 Tests)

**Test Classes:**
- `TestFundingCashflowDirection` - Verifies LONG/SHORT pay/receive logic with positive/negative rates
- `TestNoLeverageMultiplier` - Confirms no hidden leverage scaling
- `TestIterFundingEventsHistorical` - Half-open interval behavior, mark price inclusion
- `TestIterFundingEventsSynthetic` - Standard settlement times, multi-day spans
- `TestApplyOncePerSettlement` - Multiple bars don't trigger multiple applications
- `TestProviderModes` - Historical/constant/no-data mode detection
- `TestGetRate` - Convenience method behavior

## Design Decisions

### 1. Equity Updated at Settlement Times
Funding cashflows are deducted from equity when settlements occur, not averaged across bars. This provides realistic drawdown tracking.

### 2. Trade.net_pnl Includes Funding
Each trade records its total funding cost for accurate trade-level profitability analysis.

### 3. Half-Open Interval (start, end]
`iter_funding_events(start, end)` excludes start time, includes end time. This prevents double-counting across consecutive intervals.

### 4. Mark Price Fallback
When mark price is unavailable in funding data, the engine falls back to bar close price.

## Verification

- 24 new funding-specific tests pass
- 308 total tests pass (1 pre-existing failure in `test_data_schema.py` unrelated to this change)
- ruff and mypy clean on modified files

## Usage

No CLI changes. The funding model now correctly:

1. Applies funding only at 00:00/08:00/16:00 UTC settlement times
2. Uses correct position-side-aware pay/receive logic
3. Tracks funding separately per position
4. Reports accurate funding costs per trade
