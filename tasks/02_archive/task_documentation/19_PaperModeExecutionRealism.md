# Task 19 - Paper Mode Execution Realism

## Summary

Integrated realistic execution simulation into paper trading mode so that paper mode predicts live outcomes (fill rate, slippage, fees) instead of optimistic instant fills.

## What Changed

### Before
- Paper mode assumed instant fills at exact requested price
- No slippage modeling
- No fill probability (all orders always filled)
- No fee calculations
- Overly optimistic profitability estimates that hide execution problems

### After
- Uses live `bookTicker` data for spread awareness
- Variable slippage based on ATR, spread, and volatility regime
- Probabilistic fill rates for limit orders (30-85% depending on price distance)
- Partial fill simulation (configurable rate)
- Maker/taker fee calculations
- Events include `simulated: true`, `slippage_bps`, and `fees` metadata
- Paper mode can now produce "no fill" outcomes matching live behavior

## Implementation Details

### Files Modified

1. **`src/config/settings.py`**
   - Added `PaperSimConfig` class with:
     - `enabled: bool` (default: True) - toggle realistic simulation
     - `slippage_base_bps: float` (default: 2.0)
     - `slippage_atr_scale: float` (default: 1.0)
     - `maker_fee_pct: float` (default: 0.02)
     - `taker_fee_pct: float` (default: 0.04)
     - `random_seed: int | None` - for reproducibility
     - `partial_fill_rate: float` (default: 0.20)
     - `use_live_book_ticker: bool` (default: True)
     - `book_ticker_cache_seconds: float` (default: 1.0)
   - Added `paper_sim: PaperSimConfig` to `Settings` class

2. **`src/execution/paper_simulator.py`**
   - Updated to use `PaperSimConfig.book_ticker_cache_seconds`
   - Updated to use `PaperSimConfig.partial_fill_rate`

3. **`src/execution/engine.py`**
   - Added import for `PaperSimConfig` and `PaperExecutionSimulator`
   - Initialize `PaperExecutionSimulator` in `__init__` when paper mode + simulation enabled
   - Modified `execute_entry()`:
     - Calls `_paper_simulator.simulate_fill()` for realistic execution
     - Handles no-fill outcomes (emits `ORDER_EXPIRED`)
     - Handles partial fills (emits `ORDER_PARTIAL_FILL`)
     - Includes simulation metadata in `ORDER_FILLED` events
   - Modified `execute_exit()`:
     - Calls `_paper_simulator.simulate_fill()` with market order slippage
     - Includes simulation metadata in `POSITION_CLOSED` events
   - Fixed pre-existing type errors:
     - `Exception.response` access now uses `getattr()`
     - `Decimal.as_tuple().exponent` now handles string literals

4. **`tests/test_paper_simulator_integration.py`** (new)
   - 11 tests covering:
     - Engine initializes simulator in paper mode
     - Engine skips simulator when disabled
     - Market orders always fill with slippage
     - Limit orders fill immediately when price through market
     - Limit orders have probabilistic fills
     - Entry fills include simulation metadata
     - Exit fills include slippage
     - Slippage increases with volatility
     - Market orders have more slippage than limit
     - Maker/taker fee calculations

### Slippage Model

Uses the existing `PaperExecutionSimulator.estimate_slippage()`:
```
base_slippage = base_bps / 10000
atr_component = (atr_pct / 100) * atr_scale
spread_component = spread_pct / 200  # Half spread
regime_multiplier = {LOW: 0.5, NORMAL: 1.0, HIGH: 2.0}
limit_slippage = (base + atr + spread) * regime
market_slippage = limit_slippage + 0.0003  # +3 bps penalty
```

### Fill Probability Model

Uses the existing `PaperExecutionSimulator.estimate_fill_probability()`:
- Immediate fill if limit price through market (buy >= ask, sell <= bid)
- Distance-based probability: aggressive (85%) to passive (15%)
- Time bonus: +10% per bar (capped at 40%)
- Volatility bonus: +5% to +15% for high ATR
- Spread penalty: wide spreads reduce fill probability

### Event Metadata

Simulated events include:
```json
{
  "simulated": true,
  "slippage_bps": 4.5,
  "fees": 0.08
}
```

### Configuration

In `config.yaml`:
```yaml
paper_sim:
  enabled: true
  slippage_base_bps: 2.0
  slippage_atr_scale: 1.0
  maker_fee_pct: 0.02
  taker_fee_pct: 0.04
  random_seed: 42  # For reproducibility
  partial_fill_rate: 0.20
  use_live_book_ticker: true
  book_ticker_cache_seconds: 1.0
```

## Reasoning

1. **Execution Reality**: Paper mode that assumes 100% fills at exact prices massively overstates profitability. Real Binance futures trading shows 30-60% fill rates on limit orders during volatile moves.

2. **Live Spread Data**: Using `bookTicker` data provides realistic spread awareness without additional data costs.

3. **Slippage Awareness**: Fixed slippage ignores market conditions. Slippage should scale with volatility (ATR), spread width, and order type.

4. **Strategy Validation**: If a strategy shows profits with instant fills but losses with realistic simulation, the "edge" is execution-dependent (a red flag before going live).

5. **Fees Matter**: Even small fees compound over many trades. Including them in paper mode gives realistic P&L estimates.

## Acceptance Criteria Verification

- ✅ Paper mode can produce "no fill" outcomes (`ORDER_EXPIRED` events)
- ✅ Order lifecycles identical to live (ORDER_PLACED → ORDER_EXPIRED or ORDER_FILLED)
- ✅ Uses live `bookTicker` for spread data
- ✅ Models slippage as function of spread + ATR + volatility regime
- ✅ Applies maker/taker fees (configurable)
- ✅ Logs simulated fills with same ledger events as real fills
- ✅ All 233 tests pass
- ✅ Type-check passes (0 errors)

## Testing

```bash
pytest tests/test_paper_simulator_integration.py -v
```

Validates:
- Simulator initialization in paper mode
- Market order fills with slippage
- Limit order immediate fills when through market
- Limit order probabilistic fills
- Entry/exit metadata includes simulation fields
- Slippage scaling with volatility
- Market vs limit slippage difference
- Maker/taker fee calculations
