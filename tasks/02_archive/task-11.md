# Task 11 — Alerts: Webhooks for “Something Is Wrong” (Manual Review, Circuit Breaker, etc.)

## Goal
Notify the operator immediately when the bot is paused, unprotected, or failing.

## Why
Logs are not alerts. If you have to “check the logs” to learn you’re unprotected, you’re already late.

## Deliverables
- Implement `monitoring.alert_webhooks` in a small alerting module.
- Send concise JSON payloads (and optionally Slack/Discord formatting) on:
  - `ManualInterventionDetected`
  - `CircuitBreakerTriggered`
  - repeated REST failures / WS disconnect storms
  - protective order missing
- Include: run mode, symbol, trade_id, reason, and pointers to where to look (`data/ledger/events.jsonl`, `logs/orders.csv`).

## Acceptance Criteria
- A manual intervention event triggers exactly one operator alert per incident (deduped).

