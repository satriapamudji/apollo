# Monitoring Guide

Comprehensive guide to monitoring the Apollo.

## Overview

The bot provides multiple monitoring interfaces:

- **Prometheus Metrics**: Port 9090 (default)
- **Operator API**: Port 8000 (default)
- **Log Files**: `logs/` directory
- **Event Ledger**: `data/ledger/events.jsonl`

---

## Prometheus Metrics

### Endpoint

```
http://localhost:9090/metrics
```

### Key Metrics

#### Loop Health

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `loop_last_tick_age_sec` | Gauge | `loop` | Seconds since last loop execution |

**Loops**: `universe`, `news`, `strategy`, `reconciliation`, `watchdog`, `telemetry`

```bash
# Check all loops
curl -s http://localhost:9090/metrics | grep loop_last_tick_age_sec
```

**Alert Threshold**: > 120 seconds indicates stalled loop.

#### WebSocket Status

| Metric | Type | Description |
|--------|------|-------------|
| `ws_connected` | Gauge | 1 = connected, 0 = disconnected |
| `ws_last_message_age_sec` | Gauge | Seconds since last WS message |

```bash
curl -s http://localhost:9090/metrics | grep ws_
```

**Alert Threshold**: `ws_connected = 0` or `ws_last_message_age_sec > 60`

#### Trading State

| Metric | Type | Description |
|--------|------|-------------|
| `open_positions` | Gauge | Number of open positions |
| `daily_pnl_pct` | Gauge | Daily P&L percentage |
| `max_drawdown_pct` | Gauge | Peak-to-trough drawdown |
| `consecutive_losses` | Gauge | Consecutive losing trades |

```bash
curl -s http://localhost:9090/metrics | grep -E "(open_positions|daily_pnl|drawdown|consecutive)"
```

#### API Performance

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `rest_request_latency_seconds` | Histogram | `endpoint` | REST API latency |
| `rest_request_errors_total` | Counter | `endpoint`, `error` | API errors |
| `rest_rate_limit_remaining` | Gauge | - | Rate limit headroom |

#### Reconciliation

| Metric | Type | Description |
|--------|------|-------------|
| `reconciliation_success_total` | Counter | Successful reconciliations |
| `reconciliation_failure_total` | Counter | Failed reconciliations |
| `reconciliation_consecutive_failures` | Gauge | Consecutive failures |
| `reconciliation_discrepancy_total` | Counter | Detected discrepancies |

**Alert Threshold**: `reconciliation_consecutive_failures >= 3`

#### Orders

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `orders_placed_total` | Counter | `side`, `type` | Orders placed |
| `orders_filled_total` | Counter | `side` | Orders filled |
| `orders_rejected_total` | Counter | `reason` | Orders rejected |
| `slippage_bps` | Histogram | - | Execution slippage |

---

## Grafana Dashboards

### Recommended Panels

#### Overview Dashboard

1. **System Health**
   - Loop age gauges (all loops)
   - WebSocket status
   - Uptime

2. **Trading State**
   - Open positions
   - Daily P&L
   - Drawdown gauge
   - Equity curve

3. **Execution**
   - Orders placed/filled
   - Slippage histogram
   - API latency

4. **Risk**
   - Consecutive losses
   - Circuit breaker status
   - Reconciliation status

### Sample Panel Queries

```promql
# Loop health (should be < 120)
max(loop_last_tick_age_sec) by (loop)

# Daily P&L
daily_pnl_pct

# Drawdown alert
max_drawdown_pct > 5

# API error rate (5m)
rate(rest_request_errors_total[5m])

# Order fill rate
rate(orders_filled_total[1h])
```

---

## Log Files

### File Locations

| File | Format | Description |
|------|--------|-------------|
| `logs/bot.log` | Structured JSON | Main application log |
| `logs/trades.csv` | CSV | Trade records |
| `logs/orders.csv` | CSV | Order history |
| `logs/thinking.jsonl` | JSONL | Strategy decisions |

### Log Levels

Configure in `config.yaml`:

```yaml
monitoring:
  log_level: INFO  # DEBUG, INFO, WARNING, ERROR
```

### Important Log Events

| Event | Level | Meaning |
|-------|-------|---------|
| `strategy_cycle_complete` | INFO | Normal cycle |
| `strategy_paused` | WARNING | Trading halted |
| `order_filled` | INFO | Order executed |
| `circuit_breaker_triggered` | WARNING | Risk limit hit |
| `reconciliation_completed` | INFO | State verified |
| `manual_intervention_detected` | WARNING | Review needed |

### Log Watching

```bash
# Real-time main log
tail -f logs/bot.log | jq

# Filter by level
tail -f logs/bot.log | jq 'select(.level == "warning")'

# Filter by event
tail -f logs/bot.log | jq 'select(.event == "order_filled")'

# Recent trades
tail -10 logs/trades.csv

# Strategy decisions
tail -f logs/thinking.jsonl | jq
```

