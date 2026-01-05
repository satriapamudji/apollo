# Task 17 — Add Free Binance “Crowding/Regime” Data (Open Interest, Ratios)

## Goal
Improve signal quality using free Binance Futures public data that helps avoid crowded/choppy regimes.

## Why
Pure OHLCV trend systems are fragile in crypto chop. Binance provides useful public time series to detect crowding and regime shifts.

## Deliverables
- Add REST support for (production market-data domain):
  - Open interest history
  - Global long/short account ratio
  - Top trader long/short ratio
  - Taker buy/sell (long/short) ratio
  - Historical funding rate (for carry modeling; complements Task 06)
- Store to `data/` with a stable schema and cache to avoid re-downloading.
- Integrate as factors into the scoring/regime filter (Task 04):
  - crowding penalty, volatility-of-funding, OI expansion/decay, taker imbalance.

## Acceptance Criteria
- Factors are deterministic and visible in `logs/thinking.jsonl`.
- Strategy can run entirely on free Binance + local computation (no paid data).

