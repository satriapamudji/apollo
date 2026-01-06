# Task 09 — Protective Order Watchdog (Auto-Remediate or Pause)

## Goal
Continuously verify that every open position has the expected protective orders on-exchange.

## Why
Even with “place after fill”, orders can disappear (manual cancel, exchange reject, connectivity issues). This is the #1 operational risk.

## Deliverables
- Add a periodic watchdog loop:
  - for each open position, check SL/TP (or trailing stop) exists and is reduce-only
  - if missing: either auto-replace (config-driven) or require manual review
- Emit clear ledger events: `ProtectiveOrdersVerified`, `ProtectiveOrdersMissing`, `ProtectiveOrdersReplaced` (names flexible).

## Acceptance Criteria
- If SL/TP are cancelled from the Binance UI, the bot detects it within one cycle and responds deterministically.

