# Task 10 — Continuous Reconciliation Loop (Not Just at Startup)

## Goal
Run reconciliation regularly and integrate it into monitoring/alerting.

## Why
Exchange state changes while the bot is running (manual trades, partial fills, order cancels). Reconciliation is your “truth sync”.

## Deliverables
- Add a reconciliation loop in `src/main.py` that runs every N minutes (configurable).
- Update metrics:
  - `last_reconciliation_age_hr`
  - discrepancy counters by type
- On repeated reconciliation failures, trigger manual review and alert.

## Acceptance Criteria
- Reconciliation runs continuously in `testnet`/`live` without blocking the strategy loop.
- Discrepancies are visible in both Prometheus metrics and the event ledger.

