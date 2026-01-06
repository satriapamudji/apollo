# Task 04: Factorized Scoring + Regime Filter (Anti-Chop)

## What Was Done

### 1. Added ScoringConfig to `src/config/settings.py`

**Before:** Factor weights were hardcoded in `ScoringEngine.__init__`:
```python
self.weights = {
    "trend": 0.35,
    "volatility": 0.15,
    "entry_quality": 0.25,
    "funding": 0.10,
    "news": 0.15,
}
```

**After:** Added config-driven scoring configuration:
```python
class FactorWeightsConfig(BaseModel):
    trend: float = Field(default=0.35, ge=0.0, le=1.0)
    volatility: float = Field(default=0.15, ge=0.0, le=1.0)
    entry_quality: float = Field(default=0.25, ge=0.0, le=1.0)
    funding: float = Field(default=0.10, ge=0.0, le=1.0)
    news: float = Field(default=0.15, ge=0.0, le=1.0)
    liquidity: float = Field(default=0.0, ge=0.0, le=1.0)

class ScoringConfig(BaseModel):
    enabled: bool = True
    threshold: float = Field(default=0.55, ge=0.3, le=0.95)
    factors: FactorWeightsConfig = Field(default_factory=FactorWeightsConfig)
```

### 2. Refactored ScoringEngine (`src/strategy/scoring.py`)

**Before:** Single monolithic `ScoringEngine` class with all factor logic inline.

**After:** Modular factor architecture with separate classes:
- `TrendFactor`: EMA trend alignment and momentum scoring
- `VolatilityFactor`: ATR-based volatility regime scoring
- `EntryQualityFactor`: Entry distance quality (0.5-1.0 ATR sweet spot)
- `FundingFactor`: Funding rate penalty for adverse positions
- `NewsFactor`: News sentiment modifier
- `LiquidityFactor`: Spread/liquidity scoring (new, default weight 0.0)

**CompositeScore now includes `liquidity_score`** for full transparency.

### 3. Enhanced RegimeClassifier (`src/strategy/regime.py`)

**Before:** Only ADX + Choppiness Index for regime detection.

**After:** Added `VolatilityRegimeType` enum and volatility regime detection:
- `VolatilityRegimeType.CONTRACTION`: Volatility decreasing (potential breakout setup)
- `VolatilityRegimeType.NORMAL`: Normal volatility
- `VolatilityRegimeType.EXPANSION`: Volatility increasing (potential trend)

New config options in `RegimeConfig`:
```python
volatility_regime_enabled: bool = False
volatility_contraction_threshold: float = Field(default=0.5, ge=0.1, le=1.0)
volatility_expansion_threshold: float = Field(default=2.0, ge=1.0, le=5.0)
```

### 4. Updated SignalGenerator (`src/strategy/signals.py`)

- Now wires `ScoringConfig` from `StrategyConfig` to `ScoringEngine`
- Uses `config.scoring.threshold` for entry quality gate

### 5. Updated config.yaml

Added `scoring` and `regime` sections:
```yaml
strategy:
  scoring:
    enabled: true
    threshold: 0.55
    factors:
      trend: 0.35
      volatility: 0.15
      entry_quality: 0.25
      funding: 0.10
      news: 0.15
      liquidity: 0.0
regime:
  enabled: true
  volatility_regime_enabled: false
  volatility_contraction_threshold: 0.5
  volatility_expansion_threshold: 2.0
```

### 6. Updated Thinking Logger (`src/monitoring/thinking_log.py`)

Added `liquidity` field to score output and `volatility_regime` fields to regime output for full auditability.

## Why These Changes

1. **Config-driven weights**: Allows tuning factor importance without code changes
2. **Modular factor classes**: Easier to add/modify factors, better testability
3. **Liquidity factor**: Prepares for spread-based entry quality filtering
4. **Volatility regime**: Enables detection of contraction/expansion patterns for better timing
5. **Deterministic scoring**: All factors and weights are now visible in thinking logs

## Files Modified

| File | Changes |
|------|---------|
| `src/config/settings.py` | Added `FactorWeightsConfig`, `ScoringConfig`, enhanced `RegimeConfig` |
| `src/strategy/scoring.py` | Refactored into modular factor classes, added `liquidity_score` |
| `src/strategy/regime.py` | Added `VolatilityRegimeType`, volatility regime detection |
| `src/strategy/signals.py` | Wired config to scoring engine |
| `src/monitoring/thinking_log.py` | Added `liquidity` and volatility regime fields |
| `config.yaml` | Added `scoring` and `regime` configuration sections |

## Testing

All scoring tests pass. The pre-existing test failures in `test_backtester_reporting.py` and `test_backtester_execution.py` are unrelated to these changes.
