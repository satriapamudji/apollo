# Backtester Overview

Architecture and design of the Binance Trend Bot backtesting system.

## Purpose

The backtester simulates historical trading to evaluate strategy performance before live deployment. It provides:

- **Realistic Simulation**: Variable slippage, fill probability, funding settlements
- **Event-Driven Architecture**: Same event flow as live trading
- **Reproducibility**: Deterministic results with random seeds
- **Performance Metrics**: Sharpe ratio, drawdown, win rate, profit factor

---

## Architecture

```
                +-------------------+
                |  Backtest Runner  |
                |   (runner.py)     |
                +---------+---------+
                          |
            +-------------+-------------+
            |                           |
    +-------v-------+           +-------v-------+
    | Single-Symbol |           | Multi-Symbol  |
    |    Engine     |           | Replay Engine |
    |  (engine.py)  |           |(replay_engine)|
    +-------+-------+           +-------+-------+
            |                           |
    +-------v-------+           +-------v-------+
    | Execution Sim |           |  Event Mux    |
    |(execution_sim)|           |  (event_mux)  |
    +-------+-------+           +-------+-------+
            |                           |
    +-------v-------+           +-------v-------+
    | Funding Model |           | Spread Model  |
    |  (funding.py) |           | (spread.py)   |
    +---------------+           +---------------+
                          |
                +---------v---------+
                |    Reporting      |
                |  (reporting.py)   |
                +-------------------+
```

---

## Components

### Backtest Runner (`runner.py`)

CLI interface and orchestration.

**Responsibilities**:
- Parse command-line arguments
- Load configuration and data
- Validate strategy spec
- Execute backtest engine
- Generate reports

### Backtest Engine (`engine.py`)

Core single-symbol backtest logic.

**Responsibilities**:
- Iterate through historical bars
- Apply strategy logic
- Execute simulated trades
- Track equity and positions
- Calculate performance metrics

### Replay Engine (`replay_engine.py`)

Multi-symbol event-driven backtest.

**Responsibilities**:
- Synchronize multiple symbol streams
- Maintain deterministic event ordering
- Apply strategy across portfolio
- Handle cross-sectional selection

### Execution Simulation (`execution_sim.py`)

Realistic fill modeling.

**Responsibilities**:
- Slippage calculation (fixed or ATR-scaled)
- Fill probability modeling
- Partial fill simulation
- Fee calculation

### Funding Model (`funding.py`)

Funding rate settlement modeling.

**Responsibilities**:
- Load historical funding rates
- Apply discrete settlements (8h)
- Calculate funding costs/credits
- Support constant rate stress testing

### Spread Model (`spread.py`)

Bid-ask spread simulation.

**Responsibilities**:
- Conservative model (ATR-scaled)
- Historical data provider
- Entry gating based on spread
- Hybrid model support

### Reporting (`reporting.py`)

Performance metrics and output.

**Responsibilities**:
- Calculate Sharpe ratio
- Compute max drawdown
- Generate trade statistics
- Output CSV/JSON reports

---

## Execution Models

### Ideal Model

Fixed slippage, all orders fill immediately.

```yaml
backtest:
  execution_model: ideal
  slippage_base_bps: 2.0
```

**Use Case**: Quick strategy iteration, upper-bound performance.

### Realistic Model

Variable slippage, stochastic fills, funding settlements.

```yaml
backtest:
  execution_model: realistic
  slippage_base_bps: 2.0
  slippage_atr_scale: 1.0
  fill_probability_model: true
  random_seed: 42
```

**Use Case**: Production-like performance estimation.

---

## Slippage Model

### Fixed Slippage

```
slippage = slippage_base_bps * 0.0001
executed_price = order_price * (1 + slippage * direction)
```

### ATR-Scaled Slippage

```
atr_component = atr_pct * slippage_atr_scale
slippage = (slippage_base_bps * 0.0001) + (atr_component * 0.01)
executed_price = order_price * (1 + slippage * direction)
```

**direction**: +1 for buys, -1 for sells

---

## Fill Probability Model

Determines whether limit orders fill based on price movement.

### Algorithm

