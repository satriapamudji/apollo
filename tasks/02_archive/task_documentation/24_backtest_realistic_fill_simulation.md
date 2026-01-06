# Task 24 - Backtest Realistic Fill Simulation

## Summary

Implemented realistic execution simulation for backtesting including variable slippage, partial fills, and limit order fill probability models.

## What Changed

### Before
- Backtester used fixed slippage (0.05%) regardless of market conditions
- All orders filled at 100% rate
- Instant fills at exact desired price
- No partial fill modeling
- Overly optimistic profitability estimates

### After
- Variable slippage based on ATR and volatility regime
- Probabilistic fill rates for limit orders (30-80% depending on aggressiveness)
- Partial fill simulation (50% chance when order fills)
- Reproducible results with random seed
- More realistic profitability estimates

## Implementation Details

### Files Created/Modified

1. **`src/backtester/execution_sim.py`** (new)
   - `VolatilityRegime` enum (LOW, NORMAL, HIGH)
   - `detect_volatility_regime()` - classifies ATR% into regimes
   - `estimate_slippage()` - calculates variable slippage based on ATR, regime, order type
   - `estimate_fill_probability()` - determines fill probability for limit orders
   - `ExecutionSimulator` class - orchestrates fill simulation with reproducible random state

2. **`src/backtester/engine.py`**
   - `Backtester.__init__()` now accepts `execution_model` and `random_seed` parameters
   - `Backtester.run()` branches on `execution_model` ("ideal" vs "realistic")
   - Tracks new metrics: `fill_rate`, `avg_slippage_bps`, `missed_entries`, `partial_fills`
   - `Trade` dataclass extended with `slippage_bps` and `is_partial_fill` fields
   - `BacktestResult` dataclass extended with execution simulation metrics

3. **`src/config/settings.py`**
   - `BacktestConfig` class with:
     - `execution_model: Literal["ideal", "realistic"]` (default: "ideal")
     - `slippage_base_bps: float` (default: 2.0)
     - `slippage_atr_scale: float` (default: 1.0)
     - `fill_probability_model: bool` (default: True)
     - `random_seed: int | None` (default: None)

4. **`tests/test_backtester_execution.py`** (new)
   - 17 tests covering all execution simulation functions
   - Tests for volatility regime detection, slippage estimation, fill probability, and ExecutionSimulator class
   - Reproducibility tests with random seeds

### Slippage Model

Formula:
```
base_slippage = base_bps / 10000
atr_component = (atr_pct / 100) * atr_scale
regime_multiplier = {LOW: 0.5, NORMAL: 1.0, HIGH: 2.0}
limit_slippage = (base_slippage + atr_component) * regime_multiplier
market_slippage = limit_slippage + 0.0003  # +3 bps market order penalty
```

### Fill Probability Model

- Aggressive limit (within 5 bps): ~80% base fill rate
- Medium limit (5-10 bps): ~60% base fill rate  
- Conservative limit (10-20 bps): ~40% base fill rate
- Passive limit (20+ bps): ~20% base fill rate
- Time bonus: +15% per bar held (capped)
- Volatility bonus: +5% to +20% for higher ATR%

### Configuration

In `config.yaml`:
```yaml
backtest:
  execution_model: "realistic"  # or "ideal" for legacy behavior
  slippage_base_bps: 2
  slippage_atr_scale: 1.0
  fill_probability_model: true
  random_seed: 42  # For reproducibility
```

## Reasoning

1. **Realistic Expectations**: Backtests with 100% fill rates massively overstate profitability. Real trading on Binance futures shows 30-50% fill rates on aggressive limits during volatile moves.

2. **Variable Slippage**: Fixed slippage ignores market conditions. Slippage should scale with volatility (ATR) and market regime.

3. **Reproducibility**: Random fills are necessary for modeling but must be reproducible for debugging and comparison.

4. **Strategy Validation**: If a strategy shows profits with ideal execution but losses with realistic execution, the "edge" is execution-dependent (a red flag).

## Acceptance Criteria Verification

- ✅ Backtest with `execution_model: realistic` shows 60-80% fill rate
- ✅ Variable slippage scales with ATR and regime
- ✅ Missed entries are logged and counted
- ✅ Results are reproducible with same seed
- ✅ All 17 execution simulation tests pass
- ✅ All 136 total tests pass

## Testing

```bash
pytest tests/test_backtester_execution.py -v
```

Validates:
- Volatility regime detection boundaries
- Slippage calculation with ATR scaling and regime multipliers
- Market order penalty (+3 bps)
- Fill probability distance decay
- Time and volatility bonuses
- ExecutionSimulator reproducibility with seeds
- Partial fill probability (~50% of fills)
