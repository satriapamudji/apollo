# Task 16 — Use Production Market Data While Trading Testnet (Configurable)

## Goal
Decouple “where we get market data” from “where we trade” so testnet runs use realistic liquidity/volume/funding inputs.

## Why
Binance testnet market data can be incomplete or unrepresentative. For strategy development you want production market data even when placing orders on testnet.

## Deliverables
- Add config fields (or a single switch), e.g.:
  - `binance.market_data_base_url`
  - `binance.market_data_ws_url`
  - `binance.use_production_market_data_in_testnet: true`
- Route these calls through the market-data endpoints:
  - exchangeInfo, tickers, klines, premiumIndex, bookTicker, futures/data endpoints
- Keep **account/order** endpoints on the trading environment (testnet/live) for safety.

## Acceptance Criteria
- In `run.mode=testnet`, universe selection + TA inputs come from production market data when enabled.
- Trading endpoints remain testnet-only unless `run.mode=live`.

