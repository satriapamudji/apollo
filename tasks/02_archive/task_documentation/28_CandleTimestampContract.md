# 28_CandleTimestampContract.md

## Task Summary
Task 28 — Candle Timestamp Contract + Data Normalization/Validation

## What Was Implemented

### Problem
The backtester mixed timestamp semantics causing silent 4h shifts:
- `data.py` treated `timestamp` as close time but fell back to `open_time`
- Different CSV files used different column naming (`open_time` vs `timestamp`)
- This could corrupt daily resampling, signal timing, and stop/exit behavior

### Solution: Canonical Schema with Both `open_time` and `close_time`

#### 1. Updated `src/tools/download_klines.py`

New downloads emit canonical schema:
```csv
open_time,close_time,open,high,low,close,volume
1704067200000,1704081599999,42000.00,42500.00,41800.00,42300.00,100.0
```

Where:
- `open_time = kline[0]` (Binance kline open time)
- `close_time = kline[6]` (Binance kline close time)

No more ambiguous `timestamp` column in newly generated files.

#### 2. Updated `src/backtester/data.py`

Rewrote `load_ohlcv_csv()` with strict schema handling:

**Schema Detection Logic:**
| Input Schema | Behavior |
|--------------|----------|
| `close_time` + `open_time` | Use as-is (canonical) |
| `close_time` only | Use, derive `open_time` if interval provided |
| `timestamp` only | Treat as `close_time` with warning |
| `open_time` only | **Requires interval** to derive `close_time` or fails |

**Key Features:**
- Returns DataFrame indexed by `close_time` (bar-close convention)
- Preserves `open_time` column for reference
- Emits `UserWarning` for legacy schemas
- Raises `ValueError` for ambiguous schemas without interval

**Helper Functions Added:**
- `_interval_to_ms(interval)`: Converts interval strings (e.g., "4h") to milliseconds
- `_convert_timestamp_series(df, col)`: Converts timestamp columns to UTC datetime

**Updated `load_symbol_interval()`:**
- Now passes `interval` parameter to `load_ohlcv_csv()` for legacy file support

#### 3. Created `src/tools/normalize_klines.py`

New CLI tool for validating and normalizing kline CSVs:

**Features:**
- Validates required columns present
- Checks monotonic time ordering
- Detects duplicate timestamps
- Validates interval spacing consistency
- Identifies gaps in time series
- Outputs normalized copy to `data/market_normalized/`

**Usage:**
```bash
python -m src.tools.normalize_klines --input data/market/BTCUSDT_4h.csv --interval 4h
```

**Options:**
- `--input`: Input CSV path
- `--interval`: Interval string (e.g., "4h", "1d")
- `--output-dir`: Output directory (default: `data/market_normalized`)
- `--dry-run`: Validate only, don't write normalized file

**Validation Functions:**
- `validate_klines()`: Returns dict with `is_valid`, `errors`, `warnings`
- `normalize_klines()`: Converts legacy schema to canonical

#### 4. Verified Engine Daily Resampling

Confirmed `src/backtester/engine.py:_get_daily_at_time()` already correctly:
- Uses bar-close convention for replay
- Prevents lookahead bias in daily aggregation
- No changes needed

### Test Coverage: `tests/test_data_schema.py`

23 new tests covering:

| Test Class | Tests |
|------------|-------|
| `TestIntervalToMs` | 5 tests for interval string conversion |
| `TestCanonicalSchema` | 3 tests for canonical schema loading |
| `TestLegacyTimestampSchema` | 3 tests for legacy timestamp handling |
| `TestLegacyOpenTimeSchema` | 3 tests for legacy open_time handling |
| `TestMissingTimestampColumns` | 2 tests for error handling |
| `TestDataSorting` | 1 test for data ordering |
| `TestLoadSymbolInterval` | 2 tests for convenience function |
| `TestNaHandling` | 2 tests for missing/invalid data |
| `TestCaseInsensitiveColumns` | 2 tests for case insensitivity |

### Schema Contract

**Canonical Schema (preferred):**
```csv
open_time,close_time,open,high,low,close,volume
```

**Replay Contract:**
- Event time for bar replay = `close_time` (bar-close convention)
- DataFrame index = `close_time`
- `open_time` preserved as column for reference

**Time Relationship:**
```
close_time = open_time + interval_ms - 1
```
Where interval_ms is the interval in milliseconds (e.g., 4h = 14,400,000 ms)

## Files Modified

| File | Change |
|------|--------|
| `src/tools/download_klines.py` | Emit canonical schema with `open_time` + `close_time` |
| `src/backtester/data.py` | Strict schema handling, legacy support, new helpers |
| `src/tools/normalize_klines.py` | **NEW** - Validation/normalization CLI tool |
| `tests/test_data_schema.py` | **NEW** - 23 tests for schema contract |

## Usage Notes

### For New Data
Download with the updated tool:
```bash
python -m src.tools.download_klines --symbol BTCUSDT --interval 4h --start 2024-01-01
```

### For Existing Legacy Data
Option 1: Normalize existing files:
```bash
python -m src.tools.normalize_klines --input data/market/BTCUSDT_4h.csv --interval 4h
```

Option 2: Use with backtester (automatic handling):
```python
df = load_symbol_interval("data/market", "BTCUSDT", "4h")  # Handles legacy automatically
```

### Warnings for Legacy Files
Legacy files will emit warnings but still load:
```
UserWarning: Legacy schema detected in data/market/BTCUSDT_4h.csv: only 'open_time' column found. 
Deriving close_time using interval=4h.
```

## Acceptance Criteria Status

| Criterion | Status |
|-----------|--------|
| New downloads contain `open_time` and `close_time` | ✅ |
| Loader refuses ambiguous schemas without interval | ✅ |
| Backtest no longer suffers 4h timestamp shift | ✅ |
| Validator flags non-monotonic/inconsistent time series | ✅ |
| All tests pass (309 total) | ✅ |
