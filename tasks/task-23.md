# Task 23 — Implement ADX + Choppiness Index Regime Filter

## Goal
Add a regime detection layer that blocks entries during choppy/ranging market conditions using ADX and Choppiness Index.

## Why
**This is the #1 reason trend-following strategies fail in crypto.** Markets trend only 30-40% of the time. During the other 60-70%, simple EMA/breakout systems generate constant false signals that get stopped out repeatedly.

Current system has NO regime filter. The `_determine_trend()` function only checks EMA alignment, which is insufficient — EMAs will show "uptrend" even in choppy markets where price oscillates around them.

## Current Problem
```python
# signals.py - only checks EMA relationship, not trend STRENGTH
if ema_fast > ema_slow and price > ema_slow and ema_fast > ema_fast_prev:
    return "UPTREND"
```

This triggers entries in sideways markets where EMAs happen to be stacked but price isn't actually trending.

## Deliverables

### 1. New Indicators
Add to `src/features/indicators.py`:

```python
def calculate_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average Directional Index - measures trend strength (0-100).
    ADX > 25 = trending, ADX < 20 = ranging/choppy.
    """
    # Implementation: +DI, -DI, then ADX smoothing

def calculate_choppiness_index(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Choppiness Index - measures market choppiness (0-100).
    CHOP < 38.2 = trending, CHOP > 61.8 = choppy/ranging.
    Formula: 100 * LOG10(SUM(ATR, n) / (Highest High - Lowest Low)) / LOG10(n)
    """
```

### 2. Pipeline Integration
Add to `FeaturePipeline.compute()`:
- `adx` (14-period ADX)
- `chop` (14-period Choppiness Index)

### 3. Regime Classification
Add `RegimeClassifier` in `src/strategy/regime.py`:
```python
class RegimeType(Enum):
    TRENDING = "TRENDING"
    CHOPPY = "CHOPPY"
    TRANSITIONAL = "TRANSITIONAL"

def classify_regime(adx: float, chop: float, config: RegimeConfig) -> RegimeType:
    # TRENDING: ADX > 25 AND CHOP < 50
    # CHOPPY: ADX < 20 OR CHOP > 61.8
    # TRANSITIONAL: everything else
```

### 4. Signal Gating
In `SignalGenerator.generate()`:
- Compute regime before signal generation
- Block entries when `regime == CHOPPY`
- Reduce position size by 50% when `regime == TRANSITIONAL`
- Full conviction only in `TRENDING` regime

### 5. Config
Add to `strategy`:
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

### 6. Logging
Add regime classification to `logs/thinking.jsonl`:
```json
{
  "regime": "TRENDING",
  "adx": 32.5,
  "chop": 42.1,
  "regime_blocks_entry": false
}
```

## Acceptance Criteria
- Entries are blocked when ADX < 20 OR Choppiness > 61.8
- Regime is logged for every signal evaluation
- Backtest shows significantly reduced trade count but improved expectancy
- Strategy only trades in confirmed trending regimes (~30% of candles)

## Files to Modify
- `src/features/indicators.py` (add ADX, Choppiness)
- `src/features/pipeline.py` (compute new indicators)
- `src/strategy/regime.py` (new file)
- `src/strategy/signals.py` (gate entries on regime)
- `src/config/settings.py` (RegimeConfig)
- `config.yaml`

## Research References
- ADX: Developed by Welles Wilder, measures trend strength not direction
- Choppiness Index: Developed by E.W. Dreiss, measures market consolidation
- Combined filter reduces false signals by 40-60% in typical crypto conditions

