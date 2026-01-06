# Task 26 - Book Ticker Integration for Real-Time Spread Check

## Overview

Added pre-trade spread validation using Binance book ticker endpoint to reject entries when bid-ask spread is too wide, with dynamic ATR-based thresholds that adapt to market volatility.

## What Was Before

- Spread check existed with a fixed `max_spread_pct` threshold (default 0.3%)
- No distinction between calm vs volatile markets
- No spread data recorded in ORDER_PLACED events
- No dedicated spread metrics for monitoring

## What Changed

### 1. REST Client (`src/connectors/rest_client.py`)

Added `get_spread_pct()` convenience method:
```python
async def get_spread_pct(self, symbol: str) -> float:
    """Return current spread as percentage of mid price."""
    ticker = await self.get_book_ticker(symbol)
    bid = float(ticker["bidPrice"])
    ask = float(ticker["askPrice"])
    mid = (bid + ask) / 2
    return ((ask - bid) / mid) * 100 if mid > 0 else 0.0
```

### 2. Configuration (`src/config/settings.py`)

Added dynamic spread threshold settings to `ExecutionConfig`:
- `use_dynamic_spread_threshold: bool = True` - Enable/disable dynamic thresholds
- `spread_threshold_calm_pct: float = 0.05` - Threshold when ATR < 2%
- `spread_threshold_normal_pct: float = 0.10` - Threshold when ATR 2-4%
- `spread_threshold_volatile_pct: float = 0.20` - Threshold when ATR > 4%
- `atr_calm_threshold: float = 2.0` - ATR% below this = calm market
- `atr_volatile_threshold: float = 4.0` - ATR% above this = volatile market

### 3. Metrics (`src/monitoring/metrics.py`)

Added spread-specific metrics:
- `trade_spread_pct` - Histogram tracking observed spreads with buckets [0.01, 0.02, 0.05, 0.1, 0.15, 0.2, 0.3, 0.5, 1.0]
- `spread_rejections_total` - Counter for trades rejected due to wide spread

### 4. Execution Engine (`src/execution/engine.py`)

Enhanced `_check_spread_slippage()` method:
- Accepts optional `atr` parameter for dynamic threshold calculation
- Calculates ATR as percentage of price: `atr_pct = (atr / price) * 100`
- Selects appropriate threshold based on market regime:
  - Calm (ATR% < 2%): Use tighter spread threshold
  - Normal (ATR% 2-4%): Use normal threshold
  - Volatile (ATR% > 4%): Use wider threshold
- Records spread metrics via injected `Metrics` instance
- Stores spread data in `_last_spread_data` for inclusion in events

Added `set_metrics()` method for deferred metrics injection:
```python
def set_metrics(self, metrics: Metrics) -> None:
    """Set metrics instance for spread tracking."""
    self._metrics = metrics
```

Updated events:
- RISK_REJECTED now includes spread data when rejection is due to spread
- ORDER_PLACED (for entry orders) includes spread data captured at check time

### 5. Main (`src/main.py`)

Added metrics injection:
```python
execution_engine.set_metrics(metrics)
```

### 6. Tests (`tests/test_spread_check.py`)

Comprehensive test coverage:
- `TestGetSpreadPct` - Spread percentage calculation
- `TestDynamicSpreadThreshold` - Calm/normal/volatile market thresholds
- `TestSpreadRejection` - Rejection events and spread data
- `TestMetricsRecording` - Metrics recording
- `TestFixedThresholdFallback` - Fixed threshold when dynamic disabled

## Reasoning

Wide spreads are hidden killers, especially during volatility. This implementation:

1. **Adapts to market conditions**: Tighter thresholds in calm markets (where spreads should be tight) and looser thresholds during volatility (where wider spreads are normal)

2. **Uses ATR as volatility proxy**: ATR already computed for each trade proposal, making it a natural choice for volatility measurement

3. **Provides observability**: Spread metrics allow monitoring of spread patterns over time and correlation with rejection rates

4. **Includes spread in events**: Spread data in ORDER_PLACED events enables post-trade analysis of execution quality

## Configuration Example

```yaml
execution:
  use_dynamic_spread_threshold: true
  spread_threshold_calm_pct: 0.05    # 5 bps in calm markets
  spread_threshold_normal_pct: 0.10  # 10 bps normally
  spread_threshold_volatile_pct: 0.20  # 20 bps in volatile markets
  atr_calm_threshold: 2.0   # ATR < 2% = calm
  atr_volatile_threshold: 4.0  # ATR > 4% = volatile
```

To use fixed threshold instead:
```yaml
execution:
  use_dynamic_spread_threshold: false
  max_spread_pct: 0.3  # Fixed 30 bps threshold
```
