# Task 05 — Exits That Let Winners Run (Trailing Stop + Optional Partial TP)

## Goal
Replace the “fixed TP at 3*ATR” cap with exits that better fit trend-following.

## Why
Fixed take-profits often kill trend strategies: you harvest small gains and miss the few outsized moves that make the year.

## Deliverables
- Implement ATR-based trailing stop logic:
  - start trailing after `trailing_start_atr` in favor
  - trail at `trailing_distance_atr`
  - update/replace the stop order safely (cancel/replace reduce-only STOP)
- (Optional) Partial take profit:
  - take a small fraction at a conservative target
  - leave runner governed by trailing stop

## Acceptance Criteria
- Trailing stop parameters in `config.yaml` are actually used.
- Stop updates are visible in the ledger and orders CSV.
- Safety: if stop modification fails, trading pauses (manual review) rather than leaving exposure unprotected.

