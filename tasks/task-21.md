# Task 21 — Fix Breakout Entry Distance Calculation

## Goal
Replace the hardcoded `entry_distance = 0.8` constant with a real calculation of how extended price is from the breakout level.

## Why
The current breakout entry scoring feeds a meaningless constant into the scoring engine. This allows entries on over-extended breakouts (price already 2-3 ATR above the breakout level) which have significantly lower win rates. The `entry_quality` factor in scoring becomes useless.

## Current Problem
```python
# signals.py line 193
entry_distance = 0.8  # HARDCODED - completely ignores actual extension
```

## Deliverables
- In `_breakout_entry()`, compute actual entry distance:
  ```python
  breakout_level = high_20 if direction == "LONG" else low_20
  entry_distance = abs(price - breakout_level) / atr if atr > 0 else 0.0
  ```
- Add a config-driven `max_breakout_extension_atr` threshold (default: 1.5 ATR)
- Reject breakouts where `entry_distance > max_breakout_extension_atr`
- Log the actual extension value in `logs/thinking.jsonl` for analysis

## Acceptance Criteria
- Breakout entries only trigger when price is within configured ATR distance of breakout level
- `entry_quality` score now reflects real extension (0.5-1.0 ATR = best, >1.5 ATR = rejected)
- Backtests show change in trade count and improved win rate on breakouts

## Files to Modify
- `src/strategy/signals.py`
- `src/config/settings.py` (add `max_breakout_extension_atr`)
- `config.yaml`

