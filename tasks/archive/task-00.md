# Task 00 — Make Backtests Match Live (No Lookahead)

## Goal
Make the backtester semantically equivalent to the live bot so results are not inflated by lookahead or data alignment artifacts.

## Why
The current backtester builds the “daily” trend series by resampling the full 4h dataset, which leaks future intra-day OHLC into earlier 4h decisions (lookahead bias). Any profitability conclusion based on that is not trustworthy.

## Deliverables
- Update `src/backtester/engine.py` so the “trend timeframe” inputs are strictly **closed** candles at decision time.
- Ensure the backtest uses the same “closed candle only” rule as live (`src/main.py:_klines_to_df` filters by close-time).
- Add a unit test that fails if daily features at time `t` depend on 4h bars after `t`.

## Implementation Notes
- Easiest safe rule: when generating a signal at 4h candle close `t`, use daily candles with close time `< t` (or `<= t` only when `t` is a daily close).
- Prefer using the same timestamp convention as live (index by `close_time` / candle close).
- Keep the strategy logic unchanged; change only the data alignment.

## Acceptance Criteria
- Running `python -m src.backtester.runner --symbol ETHUSDT` produces the same trades whether the 4h CSV contains future rows appended or not (i.e., no dependence on data after the evaluation timestamp).
- A new test demonstrates that “future” 4h rows do not change signals for earlier timestamps.
- Document the backtest assumptions in `SPEC_v2.md` or a new `docs/backtesting.md` (one page max).

