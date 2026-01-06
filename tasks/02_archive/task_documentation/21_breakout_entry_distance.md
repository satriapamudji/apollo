# Task 21 — Breakout Entry Distance Calculation

## Summary
Replaced the hardcoded `entry_distance = 0.8` constant in breakout entry logic with a real calculation measuring how extended the current price is from the breakout level, expressed in ATR units.

## Before

### `src/strategy/signals.py`
The `_breakout_entry()` method had a hardcoded constant:
```python
entry_distance = 0.8  # HARDCODED - completely ignores actual extension
```

This meant the scoring engine received the same `entry_quality` score regardless of whether price was just breaking out or already 3 ATR extended from the breakout level.

### `src/config/settings.py`
No configuration for maximum breakout extension threshold.

### `config.yaml`
No breakout extension setting.

## After

### `src/strategy/signals.py` (lines 211-252)

1. **Breakout level tracking**: The method now tracks the actual breakout level (`high_20` for LONG, `low_20` for SHORT).

2. **Real entry distance calculation** (line 228):
   ```python
   entry_distance = abs(price - breakout_level) / atr if atr > 0 else 0.0
   ```

3. **Over-extension rejection** (lines 230-246):
   ```python
   max_extension = self.config.entry.max_breakout_extension_atr
   if entry_distance > max_extension:
       return Signal(
           symbol=symbol,
           signal_type=SignalType.NONE,
           ...
           reason=f"Breakout over-extended: {entry_distance:.2f} ATR > {max_extension} ATR",
           entry_extension=entry_distance,
           ...
       )
   ```

4. **Entry extension tracking**: The `Signal` dataclass now includes `entry_extension: float | None` field (line 44) to track the actual extension value.

### `src/config/settings.py` (line 65)

Added configurable threshold in `EntryConfig`:
```python
max_breakout_extension_atr: float = Field(default=1.5, ge=0.5, le=5.0)
```

### `config.yaml` (line 29)

Added configuration:
```yaml
entry:
  style: breakout
  score_threshold: 0.55
  max_breakout_extension_atr: 1.5
```

### `src/monitoring/thinking_log.py` (line 62)

The extension value is now logged to `logs/thinking.jsonl`:
```python
"entry_extension": signal.entry_extension,
```

## Reasoning

### Why This Change Matters

1. **Entry Quality Accuracy**: The `entry_quality` factor in the scoring engine now reflects the actual extension from breakout level. Entries at 0.5-1.0 ATR are preferred (fresh breakouts), while entries beyond 1.5 ATR are rejected (chasing).

2. **Risk Management**: Over-extended breakouts have lower win rates because:
   - Price has already moved significantly, reducing reward potential
   - Stop loss placement becomes challenging (either too tight or too wide)
   - Reversion to mean becomes more likely

3. **Configurable Threshold**: The `max_breakout_extension_atr` parameter (default 1.5) allows tuning based on backtesting results and market conditions.

### How Entry Quality Score Works

The scoring engine uses `entry_distance_atr` to compute `entry_quality`:
- Distance 0.5 ATR = Best (score approaches 1.0)
- Distance 1.0 ATR = Good (score ~0.75)
- Distance 1.5 ATR = Marginal (score ~0.5, at rejection threshold)
- Distance >1.5 ATR = Rejected (signal not generated)

## Files Modified

| File | Change |
|------|--------|
| `src/strategy/signals.py` | Real entry distance calculation, over-extension rejection logic |
| `src/config/settings.py` | Added `max_breakout_extension_atr` to `EntryConfig` |
| `config.yaml` | Added `max_breakout_extension_atr: 1.5` under `strategy.entry` |
| `src/monitoring/thinking_log.py` | Logs `entry_extension` field |

## Acceptance Criteria Verification

| Criterion | Status |
|-----------|--------|
| Breakout entries only trigger when price is within configured ATR distance | PASS |
| `entry_quality` score reflects real extension | PASS |
| Extension value logged to `logs/thinking.jsonl` | PASS |
| Config-driven threshold with default 1.5 ATR | PASS |

## Test Results

- Full test suite: 135 passed, 1 unrelated failure (floating-point precision in `test_market_order_penalty`)
- Type checking (mypy): Pre-existing errors only, no new errors introduced
- Linting (ruff): Pre-existing line length warnings only

## Notes

The implementation was already complete when this task was reviewed. This documentation captures the changes that were made to fulfill the task requirements.
