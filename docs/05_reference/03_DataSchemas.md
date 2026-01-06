# Data Schemas Reference

Reference for all data formats used in the Apollo.

## Event Ledger

**File**: `data/ledger/events.jsonl`
**Format**: JSON Lines (one JSON object per line)

### Event Schema

```json
{
  "event_id": "string (UUID)",
  "event_type": "string (EventType enum value)",
  "timestamp": "string (ISO-8601 with Z suffix)",
  "sequence_num": "integer",
  "payload": "object (event-specific data)",
  "metadata": "object (context data)"
}
```

**Example**:
```json
{"event_id":"550e8400-e29b-41d4-a716-446655440000","event_type":"OrderFilled","timestamp":"2024-01-15T10:30:00.000Z","sequence_num":1234,"payload":{"symbol":"BTCUSDT","side":"BUY","quantity":0.001,"price":42000.0},"metadata":{"source":"user_stream"}}
```

### Event Payload Schemas

#### OrderPlaced
```json
{
  "symbol": "BTCUSDT",
  "side": "BUY",
  "order_type": "LIMIT",
  "quantity": 0.001,
  "price": 42000.0,
  "stop_price": null,
  "client_order_id": "entry_abc123",
  "reduce_only": false
}
```

#### OrderFilled
```json
{
  "symbol": "BTCUSDT",
  "side": "BUY",
  "quantity": 0.001,
  "price": 42000.0,
  "fee": 0.0084,
  "fee_asset": "USDT",
  "order_id": 12345678,
  "client_order_id": "entry_abc123"
}
```

#### PositionOpened
```json
{
  "symbol": "BTCUSDT",
  "side": "LONG",
  "quantity": 0.001,
  "entry_price": 42000.0,
  "leverage": 3,
  "stop_price": 40000.0,
  "take_profit": 46000.0,
  "trade_id": "trade_abc123"
}
```

#### PositionClosed
```json
{
  "symbol": "BTCUSDT",
  "exit_price": 44000.0,
  "pnl": 2.0,
  "pnl_pct": 4.76,
  "reason": "TAKE_PROFIT",
  "holding_hours": 24.5
}
```

#### SignalComputed
```json
{
  "symbol": "BTCUSDT",
  "signal_type": "LONG",
  "score": 0.72,
  "regime": "TRENDING"
}
```

---

## Trade Log

**File**: `logs/trades.csv`
**Format**: CSV with headers

### Schema

| Column | Type | Description |
|--------|------|-------------|
| `trade_id` | string | Unique trade identifier |
| `symbol` | string | Trading symbol |
| `side` | string | LONG or SHORT |
| `entry_time` | datetime | Position open time |
| `entry_price` | float | Average entry price |
| `quantity` | float | Position size |
| `leverage` | int | Position leverage |
| `exit_time` | datetime | Position close time |
| `exit_price` | float | Average exit price |
| `pnl` | float | Realized P&L (USDT) |
| `pnl_pct` | float | Realized P&L (%) |
| `reason` | string | Exit reason |
| `holding_hours` | float | Position duration |

**Example**:
```csv
trade_id,symbol,side,entry_time,entry_price,quantity,leverage,exit_time,exit_price,pnl,pnl_pct,reason,holding_hours
trade_001,BTCUSDT,LONG,2024-01-15T08:00:00Z,42000.0,0.001,3,2024-01-16T08:00:00Z,44000.0,2.0,4.76,TAKE_PROFIT,24.0
```

---

## Order Log

**File**: `logs/orders.csv`
**Format**: CSV with headers

### Schema

| Column | Type | Description |
|--------|------|-------------|
| `timestamp` | datetime | Event timestamp |
| `event_type` | string | Order event type |
| `symbol` | string | Trading symbol |
| `side` | string | BUY or SELL |
| `order_type` | string | LIMIT, MARKET, STOP_MARKET, etc. |
| `quantity` | float | Order quantity |
| `price` | float | Order price |
| `stop_price` | float | Stop trigger price |
| `client_order_id` | string | Client order ID |
| `order_id` | int | Exchange order ID |
| `status` | string | Order status |
| `filled_qty` | float | Filled quantity |
| `avg_price` | float | Average fill price |

