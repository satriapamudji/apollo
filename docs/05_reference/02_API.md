# Operator API Reference

REST API reference for the Binance Trend Bot operator interface.

## Overview

The Operator API provides a FastAPI-based REST interface for monitoring bot state and taking safe actions. It runs on port 8000 by default (configurable via `monitoring.api_port`).

**Base URL**: `http://localhost:8000`

**Source File**: `src/api/operator.py`

---

## Endpoints

### Root

#### GET /

Get API information and available endpoints.

**Response**:
```json
{
  "name": "Trading Bot Operator API",
  "version": "0.1.0",
  "endpoints": {
    "health": "GET /health",
    "state": "GET /state",
    "events": "GET /events?tail=N",
    "ack_manual_review": "POST /actions/ack-manual-review?reason=<text>",
    "kill_switch": "POST /actions/kill-switch?reason=<text>",
    "pause": "POST /actions/pause?reason=<text>&duration_hours=<int>",
    "resume": "POST /actions/resume?reason=<text>"
  }
}
```

**Example**:
```bash
curl http://localhost:8000/
```

---

### Health Check

#### GET /health

Get system health status.

**Response**:
```json
{
  "status": "healthy",
  "uptime_sec": 3600.5,
  "mode": "paper",
  "trading_enabled": false,
  "environment": "testnet",
  "ws_last_message_age_sec": 5.2,
  "api_port": 8000,
  "metrics_port": 9090
}
```

**Fields**:
| Field | Type | Description |
|-------|------|-------------|
| `status` | string | Always "healthy" if API is responding |
| `uptime_sec` | float | Seconds since API started |
| `mode` | string | Run mode (paper/testnet/live) |
| `trading_enabled` | bool | Whether trading is active |
| `environment` | string | Environment (testnet/production) |
| `ws_last_message_age_sec` | float | Seconds since last WebSocket message |
| `api_port` | int | API port |
| `metrics_port` | int | Prometheus metrics port |

**Example**:
```bash
curl http://localhost:8000/health
```

---

### Trading State

#### GET /state

Get current trading state.

**Response**:
```json
{
  "equity": 100.0,
  "peak_equity": 105.0,
  "realized_pnl_today": 5.0,
  "daily_loss": 0.0,
  "consecutive_losses": 0,
  "cooldown_until": null,
  "circuit_breaker_active": false,
  "requires_manual_review": false,
  "last_reconciliation": "2024-01-15T10:30:00Z",
  "last_event_sequence": 1234,
  "universe": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
  "positions": {
    "BTCUSDT": {
      "symbol": "BTCUSDT",
      "side": "LONG",
      "quantity": 0.001,
      "entry_price": 42000.0,
      "leverage": 3,
      "opened_at": "2024-01-15T08:00:00Z",
      "stop_price": 40000.0,
      "take_profit": 46000.0,
      "unrealized_pnl": 50.0,
      "realized_pnl": 0.0
    }
  },
  "open_orders": {
    "order_123": {
      "client_order_id": "order_123",
      "symbol": "BTCUSDT",
      "side": "SELL",
      "order_type": "STOP_MARKET",
      "quantity": 0.001,
      "price": null,
      "stop_price": 40000.0,
      "reduce_only": true,
      "status": "NEW",
      "created_at": "2024-01-15T08:00:05Z",
      "order_id": 12345678
    }
  },
  "news_risk_flags": {
    "BTCUSDT": {
      "symbol": "BTCUSDT",
      "level": "LOW",
      "reason": "No significant news",
      "confidence": 0.9,
      "last_updated": "2024-01-15T09:00:00Z"
    }
  }
}
```

**Fields**:
| Field | Type | Description |
|-------|------|-------------|
| `equity` | float | Current account equity |
| `peak_equity` | float | Peak equity (for drawdown) |
| `realized_pnl_today` | float | Today's realized P&L |
| `daily_loss` | float | Today's loss amount |
| `consecutive_losses` | int | Consecutive losing trades |
| `cooldown_until` | string | ISO timestamp of cooldown end |
| `circuit_breaker_active` | bool | Circuit breaker triggered |
| `requires_manual_review` | bool | Manual review required |
| `last_reconciliation` | string | Last reconciliation time |
| `last_event_sequence` | int | Last processed event sequence |
| `universe` | array | Active trading symbols |
| `positions` | object | Open positions by symbol |
| `open_orders` | object | Open orders by ID |
| `news_risk_flags` | object | News risk levels by symbol |

**Example**:
```bash
curl http://localhost:8000/state | jq
```

---

### Event History

#### GET /events

Get recent events from the ledger.

**Query Parameters**:
| Parameter | Type | Default | Range | Description |
|-----------|------|---------|-------|-------------|
| `tail` | int | 100 | 1-1000 | Number of recent events |

