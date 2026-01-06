# Task 33 - Spread-Aware Execution Model

## Summary

Implemented spread modeling for the backtester, enabling entries to be rejected when spread exceeds `execution.max_spread_pct`, adding a realistic half-spread slippage floor, and including spread-at-entry diagnostics in results.

## What Changed

### Before
- Backtester had no concept of bid-ask spread
- Slippage direction was inferred from price comparison (incorrect for buy limits below market)
- `execution.max_spread_pct` config existed but was not enforced in backtests
- Realistic execution mode was incomplete without spread costs

### After
- Full spread modeling with `SpreadSnapshot` dataclass
- Two spread sources: `ConservativeSpreadModel` (ATR-scaled) and `HistoricalSpreadProvider` (real data)
- Entries rejected when `spread_pct > max_spread_pct`
- Half-spread floor for marketable orders (MARKET + aggressive limits within 5 bps)
- Explicit `side` parameter fixes slippage direction: BUY pays more, SELL receives less
- Spread-at-entry diagnostics in trade records and summary reports

## Implementation Details

### Files Created

1. **`src/backtester/spread.py`** (new)
   - `SpreadSnapshot` dataclass with computed properties: `mid`, `spread_pct`, `spread_decimal`, `is_modeled`
   - `SpreadModel` Protocol for spread providers
   - `ConservativeSpreadModel` - deterministic ATR-scaled spread estimation
   - `HistoricalSpreadProvider` - loads real spread data from CSV with forward-fill (no lookahead)
   - `HybridSpreadProvider` - tries historical first, falls back to model

2. **`src/tools/collect_spreads.py`** (new)
   - CLI tool to sample `GET /fapi/v1/ticker/bookTicker` periodically
   - Saves to `data/spreads/{symbol}_spreads.csv`
   - Usage: `python -m src.tools.collect_spreads --symbols BTCUSDT,ETHUSDT --interval 60 --duration 3600`

3. **`tests/test_backtester_spread.py`** (new)
   - 27 tests covering:
     - `SpreadSnapshot` calculations
     - `ConservativeSpreadModel` regime multipliers and ATR scaling
     - `HistoricalSpreadProvider` forward-fill and symbol filtering
     - `HybridSpreadProvider` fallback logic
     - Slippage direction (BUY increases price, SELL decreases price)
     - Half-spread floor for marketable orders

### Files Modified

1. **`src/backtester/execution_sim.py`**
   - Added `side: Literal["BUY", "SELL"]` parameter to `fill_order()` (fixes direction inference bug)
   - Added `spread_pct: float | None` parameter for spread-aware slippage floor
   - Implemented half-spread floor: `max(model_slippage, half_spread)` for marketable orders only
   - Slippage always adverse: BUY fills higher, SELL fills lower

2. **`src/backtester/engine.py`**
   - Added `spread_at_entry_pct: float` to `Trade` dataclass
   - Added `spread_rejections`, `avg_spread_at_entry_pct`, `spread_source` to `BacktestResult`
   - Added spread model initialization with auto-selection logic
   - Spread gating: rejects entries when `spread_pct > max_spread_pct`
   - Updated `fill_order()` calls to pass `side` and `spread_pct`

3. **`src/backtester/reporting.py`**
   - Added spread metrics to `compute_metrics()`
   - Added `spread_at_entry_pct` column to `write_trade_csv()`
   - Added "SPREAD ANALYSIS" section to `print_summary()` (only shown when spread model active)

4. **`src/config/settings.py`**
   - Added to `BacktestConfig`:
     - `spread_model: Literal["none", "conservative", "historical", "hybrid"]`
     - `spread_base_pct: float` (default: 0.01%)
     - `spread_atr_scale: float` (default: 0.5)
     - `max_spread_pct: float` (default: 0.1%)

5. **`src/backtester/data.py`**
   - Added `load_spread_csv()` function to load historical spread data

6. **`tests/test_backtester_execution.py`**
   - Fixed all `fill_order()` calls to include `side="BUY"` or `side="SELL"`

### Conservative Spread Model

Formula:
```
spread_pct = (base_spread_pct + atr_pct * atr_scale) * regime_multiplier

Regime multipliers:
- LOW volatility (ATR < 0.5%): 0.5x
- NORMAL volatility (ATR 0.5-1.5%): 1.0x
- HIGH volatility (ATR > 1.5%): 2.0x
```

### Half-Spread Floor Logic

For marketable orders (MARKET or LIMIT within 5 bps of market):
```python
half_spread_decimal = (spread_pct / 100) / 2
effective_slippage = max(model_slippage, half_spread_decimal)
```

This represents the minimum cost of crossing the bid-ask spread.

### Configuration

In `config.yaml`:
```yaml
backtest:
  execution_model: "realistic"
  spread_model: "conservative"  # or "none", "historical", "hybrid"
  spread_base_pct: 0.01
  spread_atr_scale: 0.5
  max_spread_pct: 0.1

execution:
  max_spread_pct: 0.1  # Used by both live and backtest
```

## Reasoning

1. **Spread Gating**: Wide spreads during volatility can erase expected edge. Rejecting entries when spread exceeds threshold prevents unprofitable trades.

2. **Half-Spread Floor**: The minimum cost to cross the spread is half the bid-ask spread. This should be the floor for slippage, not an additive term.

3. **Slippage Direction**: Previous inference from `proposal_price >= current_price` was incorrect for buy limits below market (common entry pattern). Explicit `side` parameter ensures slippage is always adverse.

4. **Modeled vs Historical**: The conservative model is clearly marked as modeled (`is_modeled=True`) to distinguish from real spread data. Modeled spreads should be interpreted with caution.

## Acceptance Criteria Verification

- ✅ Backtest enforces `execution.max_spread_pct` when spread model enabled
- ✅ Slippage direction is correct for BUY vs SELL (unit tested)
- ✅ Output includes `spread_rejections`, `avg_spread_at_entry_pct`, `spread_source`
- ✅ Half-spread floor applied for marketable orders
- ✅ All 44 spread/execution tests pass
- ✅ All 415 total tests pass

## Testing

```bash
# Run spread and execution tests
pytest tests/test_backtester_spread.py tests/test_backtester_execution.py -v

# Run full test suite
pytest
```

Validates:
- SpreadSnapshot mid/spread_pct calculations
- ConservativeSpreadModel regime multipliers and ATR scaling
- HistoricalSpreadProvider forward-fill (no lookahead bias)
- HybridSpreadProvider fallback logic
- Slippage direction: BUY increases fill price, SELL decreases fill price
- Half-spread floor for market orders and aggressive limits
- No floor for passive limits (> 5 bps)

## Forward Data Collection

To collect real spread data for future backtests:
```bash
python -m src.tools.collect_spreads --symbols BTCUSDT,ETHUSDT --interval 60 --duration 86400
```

This saves snapshots to `data/spreads/{symbol}_spreads.csv` for use with `HistoricalSpreadProvider`.
