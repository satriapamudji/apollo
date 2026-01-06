# Task 13 — Fix Entry Order Lifecycle (Fill Probability > Theory)

## Goal
Make entry execution behave like a real 4h system: orders have a lifecycle, fills are not assumed, and missed fills don’t silently kill the strategy.

## Why
Today entries are `LIMIT` with `order_timeout_sec` (default 30s) and then the bot will *never re-attempt* on that same candle because `last_processed_candles` skips it. On 4h strategies this will miss most valid trades and make live results diverge from paper/backtests.

## Deliverables
- Add an “entry order policy” with explicit states:
  - `PLACE` → `OPEN` → `FILLED` / `CANCELLED` / `EXPIRED`
  - Persist state across strategy cycles (and ideally restarts).
- Change timeouts to be **timeframe-aware**:
  - e.g., entry order can remain valid until next 4h candle or until the signal invalidates.
- Add a safe fallback mode:
  - if order does not fill within N minutes, either keep it working, or cancel and optionally re-place as market/stop (config-driven).
- Ensure `paper` mode simulates realistic non-fills (see Task 19).

## Implementation Notes (Binance Futures)
- Consider `timeInForce=GTC` + explicit cancel/replace.
- For “breakout” entries, a `STOP_MARKET` or `STOP`-limit style entry often matches intent better than a discounted limit.
- Track `clientOrderId` as the idempotency key across cycles.

## Acceptance Criteria
- A trade signal at candle close is either (a) filled and protected, or (b) explicitly expired/cancelled with a logged reason.
- The bot does not silently “skip” a signal due to a short timeout + candle de-duplication.
- `logs/orders.csv` reflects the full entry lifecycle end-to-end.

