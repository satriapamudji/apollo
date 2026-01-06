# Task 29 - exchangeInfo Snapshot + Symbol Rules Versioning + Environment Routing Alignment

## Goal
Eliminate hardcoded symbol filters by sourcing tick/step/minQty/minNotional-style constraints from **`GET /fapi/v1/exchangeInfo`**, and align environment routing (testnet vs mainnet) to avoid configuration ambiguity for both trading and market-data collection.

## Why
- Backtest and live execution currently rely on hardcoded `SymbolFilters` in the backtester (`src/backtester/engine.py`), which will drift from reality and cause invalid sizing, unrealistic fills, and live order rejections.
- `config.yaml` sets `environment: testnet` but points at production FAPI URLs; `src/config/settings.py` has testnet defaults that may not match the desired USD-M testnet endpoints. This ambiguity will eventually cause “works on paper, fails in testnet/live” incidents.

## Deliverables

### 1) Add an exchangeInfo snapshot tool (market-data plane)
Create a tool (or extend an existing one) to download:
- `GET /fapi/v1/exchangeInfo`

Store:
- Raw response JSON with timestamped filename.
- A normalized “symbol rules” table derived from `filters[]`.

### 2) Normalize symbol rules into a stable internal format
Implement a parser that extracts (per symbol):
- `tick_size` (price increment)
- `step_size` (qty increment)
- `min_qty`
- `min_notional` (or closest equivalent futures filter)
- contract metadata: `contractType`, `status`, `quoteAsset`

Requirements:
- Be robust to missing/extra filter types.
- Prefer explicit filterType matches (e.g., `PRICE_FILTER`, `LOT_SIZE`, `MIN_NOTIONAL` / futures equivalents).
- Emit deterministic defaults only when explicitly justified (and record them in metadata).

### 3) Versioning rules
Store symbol rules with an `effective_date` (snapshot time) so rules can be pinned for reproducibility:
- Backtest should use dataset-pinned rules by default.
- Optionally support “time-aware” rule changes if multiple snapshots exist (future enhancement).

### 4) Wire symbol rules into backtest sizing/risk gates
Replace hardcoded `SymbolFilters(...)` in `src/backtester/engine.py` with rules loaded from the dataset (or a provided rules file).

### 5) Environment routing clean-up (two-plane model)
Make environment selection explicit:
- Trading endpoints are controlled by `run.mode` and/or `environment`.
- Market data endpoints can be pinned to production using existing `binance.use_production_market_data`.

Concrete outcomes:
- Add explicit `binance.testnet_base_url` and `binance.testnet_ws_url` to `config.yaml` (fields already exist in `BinanceConfig`).
- Document the intended mapping for testnet vs mainnet, and ensure `Settings.binance_base_url`, `Settings.binance_ws_url`, and `Settings.binance_market_data_base_url` behave predictably.

## Acceptance Criteria
- Backtester no longer uses hardcoded tick/step/min constraints; it loads symbol rules from an exchangeInfo snapshot artifact.
- Backtests fail fast if symbol rules are missing for a requested symbol.
- `config.yaml` has explicit testnet URLs so “environment=testnet” cannot silently point at production trading endpoints.
- Unit tests validate symbol rule parsing for at least one realistic exchangeInfo symbol payload.

## Files to Modify
- `src/config/settings.py`
- `config.yaml`
- `src/backtester/engine.py`
- `src/tools/` (new exchangeInfo downloader)
- `tests/` (symbol rule parsing tests)

## Notes
- Keep raw exchangeInfo snapshots; do not rely on “latest” at runtime for reproducible backtests.
- This task intentionally reuses existing config fields; avoid introducing new config keys unless strictly required.

