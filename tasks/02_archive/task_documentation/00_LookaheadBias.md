# 00_LookaheadBias.md

## Task Summary
Task 00 — Make Backtests Match Live (No Lookahead)

## What Was Implemented
The backtester already had lookahead bias prevention implemented via the `_get_daily_at_time()` method.

### Key Implementation: `src/backtester/engine.py:Backtester._get_daily_at_time()`

This method ensures daily candles are derived point-in-time from the 4h series:

```python
def _get_daily_at_time(
    self, fourh: pd.DataFrame, current_time: pd.Timestamp
) -> pd.DataFrame:
    """Resample 4h to daily using only data available at current_time (no lookahead).

    At 4h candle close time `t`, use daily candles with close time < t,
    OR <= t only when t is a daily close (00:00 UTC).

    This prevents lookahead bias by ensuring daily features at time T
    never depend on 4h bars after T.
    """
    is_daily_close = current_time.hour == 0 and current_time.minute == 0

    if is_daily_close:
        # Include current bar if it closes the daily candle
        fourh_subset = fourh.loc[:current_time]
    else:
        # Use only 4h bars up to the most recent daily close (00:00 UTC today)
        cutoff = current_time.normalize()
        if cutoff <= fourh.index.min():
            return pd.DataFrame()
        fourh_subset = fourh.loc[:cutoff]
```

### Test Coverage: `tests/test_backtester_lookahead.py`

5 tests verify no lookahead bias:
- `test_daily_features_independent_of_future_4h_bars` - Main acceptance test
- `test_daily_bar_excludes_future_4h_candles`
- `test_daily_bar_at_daily_close`
- `test_daily_bar_uses_correct_4h_candles`
- `test_no_lookahead_with_varying_data_lengths`

### Documentation: `docs/backtesting.md`

Section "Lookahead Bias Prevention (Daily Trend)" documents the approach.

## Changes Made During Verification

1. **Fixed bug at line 180**: `hours_since_entry` → `hrs_since_entry` (undefined variable)

2. **Fixed line length issues** (lines 294-296, 365-366):
   - Reformatted slippage calculation
   - Reformatted execution metrics calculation

3. **Fixed return type annotation** (line 510):
   - Added `TradingState` to imports at module level
   - Added return type annotation to `_mock_state()` method

## Verification Results

| Check | Status |
|-------|--------|
| `pytest tests/test_backtester_lookahead.py` | ✅ 5 passed |
| `ruff check src/backtester/engine.py` | ✅ Pass |
| `mypy src/backtester/engine.py` | ✅ Pass (no engine errors) |
| Full test suite | ⚠️ 76 passed, 2 pre-existing failures |

The 2 pre-existing test failures are in execution simulation tests (`test_market_order_penalty`, `test_aggressive_limit_high_prob`), unrelated to lookahead bias.

## Files
- **Implementation**: `src/backtester/engine.py`
- **Tests**: `tests/test_backtester_lookahead.py`
- **Documentation**: `docs/backtesting.md`
- **Task spec**: `tasks/02_archive/task-00.md`
