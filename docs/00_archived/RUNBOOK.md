# Operations Runbook

Operational procedures for running and maintaining the Apollo.

## Table of Contents

- [Pre-Flight Checklist](#pre-flight-checklist)
- [Starting the Bot](#starting-the-bot)
- [Stopping the Bot](#stopping-the-bot)
- [Monitoring](#monitoring)
- [Common Issues](#common-issues)
- [Emergency Procedures](#emergency-procedures)
- [Recovery Procedures](#recovery-procedures)
- [Maintenance Tasks](#maintenance-tasks)

---

## Pre-Flight Checklist

### Before First Run

- [ ] Python 3.10+ installed
- [ ] Dependencies installed: `pip install -e .`
- [ ] `.env` file created from `.env.example`
- [ ] API keys configured (testnet or live)
- [ ] `config.yaml` reviewed and customized
- [ ] Understand run mode implications (paper/testnet/live)

### Before Live Trading

- [ ] Strategy tested in paper mode
- [ ] Strategy validated in testnet with real execution
- [ ] Backtest results reviewed
- [ ] Risk parameters verified (`risk.*` in config)
- [ ] Alert webhooks configured (optional)
- [ ] Monitoring dashboards ready
- [ ] Emergency procedures understood
- [ ] `run.live_confirm: YES_I_UNDERSTAND` set intentionally

---

## Starting the Bot

### Paper Mode (Default)

```yaml
# config.yaml
run:
  mode: paper
  enable_trading: false
```

```bash
bot
```

### Testnet Mode

1. Add testnet keys to `.env`:
   ```
   BINANCE_TESTNET_FUTURE_API_KEY=your_key
   BINANCE_TESTNET_FUTURE_SECRET_KEY=your_secret
   ```

2. Configure `config.yaml`:
   ```yaml
   run:
     mode: testnet
     enable_trading: true
   ```

3. Start:
   ```bash
   bot
   ```

### Live Mode

1. Add live keys to `.env`:
   ```
   BINANCE_API_KEY=your_key
   BINANCE_SECRET_KEY=your_secret
   ```

2. Configure `config.yaml`:
   ```yaml
   run:
     mode: live
     enable_trading: true
     live_confirm: YES_I_UNDERSTAND
   ```

3. Start:
   ```bash
   bot
   ```

### Background Execution

**Linux/macOS:**
```bash
nohup bot > logs/bot.out 2>&1 &
echo $! > logs/bot.pid
```

**Windows PowerShell:**
```powershell
Start-Process -NoNewWindow -FilePath "bot" -RedirectStandardOutput "logs\bot.out" -RedirectStandardError "logs\bot.err"
```

**systemd (recommended for production):**
```ini
# /etc/systemd/system/binance-bot.service
[Unit]
Description=Apollo
After=network.target

[Service]
Type=simple
User=trader
WorkingDirectory=/home/trader/apollo
ExecStart=/home/trader/apollo/.venv/bin/bot
Restart=on-failure
RestartSec=30

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable binance-bot
sudo systemctl start binance-bot
sudo systemctl status binance-bot
```

---

## Stopping the Bot

### Graceful Shutdown

Send SIGINT (Ctrl+C) or SIGTERM:
```bash
kill $(cat logs/bot.pid)
# or
kill -TERM <PID>
```

### Single Instance Lock

The bot uses a lock file at `logs/bot.<mode>.lock`. If you see `another_instance_running`:

1. Find the running process:
   ```bash
   # Linux/macOS
   ps aux | grep bot

   # Windows PowerShell
   Get-Process bot,python -ErrorAction SilentlyContinue
   ```

2. Stop the process:
   ```bash
   # Linux/macOS
   kill <PID>

   # Windows PowerShell
   Stop-Process -Id <PID> -Force

   # Git Bash (Windows)
   MSYS2_ARG_CONV_EXCL='*' taskkill /PID <PID> /F
   ```

3. If process is already dead, remove stale lock:
   ```bash
   rm logs/bot.<mode>.lock
   ```

---

## Monitoring

### Prometheus Metrics

Default endpoint: `http://localhost:9090/metrics`

**Key Metrics:**

| Metric | Description |
|--------|-------------|
| `loop_last_tick_age_sec{loop="..."}` | Age since last loop execution |
| `ws_connected` | WebSocket connection status (1=connected) |
| `ws_last_message_age_sec` | Seconds since last WS message |
| `open_positions` | Number of open positions |
| `daily_pnl_pct` | Daily profit/loss percentage |
| `max_drawdown_pct` | Peak-to-trough drawdown |
| `reconciliation_consecutive_failures` | Consecutive reconciliation failures |
| `rest_request_latency_seconds` | REST API latency histogram |

**Health Check:**
```bash
curl -s http://localhost:9090/metrics | grep loop_last_tick_age_sec
```

### Log Files

| File | Content | Format |
|------|---------|--------|
| `logs/bot.log` | Main application log | Structured JSON |
| `logs/trades.csv` | Trade records | CSV |
| `logs/orders.csv` | Order history | CSV |
| `logs/thinking.jsonl` | Strategy decisions | JSONL |
| `data/ledger/events.jsonl` | Event ledger | JSONL |

### Key Log Events

Watch for these events in the application log:

| Event | Meaning |
|-------|---------|
| `strategy_cycle_complete` | Normal strategy cycle |
| `strategy_paused` | Trading halted (manual review/circuit breaker) |
| `reconciliation_completed` | State verification passed |
| `manual_intervention_detected` | Requires operator action |
| `circuit_breaker_triggered` | Trading halted due to risk limits |
| `order_filled` | Order execution confirmed |
| `position_opened` | New position established |
| `position_closed` | Position exited |

### Operator API

Default endpoint: `http://localhost:8000`

**Endpoints:**

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/state` | GET | Current trading state |
| `/positions` | GET | Open positions |
| `/orders` | GET | Open orders |
| `/events` | GET | Recent events |
| `/config` | GET | Active configuration |
| `/pause` | POST | Pause trading |
| `/resume` | POST | Resume trading |

```bash
# Check state
curl http://localhost:8000/state | jq

# Check positions
curl http://localhost:8000/positions | jq
```

### HTTP Debug Mode

Enable verbose HTTP logging:

```yaml
monitoring:
  log_http: true
  log_http_responses: true
  log_http_max_body_chars: 1000
```

---

## Common Issues

### `strategy_paused` in Logs

**Cause:** Trading halted due to manual review requirement or circuit breaker.

**Diagnosis:**
```bash
# Check for manual intervention events
grep -i "manual" data/ledger/events.jsonl | tail -5

# Check state
curl http://localhost:8000/state | jq '.requires_manual_review, .circuit_breaker_active'
```

**Resolution:** See [Emergency Procedures](#emergency-procedures).

### `another_instance_running`

**Cause:** Lock file exists from previous run.

**Resolution:**
1. Verify no other bot process is running
2. Remove stale lock: `rm logs/bot.<mode>.lock`

### Orders Not Filling

**Cause:** Entry order placed but not yet filled.

**Diagnosis:**
```bash
# Check for OrderPlaced without OrderFilled
grep "OrderPlaced" data/ledger/events.jsonl | tail -5
grep "OrderFilled" data/ledger/events.jsonl | tail -5
```

**Note:** TP/SL orders are only placed after entry fills.

### Missing TP/SL Orders

**Cause:** Entry filled but protective orders failed.

**Diagnosis:**
```bash
# Check watchdog status
curl http://localhost:9090/metrics | grep watchdog
```

**Resolution:** Watchdog will auto-recover if `watchdog.auto_recover: true`.

### WebSocket Disconnection

**Cause:** Network issues or Binance server maintenance.

**Diagnosis:**
```bash
curl http://localhost:9090/metrics | grep ws_connected
curl http://localhost:9090/metrics | grep ws_last_message_age_sec
```

**Resolution:** Bot auto-reconnects. If persistent, check network/firewall.

### Reconciliation Failures

**Cause:** State mismatch between bot and exchange.

**Diagnosis:**
```bash
grep "reconciliation" data/ledger/events.jsonl | tail -10
curl http://localhost:9090/metrics | grep reconciliation
```

**Resolution:** Manual review may be required. See [Recovery Procedures](#recovery-procedures).

### Rate Limiting

**Cause:** Too many API requests.

**Diagnosis:**
```bash
grep "rate_limit" logs/bot.log | tail -5
curl http://localhost:9090/metrics | grep rest_rate_limit
```

**Resolution:** Bot handles rate limits automatically with backoff.

---

## Emergency Procedures

### Circuit Breaker Triggered

**Symptoms:**
- `circuit_breaker_triggered` event in ledger
- `strategy_paused` in logs
- Trading halted

**Cause:**
- Max drawdown exceeded (default: 10%)
- Consecutive losses exceeded (default: 3)

**Resolution:**
1. Review positions and equity on Binance
2. Assess market conditions
3. Decide whether to continue trading
4. Acknowledge and reset:
   ```bash
   ack-manual-review --reason "Reviewed drawdown, market conditions stable"
   ```
5. Restart bot

### Manual Intervention Required

**Symptoms:**
- `manual_intervention_detected` event in ledger
- `strategy_paused` in logs
- Alert webhook fired (if configured)

**Causes:**
- Reconciliation discrepancy detected
- Position/order mismatch
- Unexpected account state

**Resolution:**
1. Compare bot state with Binance account:
   ```bash
   curl http://localhost:8000/state | jq
   curl http://localhost:8000/positions | jq
   ```
2. Review Binance UI for actual positions/orders
3. Resolve discrepancies (manual close/cancel if needed)
4. Acknowledge:
   ```bash
   ack-manual-review --reason "Reconciled with exchange state"
   ```
5. Restart bot

### Kill Switch (Emergency Stop)

**When to use:** Immediate halt needed due to market conditions or system issues.

**Actions:**
1. Stop the bot immediately:
   ```bash
   kill -9 $(cat logs/bot.pid)
   ```
2. Close all positions on Binance UI
3. Cancel all open orders on Binance UI
4. Review event ledger for state
5. Do NOT restart until situation assessed

### News Risk Block

**Symptoms:**
- `NEWS_CLASSIFIED` with `risk: HIGH`
- Entries blocked for affected symbols

**Resolution:**
- Automatic: Block expires after `news.high_risk_block_hours` (default: 24h)
- Manual: Review news, assess risk, wait for expiry

---

## Recovery Procedures

### State Rebuild

Reconstruct state from event ledger:

```bash
# Bot automatically rebuilds on startup
bot
# Look for: "state_rebuilt" in logs
```

### Reset Local State

**Warning:** This resets local state only. Does NOT affect Binance positions/orders.

1. Stop the bot
2. Delete state files:
   ```bash
   rm data/ledger/events.jsonl
   rm data/ledger/sequence.txt
   rm logs/orders.csv
   rm logs/trades.csv
   rm logs/thinking.jsonl
   rm data/state/*.json  # Pending entries
   ```
3. Verify Binance account state
4. Restart bot (will start fresh)

### Recover from Crash

1. Check for stale lock:
   ```bash
   ls -la logs/bot.*.lock
   ```
2. Remove if process not running:
   ```bash
   rm logs/bot.<mode>.lock
   ```
3. Start bot normally:
   ```bash
   bot
   ```
4. Bot will:
   - Rebuild state from event ledger
   - Validate pending entries against exchange
   - Resume normal operation

### Manual Position Close

If bot cannot close a position:

1. Via Binance UI:
   - Navigate to Futures > Positions
   - Click "Close" or "Market Close"

2. Via API (emergency):
   ```bash
   # Close long position
   curl -X POST "https://fapi.binance.com/fapi/v1/order" \
     -H "X-MBX-APIKEY: $BINANCE_API_KEY" \
     -d "symbol=BTCUSDT&side=SELL&type=MARKET&quantity=0.001&timestamp=$(date +%s)000"
   ```

---

## Maintenance Tasks

### Daily

- [ ] Check Prometheus metrics for loop health
- [ ] Review `logs/trades.csv` for unexpected trades
- [ ] Check `logs/bot.log` for errors/warnings
- [ ] Verify WebSocket connection status

### Weekly

- [ ] Review backtest performance vs. live performance
- [ ] Check reconciliation success rate
- [ ] Review risk metrics (drawdown, loss streak)
- [ ] Archive old log files

### Monthly

- [ ] Rotate API keys (security best practice)
- [ ] Review and update configuration
- [ ] Test disaster recovery procedures
- [ ] Update dependencies: `pip install -e . --upgrade`

### Log Rotation

```bash
# Manual rotation
mv logs/bot.log logs/bot.log.$(date +%Y%m%d)
gzip logs/bot.log.$(date +%Y%m%d)

# Restart to create new log file
kill -HUP $(cat logs/bot.pid)
```

**logrotate config (Linux):**
```
/home/trader/apollo/logs/*.log {
    daily
    rotate 30
    compress
    delaycompress
    missingok
    notifempty
    create 644 trader trader
}
```

---

## Quick Reference

### Essential Commands

```bash
# Start bot
bot

# Check status
curl http://localhost:8000/health

# View state
curl http://localhost:8000/state | jq

# Check metrics
curl http://localhost:9090/metrics | grep -E "(loop_last_tick|ws_connected|open_positions)"

# Acknowledge manual review
ack-manual-review --reason "verified"

# View recent events
tail -20 data/ledger/events.jsonl | jq

# View recent trades
tail -10 logs/trades.csv
```

### Important Paths

| Path | Purpose |
|------|---------|
| `config.yaml` | Main configuration |
| `.env` | API keys and secrets |
| `data/ledger/events.jsonl` | Event ledger |
| `logs/bot.log` | Application log |
| `logs/trades.csv` | Trade records |
| `logs/bot.<mode>.lock` | Instance lock |

### Alert Response Priority

| Alert | Priority | Response Time |
|-------|----------|---------------|
| Circuit breaker triggered | HIGH | Immediate |
| Manual intervention | HIGH | < 15 minutes |
| Reconciliation failure | MEDIUM | < 1 hour |
| WebSocket disconnect | LOW | Monitor (auto-reconnect) |

---

## Related Documentation

- [System Overview](docs/00_architecture/01_SystemOverview.md) - Architecture details
- [Monitoring Guide](docs/02_operations/02_Monitoring.md) - Detailed monitoring setup
- [Configuration Reference](docs/04_reference/01_Configuration.md) - All config options