**Example**:
```csv
timestamp,event_type,symbol,side,order_type,quantity,price,stop_price,client_order_id,order_id,status,filled_qty,avg_price
2024-01-15T08:00:00Z,OrderPlaced,BTCUSDT,BUY,LIMIT,0.001,42000.0,,entry_001,,NEW,0,
2024-01-15T08:00:01Z,OrderFilled,BTCUSDT,BUY,LIMIT,0.001,42000.0,,entry_001,12345678,FILLED,0.001,42000.0
```

---

## Thinking Log

**File**: `logs/thinking.jsonl`
**Format**: JSON Lines

### Schema

```json
{
  "timestamp": "string (ISO-8601)",
  "symbol": "string",
  "signal_type": "string",
  "score": "float | null",
  "reason": "string | null",
  "regime": "string | null",
  "news_risk": "string",
  "funding_rate": "float",
  "entry_style": "string",
  "score_threshold": "float",
  "trend_timeframe": "string",
  "entry_timeframe": "string",
  "has_open_position": "boolean",
  "news_blocked": "boolean",
  "risk_approved": "boolean | null",
  "risk_reasons": "array | null"
}
```

**Example**:
```json
{"timestamp":"2024-01-15T08:00:00Z","symbol":"BTCUSDT","signal_type":"LONG","score":0.72,"reason":"Breakout confirmed","regime":"TRENDING","news_risk":"LOW","funding_rate":0.0001,"entry_style":"breakout","score_threshold":0.55,"trend_timeframe":"1d","entry_timeframe":"4h","has_open_position":false,"news_blocked":false,"risk_approved":true,"risk_reasons":null}
```

---

## OHLCV CSV

**File**: `data/market/<SYMBOL>_<interval>.csv`
**Format**: CSV with headers

### Canonical Schema

| Column | Type | Description |
|--------|------|-------------|
| `open_time` | int | Bar open timestamp (ms) |
| `close_time` | int | Bar close timestamp (ms) |
| `open` | float | Open price |
| `high` | float | High price |
| `low` | float | Low price |
| `close` | float | Close price |
| `volume` | float | Trading volume |

**Example**:
```csv
open_time,close_time,open,high,low,close,volume
1705312800000,1705327200000,42000.0,42500.0,41800.0,42300.0,1234.56
1705327200000,1705341600000,42300.0,43000.0,42200.0,42800.0,1567.89
```

### Legacy Schema Support

The loader also accepts:
- `timestamp` column (treated as `close_time`)
- `open_time` only (derives `close_time` from interval)

---

## Funding Rate CSV

**File**: `data/market/<SYMBOL>_funding.csv`
**Format**: CSV with headers

### Schema

| Column | Type | Description |
|--------|------|-------------|
| `timestamp` | int | Settlement timestamp (ms) |
| `symbol` | string | Trading symbol |
| `funding_rate` | float | Funding rate (decimal) |
| `mark_price` | float | Mark price at settlement |

**Example**:
```csv
timestamp,symbol,funding_rate,mark_price
1705320000000,BTCUSDT,0.0001,42100.0
1705348800000,BTCUSDT,0.00015,42500.0
```

---

## Spread CSV

**File**: `data/market/<SYMBOL>_spread.csv`
**Format**: CSV with headers

### Schema

| Column | Type | Description |
|--------|------|-------------|
| `timestamp` | int | Snapshot timestamp (ms) |
| `symbol` | string | Trading symbol |
| `bid` | float | Best bid price |
| `ask` | float | Best ask price |
| `bid_qty` | float | Bid quantity (optional) |
| `ask_qty` | float | Ask quantity (optional) |

**Example**:
```csv
timestamp,symbol,bid,ask,bid_qty,ask_qty
1705320000000,BTCUSDT,42099.5,42100.5,10.5,8.2
1705320060000,BTCUSDT,42098.0,42101.0,12.3,9.1
```

---

## Backtest Results

### Summary JSON

**File**: `<out_dir>/summary.json`

