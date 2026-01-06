# Task 26 — Book Ticker Integration for Real-Time Spread Check

## Goal
Add pre-trade spread validation using Binance book ticker endpoint to reject entries when bid-ask spread is too wide.

## Why
Wide spreads are a hidden killer:
- During volatility spikes, spreads blow out from 0.01% to 0.3%+
- Your limit order at "current price" is actually crossing half the spread
- Market orders during wide spreads = instant slippage loss

Current system has `execution.max_spread_pct: 0.3` in config but **never actually checks spread**.

## Available Free Endpoint
```
GET /fapi/v1/ticker/bookTicker?symbol=BTCUSDT
```
Response:
```json
{
  "symbol": "BTCUSDT",
  "bidPrice": "97000.00",
  "bidQty": "10.500",
  "askPrice": "97001.50",
  "askQty": "8.200",
  "time": 1704000000000
}
```

Spread = (ask - bid) / mid = (97001.50 - 97000) / 97000.75 = 0.0015%

## Deliverables

### 1. REST Client Method
Add to `src/connectors/rest_client.py`:
```python
async def get_book_ticker(self, symbol: str) -> dict:
    """Get best bid/ask for symbol."""
    return await self._request("GET", "/fapi/v1/ticker/bookTicker",
                               params={"symbol": symbol}, weight=1)

async def get_spread_pct(self, symbol: str) -> float:
    """Return current spread as percentage of mid price."""
    ticker = await self.get_book_ticker(symbol)
    bid = float(ticker["bidPrice"])
    ask = float(ticker["askPrice"])
    mid = (bid + ask) / 2
    return ((ask - bid) / mid) * 100 if mid > 0 else 0.0
```

### 2. Pre-Trade Check
In `ExecutionEngine.execute_entry()`:
```python
spread_pct = await self.rest.get_spread_pct(proposal.symbol)
if spread_pct > self.config.max_spread_pct:
    await self.event_bus.publish(
        EventType.RISK_REJECTED,
        {
            "symbol": proposal.symbol,
            "reasons": ["SPREAD_TOO_WIDE"],
            "spread_pct": spread_pct,
            "threshold": self.config.max_spread_pct,
        },
        {"source": "execution_engine"},
    )
    return
```

### 3. Dynamic Spread Threshold
Instead of fixed threshold, scale with ATR:
- Calm market (ATR < 2%): max spread 0.05%
- Normal market (ATR 2-4%): max spread 0.10%
- Volatile market (ATR > 4%): max spread 0.20%

### 4. Logging
Add spread to order events:
```json
{
  "event_type": "ORDER_PLACED",
  "spread_at_entry_pct": 0.015,
  "bid": 97000.0,
  "ask": 97001.5
}
```

### 5. Metrics
Add Prometheus metrics:
- `trade_spread_pct` (histogram): Spread at time of entry
- `spread_rejections_total` (counter): Entries blocked due to spread

## Acceptance Criteria
- Every entry attempt checks current spread before order placement
- Entries rejected when spread > configured threshold
- Spread at entry time logged in events and thinking log
- Metrics show distribution of spreads encountered

## Files to Modify
- `src/connectors/rest_client.py`
- `src/execution/engine.py`
- `src/config/settings.py`
- `src/monitoring/metrics.py`

## Notes
- Book ticker is extremely lightweight (weight=1)
- Should be called immediately before order placement (spreads change fast)
- Consider caching for <1s if multiple symbols being processed in batch

