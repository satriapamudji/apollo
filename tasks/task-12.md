# Task 12 — Operator API (State + Actions)

## Goal
Expose a minimal local API so an operator can inspect state and take safe actions without digging through files.

## Why
Operational clarity is profit. Fast diagnosis prevents bad manual interventions and reduces downtime.

## Deliverables
- Add a small FastAPI service (local-only by default):
  - `GET /health` (uptime, mode, trading enabled, last WS msg age)
  - `GET /state` (positions, open orders, equity, risk flags)
  - `GET /events?tail=N` (tail of ledger)
  - `POST /actions/ack-manual-review`
  - `POST /actions/kill-switch`
  - (Optional) `POST /actions/pause` / `resume`
- Wire it to the existing `StateManager` + `EventLedger` (no new source of truth).

## Acceptance Criteria
- Operator can identify “why trading is paused” in <60 seconds using the API alone.

