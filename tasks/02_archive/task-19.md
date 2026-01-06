# Task 19 — Paper Mode Must Simulate Fills and Misses (Execution Realism)

## Goal
Make `paper` mode behave like a plausible execution simulator so it predicts live outcomes (fill rate, slippage, fees) instead of optimistic fantasies.

## Why
Right now “paper” effectively assumes fills at the desired price. This will overestimate profitability and hide execution problems (spread, non-fills, partial fills).

## Deliverables
- Add a lightweight execution simulator using free data:
  - use `bookTicker` for spread
  - model slippage as a function of spread + volatility (simple is fine)
  - model limit fills (e.g., fill if price trades through; otherwise remain open / expire per Task 13 policy)
- Apply maker/taker fees (configurable) + funding (if enabled).
- Log simulated fills exactly like real fills (same ledger events).

## Acceptance Criteria
- Paper mode can produce “no fill” outcomes and order lifecycles identical to live.
- A strategy that “works” in paper is no longer trivially broken live due to execution.