---

## Event Ledger

### Location

```
data/ledger/events.jsonl
```

### Analysis

```bash
# Count events by type
jq -r '.event_type' data/ledger/events.jsonl | sort | uniq -c | sort -rn

# Recent events
tail -20 data/ledger/events.jsonl | jq

# Filter by type
grep "OrderFilled" data/ledger/events.jsonl | jq

# Events in time range
jq 'select(.timestamp > "2024-01-15T00:00:00Z")' data/ledger/events.jsonl
```

---

## Operator API

### Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | System health |
| `/state` | GET | Trading state |
| `/events?tail=N` | GET | Recent events |

### Health Check Script

```bash
#!/bin/bash
# health_check.sh

HEALTH=$(curl -s http://localhost:8000/health)
STATUS=$(echo $HEALTH | jq -r '.status')
MODE=$(echo $HEALTH | jq -r '.mode')
UPTIME=$(echo $HEALTH | jq -r '.uptime_sec')

if [ "$STATUS" != "healthy" ]; then
    echo "CRITICAL: Bot unhealthy"
    exit 2
fi

echo "OK: Bot healthy (mode=$MODE, uptime=${UPTIME}s)"
exit 0
```

### State Check Script

```bash
#!/bin/bash
# check_state.sh

STATE=$(curl -s http://localhost:8000/state)
MANUAL_REVIEW=$(echo $STATE | jq -r '.requires_manual_review')
CIRCUIT_BREAKER=$(echo $STATE | jq -r '.circuit_breaker_active')

if [ "$MANUAL_REVIEW" == "true" ]; then
    echo "WARNING: Manual review required"
    exit 1
fi

if [ "$CIRCUIT_BREAKER" == "true" ]; then
    echo "WARNING: Circuit breaker active"
    exit 1
fi

echo "OK: Trading state normal"
exit 0
```

---

## Alert Configuration

### Webhook Alerts

Configure in `config.yaml`:

```yaml
monitoring:
  alert_webhooks:
    - https://hooks.slack.com/services/xxx/yyy/zzz
    - https://discord.com/api/webhooks/xxx/yyy
```

**Triggered Events**:
- `MANUAL_INTERVENTION`
- `CIRCUIT_BREAKER_TRIGGERED`

### Custom Alert Script

```bash
#!/bin/bash
# alert_check.sh

# Check metrics
DRAWDOWN=$(curl -s http://localhost:9090/metrics | grep max_drawdown_pct | awk '{print $2}')

if (( $(echo "$DRAWDOWN > 5" | bc -l) )); then
    curl -X POST "$SLACK_WEBHOOK" \
        -H 'Content-type: application/json' \
        -d '{"text":"WARNING: Drawdown at '$DRAWDOWN'%"}'
fi
```

### Cron-Based Monitoring

```bash
# crontab -e
*/5 * * * * /home/trader/scripts/health_check.sh >> /var/log/bot_health.log 2>&1
*/15 * * * * /home/trader/scripts/check_state.sh >> /var/log/bot_state.log 2>&1
```

---

## Debug Mode

Enable verbose logging:

```yaml
monitoring:
  log_level: DEBUG
  log_http: true
  log_http_responses: true
  log_http_max_body_chars: 1000
```

---

## Troubleshooting

### High Loop Age

**Symptom**: `loop_last_tick_age_sec` > 120

**Causes**:
- Loop blocked by slow operation
- Exception in loop
- Resource exhaustion

**Actions**:
1. Check logs for errors
2. Check API latency metrics
3. Restart if necessary

### WebSocket Disconnected

**Symptom**: `ws_connected = 0`

**Causes**:
- Network issues
- Binance maintenance
- Rate limiting

**Actions**:
1. Bot auto-reconnects
2. Check network connectivity
3. Check Binance status page

### High Reconciliation Failures

**Symptom**: `reconciliation_consecutive_failures >= 3`

**Causes**:
- API issues
- State mismatch
- Network problems

**Actions**:
1. Check API connectivity
2. Review discrepancy events
3. Manual review if needed

---

## Maintenance

### Log Rotation

```bash
# Manual rotation
mv logs/bot.log logs/bot.log.$(date +%Y%m%d)
gzip logs/bot.log.$(date +%Y%m%d)
```

### Event Ledger Archival

```bash
# Archive old events
gzip -c data/ledger/events.jsonl > archive/events_$(date +%Y%m%d).jsonl.gz
```

### Metric Retention

Configure Prometheus retention:

```yaml
# prometheus.yml
global:
  scrape_interval: 15s

storage:
  tsdb:
    retention.time: 30d
```

---

## Related Documentation

- [RUNBOOK](../../RUNBOOK.md) - Operations procedures
- [Deployment Guide](01_Deployment.md) - Installation
- [API Reference](../04_reference/02_API.md) - API details
