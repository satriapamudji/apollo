# Task 07 — Execution Guardrails: Spread/Slippage Enforcement

## Goal
Actually enforce `execution.max_spread_pct` and `execution.max_slippage_pct` before placing orders.

## Why
Most blown-up crypto bots die to microstructure: wide spreads, thin books, and surprise slippage during volatility spikes.

## Deliverables
- Add pre-trade checks in `src/execution/engine.py`:
  - fetch best bid/ask (or book ticker) and compute spread %
  - reject entries when spread > `max_spread_pct`
  - enforce a max deviation between expected entry price and current mark/last price (`max_slippage_pct`)
- Log *why* a trade was blocked (and emit a ledger event for auditability).

## Acceptance Criteria
- Entries are skipped (not “tried and failed”) when spread/slippage constraints are violated.
- `logs/thinking.jsonl` and the ledger include the rejection reason.

