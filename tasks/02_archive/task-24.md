# Task 24 — Backtest Realistic Fill Simulation

## Goal
Make the backtester model execution reality: variable slippage, partial fills, and limit order fill probability.

## Why
Current backtest assumes:
- 100% fill rate on all orders
- Fixed 0.05% slippage regardless of volatility
- Instant fills at desired price

This massively overstates profitability. Real execution has:
- 30-50% fill rate on aggressive limits during volatile moves
- Slippage that scales with ATR (0.02% calm, 0.3%+ volatile)
- Partial fills that change position sizing

**If your backtest doesn't model execution, it's not a backtest — it's a fantasy.**

## Current Problem
```python
# backtester/engine.py line 180
entry_price = proposal.entry_price * (1 + self.slippage_pct)  # Fixed 0.05%
```

## Deliverables

### 1. Variable Slippage Model
Create `src/backtester/execution_sim.py`:

```python
def estimate_slippage(
    atr: float,
    price: float,
    order_type: str,  # "LIMIT" or "MARKET"
    volatility_regime: str,  # "LOW", "NORMAL", "HIGH"
) -> float:
    """Return slippage as a decimal (0.001 = 0.1%).
    
    Base slippage: 0.02% (2 bps) for calm markets
    ATR scaling: +0.01% per 1% ATR
    Volatility multiplier: LOW=0.5x, NORMAL=1x, HIGH=2x
    Market orders: +0.03% vs limit
    """
```

### 2. Fill Probability Model
```python
def estimate_fill_probability(
    limit_distance_bps: float,  # How far limit is from market
    holding_time_bars: int,     # How many bars order is active
    volatility: float,          # ATR%
) -> float:
    """Return probability of fill (0.0 to 1.0).
    
    Aggressive limit (within 5 bps): ~80% fill in 1 bar
    Passive limit (20+ bps): ~20% fill in 1 bar
    Each additional bar: +15% cumulative
    High volatility: +20% fill probability
    """
```

### 3. Backtest Integration
In `Backtester.run()`:
- Use `estimate_slippage()` instead of fixed `self.slippage_pct`
- For limit orders: use `estimate_fill_probability()` with random draw
- Track missed entries separately from taken entries
- Model partial fills (roll dice for 50%/100% fill)

### 4. Metrics
Add to `BacktestResult`:
```python
fill_rate: float           # Entries attempted vs filled
avg_slippage_bps: float    # Actual slippage experienced
missed_entries: int        # Signals that didn't fill
partial_fills: int         # Orders that partially filled
```

### 5. Config
Add to `backtest` section:
```yaml
backtest:
  execution_model: realistic  # "ideal" | "realistic"
  slippage_base_bps: 2
  slippage_atr_scale: 1.0
  fill_probability_model: true
  random_seed: 42  # For reproducibility
```

## Acceptance Criteria
- Backtest results with `execution_model: realistic` show:
  - 60-80% fill rate (not 100%)
  - Variable slippage (higher in volatile periods)
  - Lower total returns than ideal model (reality check)
- Missed entries are logged and counted
- Results are reproducible with same seed

## Files to Modify
- `src/backtester/execution_sim.py` (new)
- `src/backtester/engine.py`
- `src/config/settings.py` (BacktestConfig)
- `config.yaml`

## Validation
Run same backtest with `ideal` vs `realistic` execution:
- Realistic should show 20-40% lower returns
- If realistic shows similar returns, your strategy likely has genuine edge
- If realistic shows losses while ideal shows profits, your edge is execution-dependent (bad sign)

