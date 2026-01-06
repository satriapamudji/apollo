# Task 11 — Alerts: Webhooks for "Something Is Wrong" (Manual Review, Circuit Breaker, etc.)

## Summary

Implemented an alert webhook system to notify operators immediately when the bot is paused, unprotected, or failing.

## Before

The system had no mechanism to send real-time alerts to operators. Key events like `ManualInterventionDetected` and `CircuitBreakerTriggered` were only logged to files and the console, requiring manual monitoring of logs.

## After

Created a new `AlertWebhookHandler` class that:
- Sends JSON payloads to configured webhook URLs when critical events occur
- Deduplicates alerts to prevent alert storms (default 5-minute window)
- Supports Slack and Discord formatting
- Includes run mode, symbol, trade_id, reason, and log pointers in each alert

## Changes

### Files Created
1. **`src/monitoring/alert_webhooks.py`** - Main implementation with:
   - `AlertWebhookHandler` class for event-driven webhook notifications
   - Deduplication using `(event_type, symbol, reason_hash)` keys
   - JSON, Slack, and Discord payload formatters
   - `send_test_alert()` function for connectivity testing

2. **`tests/test_alert_webhooks.py`** - Comprehensive test suite covering:
   - Initialization and configuration
   - Payload structure validation
   - Deduplication behavior
   - Slack/Discord formatting
   - Webhook failure handling
   - Multiple webhook support

### Files Modified
1. **`src/monitoring/__init__.py`** - Added `AlertWebhookHandler` export
2. **`src/main.py`** - Added webhook handler initialization and registration for:
   - `MANUAL_INTERVENTION` events
   - `CIRCUIT_BREAKER_TRIGGERED` events

## Key Features

### Deduplication
- Manual intervention events are deduped using `(event_type, symbol, reason_hash)` as the key
- Configurable deduplication window (default 5 minutes)
- Each manual intervention triggers exactly one operator alert per incident
- Circuit breaker alerts are not deduped (immediate action required)

### Payload Structure
```json
{
  "alert_type": "ManualInterventionDetected",
  "run_mode": "live",
  "symbol": "BTCUSDT",
  "trade_id": "t-123",
  "reason": "HANDLER_EXCEPTION",
  "timestamp": "2026-01-06T12:00:00.000Z",
  "event_id": "abc-123",
  "sequence_num": 42,
  "pointers": {
    "events": "data/ledger/events.jsonl",
    "orders": "logs/orders.csv",
    "trades": "logs/trades.csv"
  },
  "payload": {...},
  "metadata": {...}
}
```

### Slack Formatting
- Color-coded attachments (orange for warnings, red for circuit breaker)
- Emoji indicators ( for alerts,  for circuit breaker)
- Structured fields for symbol, run mode, event ID, timestamp
- Context block with log file pointers

### Discord Formatting
- Embed-based messages with color coding
- Fields for symbol, run mode, event ID, timestamp
- Footer with full event ID
- Timestamp in embed footer

## Configuration

Add webhook URLs to `config.yaml`:
```yaml
monitoring:
  alert_webhooks:
    - https://hooks.slack.com/services/xxx/xxx/xxx
    - https://discord.com/api/webhooks/xxx/xxx
```

## Acceptance Criteria Met

- [x] Manual intervention event triggers exactly one operator alert per incident (deduped)
- [x] JSON payloads sent to configured webhooks
- [x] Slack/Discord formatting available
- [x] Alerts include run mode, symbol, trade_id, reason, and log pointers
- [x] All new code passes type-check and linting
- [x] All tests pass (16/16)