```json
{
  "symbol": "BTCUSDT",
  "interval": "4h",
  "start_date": "2024-01-01",
  "end_date": "2024-06-30",
  "initial_equity": 1000.0,
  "final_equity": 1250.0,
  "total_return_pct": 25.0,
  "sharpe_ratio": 1.5,
  "max_drawdown_pct": 8.5,
  "win_rate": 0.55,
  "profit_factor": 1.8,
  "total_trades": 45,
  "avg_holding_hours": 18.5,
  "execution_model": "realistic",
  "random_seed": 42
}
```

### Equity CSV

**File**: `<out_dir>/equity.csv`

| Column | Type | Description |
|--------|------|-------------|
| `timestamp` | datetime | Date |
| `equity` | float | Account equity |
| `drawdown_pct` | float | Current drawdown |

### Trades CSV

**File**: `<out_dir>/trades.csv`

Same schema as Trade Log above.

---

## Strategy Spec YAML

**File**: `strategies/<name>/strategy.md`

### Schema

```yaml
---
name: string                    # Strategy identifier
version: integer                # Version number

requires:
  bars:                         # Required bar data
    - series: string            # "trade"
      interval: string          # "4h", "1d", etc.
  derived:                      # Derived timeframes
    - interval: string          # Target interval
      from: string              # Source interval
  funding: boolean              # Require funding data

parameters:                     # Configurable parameters
  <param_name>:
    type: string                # int, float, str, bool
    default: any                # Default value
    min: number                 # Minimum (int/float)
    max: number                 # Maximum (int/float)
    enum: array                 # Valid values (str)
    description: string         # Parameter description

assumptions:                    # Strategy assumptions
  - string
---
```

---

## Pending Entry Store

**File**: `data/state/pending_entries.json`

```json
{
  "entry_abc123": {
    "client_order_id": "entry_abc123",
    "symbol": "BTCUSDT",
    "side": "LONG",
    "entry_price": 42000.0,
    "stop_price": 40000.0,
    "take_profit": 46000.0,
    "quantity": 0.001,
    "leverage": 3,
    "trade_id": "trade_001",
    "created_at": "2024-01-15T08:00:00Z",
    "candle_timestamp": "2024-01-15T08:00:00Z",
    "atr": 500.0
  }
}
```

---

## Universe Snapshot

**File**: Generated by `build_universe.py`

```json
{
  "snapshot_id": "string (UUID)",
  "created_at": "2024-01-15T00:00:00Z",
  "selection_params": {
    "min_quote_volume_usd": 50000000,
    "max_funding_rate_pct": 0.1,
    "size": 5
  },
  "symbols": [
    {
      "symbol": "BTCUSDT",
      "quote_volume_24h": 5000000000,
      "price": 42000.0,
      "trades_24h": 1500000,
      "status": "TRADING"
    }
  ]
}
```

---

## Dataset Manifest

**File**: Used for reproducible backtests

```json
{
  "manifest_id": "string (UUID)",
  "created_at": "2024-01-15T00:00:00Z",
  "universe_snapshot_id": "string",
  "time_range": {
    "start": "2024-01-01T00:00:00Z",
    "end": "2024-06-30T23:59:59Z"
  },
  "artifacts": {
    "klines": ["BTCUSDT_4h.csv", "ETHUSDT_4h.csv"],
    "funding": ["BTCUSDT_funding.csv", "ETHUSDT_funding.csv"],
    "exchange_info": "exchange_info.json"
  },
  "execution_assumptions": {
    "maker_fee_pct": 0.02,
    "taker_fee_pct": 0.04,
    "slippage_model": "atr_scaled",
    "slippage_base_bps": 2.0,
    "funding_application": "discrete",
    "funding_cadence_hours": 8
  }
}
```

---

## Exchange Info Snapshot

**File**: `data/exchange_info.json`

```json
{
  "snapshot_time": "2024-01-15T00:00:00Z",
  "symbols": [
    {
      "symbol": "BTCUSDT",
      "contract_type": "PERPETUAL",
      "status": "TRADING",
      "filters": {
        "tick_size": 0.1,
        "step_size": 0.001,
        "min_qty": 0.001,
        "max_qty": 1000.0,
        "min_notional": 5.0
      }
    }
  ]
}
```

---

## Related Documentation

- [Event System](../03_development/03_EventSystem.md) - Event types
- [Configuration Reference](01_Configuration.md) - Storage paths
- [Backtester Overview](../05_backtester/01_Overview.md) - Backtest formats
