# Task 08 — Persist Pending Entries (Restart-Safe Protective Orders)

## Goal
Guarantee that every filled entry ends up protected (SL/TP or trailing stop) even across process restarts.

## Why
Right now `_pending_entries` is in-memory. If the bot restarts between `OrderPlaced` and fill, a later fill may not trigger protective order placement.

## Deliverables
- Persist a minimal “pending entry context” to `data/state/` and/or the event ledger:
  - client order id -> {trade_id, symbol, side, intended stop/tp, tick size}
- On startup, recover:
  - open orders from Binance
  - recent ledger events
  - and rebuild the pending map
- When an entry fill is observed (REST poll or user stream), finalize and place protection.

## Acceptance Criteria
- Kill the bot mid-entry; restart; if the order filled while down, the bot either:
  - places protection immediately, or
  - triggers manual review with a precise, actionable reason.

