# Task 22: Volume Confirmation for Entries

## Summary

Added volume confirmation to filter out low-conviction breakout and pullback entry signals, reducing fakeout trades during low-liquidity periods.

## What Existed Before

- Entry signals (breakout and pullback) relied solely on price action and technical indicators (EMA, RSI, ATR)
- No volume analysis was performed
- Low-volume breakouts had same validity as high-volume ones, leading to potential fakeout entries

## What Was Changed

### New Volume Indicators (`src/features/indicators.py`)
- `calculate_volume_sma(volume, period)` - Simple moving average of volume
- `calculate_volume_ratio(volume, period)` - Current volume as ratio of SMA (1.0 = average, 2.0 = 2x average)

### Feature Pipeline (`src/features/pipeline.py`)
- Computes `volume_sma` and `volume_ratio` columns when volume data is available

### Configuration (`src/config/settings.py`, `config.yaml`)
- `IndicatorConfig.volume_sma_period: int = 20` - Lookback for volume SMA
- `EntryConfig.volume_breakout_threshold: float = 1.5` - Minimum volume ratio for breakouts
- `EntryConfig.volume_confirmation_enabled: bool = True` - Feature toggle
- `FactorWeightsConfig.volume: float = 0.0` - Optional score contribution

### Volume Scoring (`src/strategy/scoring.py`)
- New `VolumeFactor` class with tiered scoring:
  - volume_ratio >= 2.0 → score 1.0 (strong)
  - volume_ratio 1.5-2.0 → score 0.7 (good)
  - volume_ratio 1.0-1.5 → score 0.4 (weak)
  - volume_ratio < 1.0 → score 0.0 (no confirmation)
- `CompositeScore` dataclass includes `volume_score` field
- `ScoringEngine.compute()` accepts `volume_ratio` parameter

### Entry Gating (`src/strategy/signals.py`)
- `_breakout_entry()` blocks entries when `volume_ratio < volume_breakout_threshold`
- `_pullback_entry()` requires current bar volume > previous 3 bars average
- Signal dataclass includes `volume_ratio` field

### Thinking Log (`src/monitoring/thinking_log.py`)
- Logs `volume_ratio` in signal records
- Includes `volume` score in score breakdown

### Tests (`tests/test_volume_indicators.py`)
- 12 comprehensive tests for indicators, factor, and scoring integration

## Key Design Decisions

1. **Volume gating is separate from scoring** - Entry is blocked if volume is too low, regardless of composite score
2. **Breakout vs Pullback use different volume logic**:
   - Breakout: requires `volume_ratio >= threshold` (default 1.5x)
   - Pullback: requires current bar volume > previous 3 bars average (recovery confirmation)
3. **Volume weight defaults to 0.0** - Primary filtering via gating, not score contribution
4. **Feature is toggleable** - `volume_confirmation_enabled` in config

## Reasoning

- Low-volume breakouts are often fakeouts that reverse quickly
- Volume confirmation is a classic technical analysis principle
- Reduces trade frequency but improves win rate by filtering low-conviction entries
- Pullback entries use relative volume (vs recent bars) since they occur during consolidation periods with naturally lower volume

## Files Modified

- `src/features/indicators.py`
- `src/features/pipeline.py`
- `src/features/__init__.py`
- `src/config/settings.py`
- `src/strategy/scoring.py`
- `src/strategy/signals.py`
- `src/monitoring/thinking_log.py`
- `config.yaml`
- `tests/test_volume_indicators.py` (new)
