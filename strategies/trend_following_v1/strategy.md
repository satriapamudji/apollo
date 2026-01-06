---
name: trend_following_v1
version: 1
requires:
  bars:
    - series: trade
      interval: 4h
  derived:
    - interval: 1d
      from: 4h
  funding: true
parameters:
  # Indicator parameters
  ema_fast:
    type: int
    default: 8
    min: 2
    max: 50
    description: Fast EMA period for trend detection
  ema_slow:
    type: int
    default: 21
    min: 5
    max: 100
    description: Slow EMA period for trend detection
  atr_period:
    type: int
    default: 14
    min: 5
    max: 50
    description: ATR period for volatility measurement
  rsi_period:
    type: int
    default: 14
    min: 5
    max: 50
    description: RSI period (used in pullback entries)
  adx_period:
    type: int
    default: 14
    min: 5
    max: 50
    description: ADX period for regime detection
  chop_period:
    type: int
    default: 14
    min: 5
    max: 50
    description: Choppiness period for regime detection
  volume_sma_period:
    type: int
    default: 20
    min: 5
    max: 50
    description: Volume SMA period for volume ratio calculation

  # Entry parameters
  entry_style:
    type: str
    default: breakout
    enum: [breakout, pullback]
    description: Entry style - breakout or pullback
  score_threshold:
    type: float
    default: 0.55
    min: 0.3
    max: 0.95
    description: Minimum composite score for entry
  max_breakout_extension_atr:
    type: float
    default: 1.5
    min: 0.5
    max: 5.0
    description: Max ATR extension for breakout entries
  volume_breakout_threshold:
    type: float
    default: 1.5
    min: 1.0
    max: 5.0
    description: Volume ratio threshold for breakout confirmation
  volume_confirmation_enabled:
    type: bool
    default: true
    description: Require volume confirmation for entries

  # Exit parameters
  atr_stop_multiplier:
    type: float
    default: 2.0
    min: 1.0
    max: 5.0
    description: ATR multiplier for initial stop-loss
  trailing_start_atr:
    type: float
    default: 1.5
    min: 0.5
    max: 3.0
    description: ATR profit to start trailing
  trailing_distance_atr:
    type: float
    default: 1.5
    min: 0.5
    max: 3.0
    description: Trailing stop distance in ATR
  time_stop_days:
    type: int
    default: 7
    min: 1
    max: 30
    description: Time-based exit after N days

  # Scoring factor weights
  scoring_enabled:
    type: bool
    default: true
    description: Enable multi-factor scoring
  factor_trend:
    type: float
    default: 0.35
    min: 0.0
    max: 1.0
    description: Trend alignment weight
  factor_volatility:
    type: float
    default: 0.15
    min: 0.0
    max: 1.0
    description: Volatility regime weight
  factor_entry_quality:
    type: float
    default: 0.25
    min: 0.0
    max: 1.0
    description: Entry quality weight
  factor_funding:
    type: float
    default: 0.10
    min: 0.0
    max: 1.0
    description: Funding rate weight
  factor_news:
    type: float
    default: 0.15
    min: 0.0
    max: 1.0
    description: News sentiment weight

assumptions:
  bar_time: close_time
  stop_model: intrabar_high_low
  funding_model: discrete_settlement_events
---

# Trend Following V1

A trend-following strategy for Binance USD-M perpetual futures using dual-timeframe
analysis with EMA crossovers and multi-factor scoring.

## Overview

This strategy identifies established trends on the daily timeframe and enters on
the 4-hour timeframe using either breakout or pullback techniques. A composite
scoring system evaluates multiple factors before confirming entry.

## Signal Logic

### Trend Detection (Daily Timeframe)

Uses dual EMA crossover on daily candles:

- **Uptrend**: EMA fast > EMA slow, price > EMA slow, EMA fast rising over 3 bars
- **Downtrend**: EMA fast < EMA slow, price < EMA slow, EMA fast falling over 3 bars
- **No Trend**: Neither condition met

### Entry Styles (4H Timeframe)

#### Breakout Entry
- Requires 20-bar high/low breakout in trend direction
- Volume confirmation: volume_ratio >= volume_breakout_threshold
- Max extension filter: price not more than max_breakout_extension_atr from breakout level
- Rejected if market regime is choppy (ADX < threshold, Choppiness > threshold)

#### Pullback Entry
- Previous bar touched slow EMA (pullback)
- Current bar closes beyond fast EMA (trend resumption)
- RSI confirmation: > 40 for longs, < 60 for shorts
- Volume on recovery bar > previous 3 bars average

### Multi-Factor Scoring

All entries are scored on multiple factors before execution:

| Factor | Default Weight | Description |
|--------|----------------|-------------|
| Trend | 0.35 | Trend strength and alignment |
| Volatility | 0.15 | ATR-based volatility regime |
| Entry Quality | 0.25 | Distance from optimal entry level |
| Funding | 0.10 | Funding rate favorability |
| News | 0.15 | News sentiment modifier |

Only entries with composite score >= score_threshold are taken.

### Exit Rules

1. **Initial Stop-Loss**: Entry price +/- (ATR x atr_stop_multiplier)
2. **Trailing Stop**: Activated after trailing_start_atr profit, trails at trailing_distance_atr
3. **Time Stop**: Exit after time_stop_days if profit < 0.5 ATR
4. **Trend Invalidation**: Exit if daily trend reverses

## Risk Assumptions

- **Position Sizing**: Based on risk.risk_per_trade_pct and stop distance
- **Max Positions**: Controlled by risk.max_positions (default: 1)
- **Leverage**: Bounded by risk.max_leverage
- **Funding**: Applied at 8-hour settlement intervals (discrete events)

## Data Requirements

- **4H OHLCV bars**: Primary entry timeframe
- **Daily derived from 4H**: Trend detection (resampled, not fetched separately)
- **Funding rates**: Required for scoring and risk assessment

## Regime Filtering

The strategy uses ADX and Choppiness Index to detect market regime:

- **Trending**: ADX >= 25, Choppiness <= 50 (entries allowed)
- **Ranging**: ADX <= 20, Choppiness >= 61.8 (entries blocked)
- **Transitional**: Between thresholds (reduced position size)