**Response**:
```json
{
  "count": 100,
  "total": 1234,
  "events": [
    {
      "event_id": "550e8400-e29b-41d4-a716-446655440000",
      "event_type": "OrderFilled",
      "timestamp": "2024-01-15T10:30:00Z",
      "sequence_num": 1234,
      "payload": {
        "symbol": "BTCUSDT",
        "side": "BUY",
        "quantity": 0.001,
        "price": 42000.0
      },
      "metadata": {
        "source": "user_stream"
      }
    }
  ]
}
```

**Fields**:
| Field | Type | Description |
|-------|------|-------------|
| `count` | int | Number of events returned |
| `total` | int | Total events in ledger |
| `events` | array | Event objects |

**Example**:
```bash
# Last 100 events
curl "http://localhost:8000/events"

# Last 50 events
curl "http://localhost:8000/events?tail=50"

# Filter by type (client-side)
curl "http://localhost:8000/events" | jq '.events[] | select(.event_type == "OrderFilled")'
```

---

### Acknowledge Manual Review

#### POST /actions/ack-manual-review

Acknowledge manual review and clear the flag.

**Query Parameters**:
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `reason` | string | "operator_api" | Acknowledgment reason |

**Response**:
```json
{
  "success": true,
  "previously_flagged": true,
  "message": "Manual review acknowledged. Restart bot or wait for next reconciliation."
}
```

**Example**:
```bash
curl -X POST "http://localhost:8000/actions/ack-manual-review?reason=Verified%20positions"
```

---

### Kill Switch

#### POST /actions/kill-switch

Trigger emergency system shutdown event.

**Query Parameters**:
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `reason` | string | "operator_kill_switch" | Shutdown reason |

**Response**:
```json
{
  "success": true,
  "message": "Kill switch triggered. SYSTEM_STOPPED event recorded.",
  "reason": "market_emergency"
}
```

**Example**:
```bash
curl -X POST "http://localhost:8000/actions/kill-switch?reason=market_emergency"
```

---

### Pause Trading

#### POST /actions/pause

Pause trading by setting a cooldown period.

**Query Parameters**:
| Parameter | Type | Default | Range | Description |
|-----------|------|---------|-------|-------------|
| `reason` | string | "operator_pause" | - | Pause reason |
| `duration_hours` | int | 4 | 1-48 | Cooldown duration in hours |

**Response**:
```json
{
  "success": true,
  "message": "Trading paused until 2024-01-15T14:30:00Z",
  "cooldown_until": "2024-01-15T14:30:00Z",
  "reason": "scheduled_maintenance"
}
```

**Example**:
```bash
# Pause for default 4 hours
curl -X POST "http://localhost:8000/actions/pause"

# Pause for 8 hours
curl -X POST "http://localhost:8000/actions/pause?duration_hours=8&reason=weekend"
```

---

### Resume Trading

#### POST /actions/resume

Resume trading by clearing cooldown and manual review.

**Query Parameters**:
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `reason` | string | "operator_resume" | Resume reason |

**Response**:
```json
{
  "success": true,
  "message": "Trading resumed.",
  "previously_in_cooldown": true,
  "previously_in_manual_review": false,
  "reason": "maintenance_complete"
}
```

**Example**:
```bash
curl -X POST "http://localhost:8000/actions/resume?reason=maintenance_complete"
```

---

## Error Responses

All endpoints return standard HTTP error codes:

| Code | Description |
|------|-------------|
| 200 | Success |
| 400 | Bad request (invalid parameters) |
| 422 | Validation error |
| 500 | Internal server error |

**Error Response Format**:
```json
{
  "detail": "Error message describing the problem"
}
```

---

## Usage Examples

### Monitor Bot Health

```bash
#!/bin/bash
# health_check.sh

while true; do
    status=$(curl -s http://localhost:8000/health | jq -r '.status')
    if [ "$status" != "healthy" ]; then
        echo "Bot unhealthy!"
        # Send alert
    fi
    sleep 60
done
```

### Check for Manual Review

```bash
#!/bin/bash
# check_review.sh

needs_review=$(curl -s http://localhost:8000/state | jq -r '.requires_manual_review')
if [ "$needs_review" == "true" ]; then
    echo "Manual review required!"
    # Send alert or auto-acknowledge
fi
```

### Auto-Resume After Maintenance

```bash
#!/bin/bash
# resume_after_maintenance.sh

# Wait for maintenance window
sleep 3600

# Resume trading
curl -X POST "http://localhost:8000/actions/resume?reason=scheduled_maintenance_complete"
```

### Export Recent Events

```bash
# Export last 1000 events to file
curl -s "http://localhost:8000/events?tail=1000" | jq '.events' > events_export.json
```

---

## Configuration

```yaml
monitoring:
  api_port: 8000     # API port (default: 8000)
```

**Environment Variable Override**:
```
MONITORING__API_PORT=8080
```

---

## Related Documentation

- [RUNBOOK](../../RUNBOOK.md) - Operations procedures
- [Monitoring Guide](../02_operations/02_Monitoring.md) - Full monitoring setup
- [Configuration Reference](01_Configuration.md) - All config options
