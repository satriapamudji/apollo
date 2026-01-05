# Task 22 — Add Volume Confirmation for Entries

## Goal
Require volume confirmation for breakout and pullback entries to filter out low-conviction / fakeout signals.

## Why
Breakouts without volume are typically fakeouts. Crypto markets are full of low-volume probes that trigger technical levels but lack follow-through. Volume confirmation is one of the oldest and most reliable filters in technical trading.

## Current Problem
- No volume analysis exists anywhere in the codebase
- `FeaturePipeline` computes EMA, ATR, RSI but ignores volume
- Breakout entries trigger purely on price crossing 20-bar high/low

## Deliverables

### 1. Volume Indicators in Pipeline
Add to `src/features/indicators.py`:
```python
def calculate_volume_sma(volume: pd.Series, period: int) -> pd.Series:
    """Simple moving average of volume."""
    return volume.rolling(window=period, min_periods=period).mean()

def calculate_volume_ratio(volume: pd.Series, period: int) -> pd.Series:
    """Current volume as ratio of SMA (1.0 = average, 2.0 = 2x average)."""
    sma = calculate_volume_sma(volume, period)
    return volume / sma.replace(0, pd.NA)
```

### 2. Pipeline Integration
Add to `FeaturePipeline.compute()`:
- `volume_sma_20` (20-period volume SMA)
- `volume_ratio` (current bar volume / SMA)

### 3. Entry Gating
In `signals.py` for breakout entries:
- Require `volume_ratio >= config.volume_breakout_threshold` (default: 1.5)
- For pullback entries: require volume on recovery bar > previous 3 bars avg

### 4. Scoring Factor
Add `volume_score` to `ScoringEngine`:
- `volume_ratio >= 2.0` → score 1.0
- `volume_ratio 1.5-2.0` → score 0.7
- `volume_ratio 1.0-1.5` → score 0.4
- `volume_ratio < 1.0` → score 0.0 (block entry)

### 5. Config
Add to `strategy.entry`:
```yaml
volume_breakout_threshold: 1.5
volume_lookback: 20
```

## Acceptance Criteria
- Entries only trigger when volume confirms the move
- `logs/thinking.jsonl` includes `volume_ratio` for each signal evaluation
- Backtest shows reduced trade count but improved win rate
- Volume indicators visible in any future dashboard

## Files to Modify
- `src/features/indicators.py`
- `src/features/pipeline.py`
- `src/strategy/signals.py`
- `src/strategy/scoring.py`
- `src/config/settings.py`
- `config.yaml`

