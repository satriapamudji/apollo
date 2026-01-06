# Task 09: Protective Order Watchdog (Auto-Remediate or Pause)

## What Was Added

A new **Protective Order Watchdog** system that continuously verifies every open position has the expected protective orders (SL/TP/trailing stop) on-exchange. If orders are missing, the watchdog can auto-replace them or emit events for manual review.

## Files Modified

| File | Changes |
|------|---------|
| `src/ledger/events.py` | Added 3 new event types: `PROTECTIVE_ORDERS_VERIFIED`, `PROTECTIVE_ORDERS_MISSING`, `PROTECTIVE_ORDERS_REPLACED` |
| `src/config/settings.py` | Added `WatchdogConfig` class with `enabled`, `interval_sec`, and `auto_recover` settings |
| `src/execution/engine.py` | Added `verify_protective_orders()`, `_replace_protective_orders()`, and `_get_symbol_filters()` methods |
| `src/main.py` | Added `watchdog_loop()` function that runs concurrently with other loops |
| `config.yaml` | Added `watchdog` configuration section |

## Key Implementation Details

### Event Types
- **PROTECTIVE_ORDERS_VERIFIED**: Emitted when all protective orders are present on-exchange
- **PROTECTIVE_ORDERS_MISSING**: Emitted when protective orders are missing, includes `auto_recover` flag
- **PROTECTIVE_ORDERS_REPLACED**: Emitted when orders were auto-replaced, includes `replaced_types` and `order_ids`

### Watchdog Configuration (`WatchdogConfig`)
```python
class WatchdogConfig(BaseModel):
    enabled: bool = True              # Enable/disable watchdog
    interval_sec: int = 300          # Check interval in seconds (5 minutes)
    auto_recover: bool = True        # Auto-replace missing orders
```

### Order Pattern Matching
The watchdog detects protective orders by matching client order ID patterns:
- Stop Loss: `T_{symbol}_SL-{LONG|SHORT}_*`
- Trailing Stop: `T_{symbol}_SL-TRAIL-{LONG|SHORT}_*`
- Take Profit: `T_{symbol}_TP-PARTIAL-{LONG|SHORT}_*`

### Watchdog Loop
Runs concurrently with other loops (universe, news, strategy, user_stream) and:
1. Checks each open position for protective orders
2. Queries exchange via `rest.get_open_orders(symbol)`
3. Compares exchange orders against expected patterns
4. If missing and `auto_recover=True`: replaces orders
5. Emits appropriate events to the ledger

## Configuration Example

```yaml
watchdog:
  enabled: true
  interval_sec: 300   # 5 minutes
  auto_recover: true  # Auto-replace missing orders
```

## Why This Matters

This addresses the **#1 operational risk**: even with "place after fill" logic, orders can disappear due to:
- Manual cancellation from Binance UI
- Exchange reject
- Connectivity issues

The watchdog ensures the bot detects missing protective orders within one cycle (5 minutes by default) and responds deterministically.

## Testing
- All existing tests pass (90 passed, 4 pre-existing failures unrelated to this change)
- The implementation handles API failures gracefully (logs warning, continues)