```python
def should_fill(order_price: float, bar: Bar, is_buy: bool) -> bool:
    if is_buy:
        # Buy order fills if low touched order price
        return bar.low <= order_price
    else:
        # Sell order fills if high touched order price
        return bar.high >= order_price
```

### Stochastic Component

When enabled, adds randomness:

```python
base_prob = 0.8  # Base fill probability
movement_factor = abs(close - open) / atr
fill_prob = base_prob * (1 + movement_factor * 0.2)
fills = random.random() < fill_prob
```

---

## Funding Model

### Discrete Settlements

Funding settles every 8 hours (00:00, 08:00, 16:00 UTC).

```python
funding_pnl = position_value * funding_rate * direction
# LONG + positive rate = pay
# LONG + negative rate = receive
# SHORT = opposite
```

### Settlement Times

```python
FUNDING_HOURS = [0, 8, 16]  # UTC

def is_funding_time(timestamp: datetime) -> bool:
    return timestamp.hour in FUNDING_HOURS and timestamp.minute == 0
```

---

## Spread Model

### Conservative Model

ATR-scaled spread estimation:

```python
spread_pct = spread_base_pct + (atr_pct * spread_atr_scale)
spread_bps = spread_pct * 100
```

### Entry Gating

```python
if spread_bps > max_spread_bps:
    # Reject entry
    return None
```

---

## Event Types (Backtest)

### BarEvent

OHLCV bar close event.

```python
@dataclass(frozen=True)
class BarEvent:
    symbol: str
    interval: str
    timestamp: datetime    # Bar close time
    open: float
    high: float
    low: float
    close: float
    volume: float
    sequence: int = 0
```

### FundingEvent

Funding rate settlement.

```python
@dataclass(frozen=True)
class FundingEvent:
    symbol: str
    funding_time: datetime
    rate: float           # Decimal (0.0001 = 0.01%)
    mark_price: float | None
```

### SpreadEvent

Bid-ask spread snapshot.

```python
@dataclass(frozen=True)
class SpreadEvent:
    symbol: str
    timestamp: datetime
    bid: float
    ask: float
    spread_bps: float
```

---

## Event Priority

Events are processed in deterministic order:

| Priority | Event Type | Description |
|----------|-----------|-------------|
| 1 | FUNDING | Funding settlement |
| 2 | BAR_CLOSE | Bar data |
| 3 | SPREAD | Spread snapshot |
| 4 | STRATEGY | Signal generation |
| 5 | RISK | Risk evaluation |
| 6 | EXECUTION | Trade fills |

---

## Performance Metrics

### Sharpe Ratio

```python
returns = daily_equity.pct_change()
sharpe = returns.mean() / returns.std() * sqrt(252)
```

### Max Drawdown

```python
peak = equity.expanding().max()
drawdown = (equity - peak) / peak
max_drawdown = drawdown.min()
```

### Win Rate

```python
win_rate = winning_trades / total_trades
```

### Profit Factor

```python
profit_factor = gross_profits / abs(gross_losses)
```

### Average Trade Metrics

- Average holding time
- Average P&L per trade
- Average slippage

---

## Data Requirements

### Minimum Data

- OHLCV bars at entry timeframe (e.g., 4h)
- Sufficient history for indicators (200+ bars)

### Optional Data

- Funding rates (for funding model)
- Spread data (for spread model)
- Exchange info (for symbol rules)

---

## Reproducibility

### Random Seed

```yaml
backtest:
  random_seed: 42
```

Same seed = identical results for:
- Fill probability decisions
- Slippage variation
- Partial fill amounts

### Deterministic Requirements

1. Same data files
2. Same configuration
3. Same random seed
4. Same event ordering

---

## Limitations

1. **No order book depth**: Uses bar OHLCV only
2. **No market impact**: Assumes orders don't move market
3. **Simplified funding**: Discrete settlements only
4. **No latency**: Instant execution within bar
5. **No partial data**: Requires complete bars

---

## Related Documentation

- [Running Backtests](02_RunningBacktests.md) - Usage guide
- [Configuration Reference](../04_reference/01_Configuration.md) - Backtest config
- [CLI Tools](../04_reference/04_CLITools.md) - Backtest CLI
- [Data Schemas](../04_reference/03_DataSchemas.md) - Data formats
