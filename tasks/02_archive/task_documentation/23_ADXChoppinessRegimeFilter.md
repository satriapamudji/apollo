# Task 23 - ADX + Choppiness Index Regime Filter

## Summary

Implemented regime detection layer that blocks entries during choppy/ranging market conditions using ADX (Average Directional Index) and Choppiness Index. The implementation was ~95% complete in the codebase; this task fixed backtester integration and added comprehensive tests.

## What Changed

### Before
- Core regime implementation existed but was incomplete:
  - `src/features/indicators.py` had `calculate_adx()` and `calculate_choppiness_index()`
  - `src/strategy/regime.py` had full `RegimeClassifier` implementation
  - `src/strategy/signals.py` had entry blocking on `regime.blocks_entry`
  - `src/main.py` passed `settings.regime` to `SignalGenerator`
- **Gap 1**: Backtester (`src/backtester/engine.py`) did NOT accept `RegimeConfig`, so backtests ignored regime filtering entirely
- **Gap 2**: No test coverage for the regime system

### After
- Backtester now accepts and uses `RegimeConfig`
- Backtests produce results consistent with live trading (entries blocked in choppy regimes)
- Comprehensive test suite validates all regime components
- 23 new tests covering ADX, Choppiness Index, RegimeClassifier, signal gating, and volatility regime

## Implementation Details

### Files Modified

1. **`src/backtester/engine.py`**
   - Added `RegimeConfig` import
   - Added `regime_config: RegimeConfig | None = None` parameter to `Backtester.__init__()`
   - Now passes `regime_config` to `SignalGenerator`

2. **`src/backtester/runner.py`**
   - Added `regime_config=settings.regime` to `Backtester()` instantiation

### Files Created

3. **`tests/test_regime.py`** (new)
   - 23 comprehensive tests organized into 5 test classes:
     - `TestADXIndicator`: ADX calculation, value ranges (0-100), trending vs choppy detection
     - `TestChoppinessIndex`: CHOP calculation, value ranges, trending vs choppy detection
     - `TestRegimeClassifier`: TRENDING/CHOPPY/TRANSITIONAL classification, threshold behavior, edge cases
     - `TestSignalGeneratorRegimeGating`: Entry blocking in CHOPPY regime, regime info propagation
     - `TestVolatilityRegime`: Contraction/expansion/normal volatility detection

### Already Implemented (No Changes Needed)

- `src/features/indicators.py` - `calculate_adx()` and `calculate_choppiness_index()` 
- `src/features/pipeline.py` - Computes ADX and CHOP columns
- `src/strategy/regime.py` - Full `RegimeClassifier` with `RegimeType` enum, `RegimeInfo` dataclass
- `src/strategy/signals.py` - Entry blocking when `regime.blocks_entry == True`
- `src/config/settings.py` - `RegimeConfig` with all thresholds
- `config.yaml` - `regime:` section with enabled flag and thresholds
- `src/monitoring/thinking_log.py` - Regime logging to `logs/thinking.jsonl`
- `src/main.py` - Passes `settings.regime` to `SignalGenerator`

### Regime Classification Logic

```python
def classify(adx: float, chop: float) -> RegimeType:
    # TRENDING: Strong trend - ADX > 25 AND CHOP < 50
    # CHOPPY: Ranging market - ADX < 20 OR CHOP > 61.8
    # TRANSITIONAL: Ambiguous - everything else
```

### Configuration

In `config.yaml`:
```yaml
regime:
  enabled: true
  adx_period: 14
  chop_period: 14
  adx_trending_threshold: 25
  adx_ranging_threshold: 20
  chop_trending_threshold: 50
  chop_ranging_threshold: 61.8
```

## Reasoning

1. **Trend-Following Failure Mode**: Markets only trend 30-40% of the time. Without regime filtering, EMA crossover systems generate constant false signals during the other 60-70%, leading to repeated stop-outs.

2. **ADX vs EMA Alignment**: The original `_determine_trend()` only checked EMA relationship, not trend strength. EMAs can show "uptrend" during choppy sideways markets. ADX measures actual trend strength.

3. **Choppiness Index Complement**: ADX measures trend strength; Choppiness Index measures consolidation. Combined, they provide robust regime detection.

4. **Backtester Parity**: Without regime filtering in backtests, backtest results would not match live trading behavior, making backtests misleading.

## Acceptance Criteria Verification

- ✅ Entries blocked when ADX < 20 OR Choppiness > 61.8
- ✅ Regime logged for every signal evaluation (`logs/thinking.jsonl`)
- ✅ Backtester uses regime filtering (matches live behavior)
- ✅ Comprehensive test coverage (23 tests)
- ✅ All 159 tests pass

## Testing

```bash
pytest tests/test_regime.py -v
```

Validates:
- ADX calculation correctness and value bounds
- Choppiness Index calculation correctness and value bounds
- RegimeClassifier threshold behavior for TRENDING/CHOPPY/TRANSITIONAL
- Signal generator blocking entries in CHOPPY regime
- Regime info propagation in signal output
- Volatility regime detection (contraction/expansion/normal)
