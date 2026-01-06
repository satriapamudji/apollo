# Task 05: Exits That Let Winners Run (Trailing Stop + Optional Partial TP)

## Summary
Implemented ATR-based trailing stop logic and partial take profit to replace the fixed TP at 3*ATR, enabling trend-following strategies to capture larger moves.

## What Was Changed

### 1. src/strategy/signals.py
- Removed: Hardcoded \`take_profit = price + (3.0 * atr)\` (line 307)
- Added: \`take_profit=None\` to Signal, with comment explaining trailing stop + partial TP approach

### 2. src/execution/engine.py
- Modified \`_place_protective_orders()\`: Added partial take profit placement at 2*ATR for 25% of position
- Added \`update_trailing_stop()\` method: New async method that:
  - Calculates trailing stop based on \`trailing_start_atr\` and \`trailing_distance_atr\` config
  - Only updates stop when price moves \`trailing_start_atr\` in favor
  - Trails stop at \`trailing_distance_atr\` behind the high/low
  - Never widens stop (only moves in favor)
  - Safety: publishes \`MANUAL_INTERVENTION\` event if update fails

### 3. src/main.py
- Modified \`strategy_loop()\`: Added trailing stop monitoring for open positions
  - Fetches current price and ATR for each symbol with open position
  - Calls \`execution_engine.update_trailing_stop()\` when appropriate

## Configuration (Already in config.yaml)
\`\`\`yaml
exit:
  atr_stop_multiplier: 2.0      # Initial stop distance
  trailing_start_atr: 1.5       # Start trailing after 1.5 ATR profit
  trailing_distance_atr: 1.5    # Trail 1.5 ATR behind high/low
  time_stop_days: 7
\`\`\`

## How It Works

### Entry Flow
1. Signal generated with entry price, stop price, and ATR
2. Initial stop loss placed at \`entry - atr_stop_multiplier * atr\` (LONG) / \`entry + atr_stop_multiplier * atr\` (SHORT)
3. Partial TP placed at \`entry + 2*atr\` (LONG) / \`entry - 2*atr\` (SHORT) for 25% of position
4. Remaining 75% governed by trailing stop

### Trailing Stop Logic
For LONG positions:
- Track unrealized profit = current_price - entry_price
- Once profit > \`trailing_start_atr * atr\`, start trailing
- New stop = current_price - \`trailing_distance_atr * atr\`
- Stop only moves up (never down)

For SHORT positions:
- Track unrealized profit = entry_price - current_price
- Once profit > \`trailing_start_atr * atr\`, start trailing
- New stop = current_price + \`trailing_distance_atr * atr\`
- Stop only moves down (never up)

### Safety
If trailing stop update fails (API error, etc.):
- Error is logged
- \`MANUAL_INTERVENTION\` event published with action \`TRAILING_STOP_UPDATE_FAILED\`
- Trading pauses (existing \`requires_manual_review\` mechanism)
- Manual review required before continuing

## Testing
Added \`tests/test_trailing_stop.py\` with 9 tests covering:
- Trailing stop thresholds (start and update)
- Stop widening prevention
- Partial take profit placement
- Failure handling and event publishing

All 9 tests pass.
