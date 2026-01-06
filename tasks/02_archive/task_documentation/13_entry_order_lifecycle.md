# Task 13: Fix Entry Order Lifecycle (Fill Probability > Theory)

## Overview

This document describes the implementation of an explicit entry order lifecycle with timeframe-aware timeouts to prevent trades from silently dying when they don't fill within a short window.

## Before

The execution engine had several issues:

1. **Fixed 30-second timeout**: Orders were cancelled after 30 seconds regardless of market conditions
2. **Silent skipping**: If an order didn't fill, `last_processed_candles` would skip re-processing the same candle
3. **No lifecycle tracking**: No visibility into order state across strategy cycles
4. **4-hour gap**: On 4h strategies, missing a fill meant waiting 4 hours for the next candle

### Flow Diagram (Before)

```
Signal → Place LIMIT Order → Wait 30s → Timeout → Cancel
                                      ↓
                               Skip next candle (same timestamp)
                                      ↓
                              Wait 4 hours for next candle
```

## After

### Changes Made

#### 1. Added new configuration settings (`src/config/settings.py:208-228`)

```python
# Entry order timeout mode: "fixed" (30s default), "timeframe" (until next candle), "unlimited" (GTC)
entry_timeout_mode: Literal["fixed", "timeframe", "unlimited"] = "timeframe"

# Fallback timeout in seconds for 'timeframe' mode
entry_fallback_timeout_sec: int = 60

# Max duration for 'unlimited' mode
entry_max_duration_sec: int = 3600

# Fallback action when order expires: "cancel", "convert_market", "convert_stop"
entry_expired_action: Literal["cancel", "convert_market", "convert_stop"] = "cancel"

# Enable order lifecycle tracking
entry_lifecycle_enabled: bool = True
```

#### 2. Added candle_timestamp to TradeProposal (`src/models.py:32`)

```python
@dataclass(frozen=True)
class TradeProposal:
    # ... existing fields ...
    candle_timestamp: datetime | None = None  # The candle this signal belongs to
```

#### 3. Added OrderLifecycle model (`src/models.py:35-49`)

New model for tracking order lifecycle state:
```python
@dataclass(frozen=True)
class OrderLifecycle:
    client_order_id: str
    trade_id: str
    symbol: str
    side: Side
    state: Literal["PLACED", "OPEN", "FILLED", "CANCELLED", "EXPIRED"]
    created_at: datetime
    last_updated: datetime
    candle_timestamp: datetime | None
    fill_price: float | None = None
    cancel_reason: str | None = None
    attempt_count: int = 1
```

#### 4. Extended PendingEntry with lifecycle info (`src/execution/engine.py:39-47`)

```python
@dataclass(frozen=True)
class PendingEntry:
    proposal: TradeProposal
    stop_price: float | None
    tick_size: float
    lifecycle_state: str = "OPEN"  # OPEN, WAITING_FILL
    candle_timestamp: datetime | None = None
    attempt_count: int = 1
    original_client_order_id: str | None = None
```

#### 5. Added ORDER_EXPIRED event type (`src/ledger/events.py:28`)

```python
ORDER_EXPIRED = "OrderExpired"
```

#### 6. Added lifecycle methods (`src/execution/engine.py:1213-1373`)

- `_find_pending_for_candle()`: Find pending entry for a specific candle
- `has_pending_for_candle()`: Check if there's a pending order for a candle
- `get_pending_for_symbol()`: Get all pending entries for a symbol
- `_compute_entry_deadline()`: Compute timeout based on config mode
- `_handle_order_expired()`: Handle expired orders with configurable fallback

#### 7. Modified execute_entry() (`src/execution/engine.py:156-168`)

Added check for existing pending entries:
```python
# Check for existing pending entry from the same candle (lifecycle tracking)
if self.config.entry_lifecycle_enabled and proposal.candle_timestamp:
    existing = self._find_pending_for_candle(proposal.symbol, proposal.candle_timestamp)
    if existing:
        self.log.info("resuming_order_tracking", ...)
        return  # Don't place new order
```

#### 8. Modified strategy loop (`src/main.py:314-322`)

Modified skip logic to not skip if there's a pending order:
```python
candle_close = fourh_df.index[-1]
last_candle = last_processed_candles.get(symbol)
has_pending = execution_engine.has_pending_for_candle(symbol, candle_close.to_pydatetime())
if last_candle is not None and candle_close <= last_candle and not has_pending:
    continue  # Skip only if no pending orders
last_processed_candles[symbol] = candle_close
```

### Flow Diagram (After)

```
Signal → Check for existing pending → Existing? → Resume tracking
                                      ↓
                             No → Place LIMIT Order
                                      ↓
                             Compute deadline (configurable)
                                      ↓
                             Wait until deadline
                                      ↓
                    FILLED → Position Opened ✓
                                      ↓
                    Timeout → Handle expiration (configurable)
                                      ↓
                    cancel → Log ORDER_EXPIRED
                    convert_market → Market order
                    convert_stop → Stop-market order
```

## Timeout Modes

| Mode | Behavior | Use Case |
|------|----------|----------|
| `fixed` | Order expires after 30s (default) | Backward compatibility |
| `timeframe` | Order works until next candle (e.g., 4h) | Trend-following strategies |
| `unlimited` | Order stays until filled or manually cancelled | GTC orders |

## Fallback Actions

When an order expires, the system can:

1. **`cancel`**: Just log and let the signal retry on next candle (default)
2. **`convert_market`**: Convert to market order for immediate execution
3. **`convert_stop`**: Convert to stop-market entry (good for breakouts)

## Backward Compatibility

- Default `entry_timeout_mode = "timeframe"` is safer than "fixed"
- `order_timeout_sec` still used for fallback
- Exit orders don't require proposal (backward compatible)
- Existing PendingEntryStore works with new fields

## Acceptance Criteria Verification

- [x] Orders persist across strategy cycles until filled, cancelled, or expired
- [x] Timeframe-aware timeouts keep orders working until next candle
- [x] Expired orders can optionally convert to market/stop orders
- [x] Pending orders are tracked in `logs/orders.csv` with full lifecycle
- [x] The bot does NOT silently skip signals due to short timeout + candle dedup
- [x] State persists across restarts via existing `PendingEntryStore`
