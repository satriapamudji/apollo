# Task 10: Continuous Reconciliation Loop

## What Was Changed

### Before
Reconciliation only ran once at startup in `main.py`:
```python
await _reconcile(state_manager, event_bus, rest)
```

This meant the bot could drift from exchange state while running due to:
- Manual trades on the exchange
- Partial fills not captured by WebSocket
- Orders canceled externally
- Position modifications outside the bot

### After

1. **New Configuration** (`src/config/settings.py`):
   - Added `ReconciliationConfig` class with:
     - `enabled: bool = True` - Enable/disable continuous reconciliation
     - `interval_minutes: int = 30` - How often to reconcile (5-1440 min)
     - `failure_threshold: int = 3` - Consecutive failures before alert

2. **New Metrics** (`src/monitoring/metrics.py`):
   - `reconciliation_discrepancy_total` - Counter with labels for discrepancy type
   - `reconciliation_failure_total` - Counter for API failures
   - `reconciliation_success_total` - Counter for successful runs
   - `reconciliation_consecutive_failures` - Gauge tracking consecutive failures

3. **New Loop** (`src/main.py`):
   - Added `reconciliation_loop()` that runs concurrently with other loops
   - Tracks consecutive failures and triggers `MANUAL_INTERVENTION` event when threshold reached
   - Updates discrepancy counters by type (POSITION_OPENED_EXTERNALLY, POSITION_SIZE_CHANGED, etc.)
   - Logs and publishes events to the event ledger

4. **Updated `_reconcile` function**:
   - Now accepts optional `Metrics` parameter
   - Returns `bool` indicating success/failure
   - Updates discrepancy counters by type

## Why

Exchange state can change while the bot is running:
- Manual trades by the trader
- Partial order fills not captured by WebSocket
- Orders canceled externally
- Position modifications through other interfaces

A continuous reconciliation loop provides:
- **"Truth sync"** - Regular verification that internal state matches exchange
- **Early detection** - Discrepancies caught within minutes, not hours
- **Alerting** - Repeated failures trigger manual review
- **Visibility** - Discrepancies visible in Prometheus metrics and event ledger

## Files Modified

- `src/config/settings.py` - Added `ReconciliationConfig` class
- `src/monitoring/metrics.py` - Added reconciliation metrics
- `src/main.py` - Added `reconciliation_loop()` and updated `_reconcile()`

## Configuration Example

```yaml
reconciliation:
  enabled: True
  interval_minutes: 30
  failure_threshold: 3
```

## Metrics Available

- `reconciliation_discrepancy_total{type="POSITION_OPENED_EXTERNALLY"}` - Position opened outside bot
- `reconciliation_discrepancy_total{type="POSITION_SIZE_CHANGED"}` - Position size mismatch
- `reconciliation_discrepancy_total{type="POSITION_CLOSED_EXTERNALLY"}` - Position closed outside bot
- `reconciliation_discrepancy_total{type="ORDER_PLACED_EXTERNALLY"}` - Order placed outside bot
- `reconciliation_discrepancy_total{type="ORDER_MISSING_ON_EXCHANGE"}` - Order missing from exchange
- `reconciliation_failure_total` - API/execution failures
- `reconciliation_success_total` - Successful reconciliations
- `reconciliation_consecutive_failures` - Current consecutive failure count
- `last_reconciliation_age_hr` - Hours since last successful reconciliation
