# 02_Backtest.md — USD-M Futures Backtesting & Data Pipeline (Offline-First, Event-Driven)

## 0) Purpose

We want a backtesting system that is:

1. **Reproducible**: once a dataset is built, the backtest runs offline and yields identical results given the same code + dataset + seed.
2. **Decision-grade**: funding, sizing constraints, timestamps, and execution realism are modeled in a way that avoids known “paper alpha” traps.
3. **Scalable**: supports multi-symbol portfolios and multiple strategies without bespoke ad-hoc loaders.
4. **Event-driven**: replay uses a unified time-ordered stream of typed events, matching the live system’s event-sourcing architecture.

This document maps **Binance USD-M Futures APIs → dataset contracts → replay engine architecture**, and defines the work breakdown into actionable tasks.

---

## 1) Confirm Product + Environment Routing (and fix the mismatch)

### 1.1 Product confirmation
`config.yaml` points at:
- `binance.base_url: https://fapi.binance.com` (`config.yaml:13`)
- `binance.ws_url: wss://fstream.binance.com` (`config.yaml:14`)

These are **Binance USD-M Futures (FAPI)** endpoints.

### 1.2 Explicit mismatch to fix
`config.yaml` sets `environment: testnet` (`config.yaml:1`) but still uses **mainnet** URLs (`config.yaml:13-14`).

The code already supports environment routing:
- Defaults: `testnet_base_url`, `testnet_ws_url` (`src/config/settings.py:23-24`)
- Routing logic: `Settings.binance_base_url` and `Settings.binance_ws_url` (`src/config/settings.py:441-461`)

But the repo’s default testnet URLs do **not** match the desired testnet endpoints described in the audit prompt. We must make the environment selection explicit and correct.

### 1.3 Clean routing rule (two-plane model)
For both live trading and backtesting/collection, we should separate:

1) **Trading plane** (signed/private endpoints, orders, user streams)
- mainnet trading: `fapi.binance.com`
- testnet trading: testnet base URLs

2) **Market data plane** (public endpoints like klines, exchangeInfo, funding history, tickers)
- Usually should be **mainnet** even when trading on testnet to avoid testnet’s thin/erratic market data.

We already have a switch for this:
- `binance.use_production_market_data` and the `market_data_base_url/ws_url` fields (`src/config/settings.py:28-31`, `src/config/settings.py:463-472`)

**Policy**:
- `run.mode` controls whether orders are real; `environment` controls which trading endpoint is used.
- Market data base URL can be pinned to production for realism regardless of trading environment.

---

## 2) Current State (YAML + Backtester) — What’s true today

### 2.1 Current YAML backtest configuration
`config.yaml` contains `backtest.*` knobs (`config.yaml:6-11`), but the backtest CLI currently takes separate flags and does not read those settings (`src/backtester/runner.py:14-94`).

### 2.2 Current backtester architecture (single-symbol loop)
The current backtester:
- Loads a single symbol’s OHLCV CSV (`src/backtester/data.py:44-49`).
- Iterates bars and builds point-in-time daily candles from 4h to avoid lookahead (`src/backtester/engine.py:_get_daily_at_time`).
- Generates signals via `SignalGenerator` (`src/backtester/engine.py:209-217`).
- Executes using either “ideal” fixed slippage or a probabilistic simulator (`src/backtester/execution_sim.py`).
- Attempts to apply funding costs via `FundingRateProvider` (`src/backtester/funding.py`).

### 2.3 High-risk correctness gaps to close (backtest)
1) **Funding modeling is not decision-grade**:
   - Funding is deducted repeatedly using “total hours since entry” each bar (cumulative overcount), and also re-applied on exit paths (`src/backtester/engine.py:150-194`).
   - Funding is multiplied by leverage (`src/backtester/funding.py:135-138`), which is not the correct perp funding payment model.
   - Funding payer/receiver sign is not conditioned on position side.
   - Funding cadence is hard-assumed 8h (`src/backtester/funding.py:31`, `src/backtester/funding.py:63-65`), while real settlement cadence can vary.

2) **Symbol filters are hardcoded** (`src/backtester/engine.py:93-99`), but must come from `exchangeInfo` snapshots per symbol and be versioned.

3) **Data timestamp semantics are inconsistent**:
   - Loader accepts `timestamp` or `open_time` (`src/backtester/data.py:18-27`).
   - Repo data contains mixed schemas (e.g., `data/market/BTCUSDT_4h.csv` uses `open_time`, while `data/market/ETHUSDT_4h.csv` uses `timestamp`), which risks lookahead and inconsistent daily aggregation.

4) **Backtest ignores YAML backtest/execution knobs**:
   - `execution.max_spread_pct` exists in YAML (`config.yaml:91`) but backtester doesn’t model spread at all.

These drive the new design below.

---

## 3) Binance APIs → Backtest Data Needs (Data Contract Mapping)

### 3.1 Mapping table (offline dataset contract)

| Backtest Need | Binance Endpoint | Minimal Fields Needed | Local Artifact | Refresh Policy |
|---|---|---|---|---|
| Symbol rules / filters | `GET /fapi/v1/exchangeInfo` | `symbols[].symbol`, `contractType`, `status`, `filters[]` for tick/step/minQty/minNotional-style | `metadata/exchangeInfo/<date>.json` + normalized `metadata/symbol_rules.parquet` | Snapshot per dataset build (recommended daily) |
| Universe selection (liquidity) | `GET /fapi/v1/ticker/24hr` | `symbol`, `quoteVolume`, `lastPrice` | `universe/universe_<date>.json` | Snapshot per dataset build |
| Spread proxy (tier 2) | `GET /fapi/v1/ticker/bookTicker` | `bidPrice`, `askPrice`, `bidQty`, `askQty`, `time` | `micro/bookTicker/symbol=<SYM>.parquet` | Forward-collected at fixed cadence |
| Trade-price OHLCV | `GET /fapi/v1/klines` | open_time, close_time, OHLCV, trades, taker_buy_volume (optional) | `bars/trade/interval=<I>/symbol=<SYM>.parquet` | Append |
| Mark-price OHLCV (tier 2) | `GET /fapi/v1/markPriceKlines` | open/close time, OHLCV | `bars/mark/...` | Append |
| Index/premium OHLCV (tier 2) | `GET /fapi/v1/indexPriceKlines`, `GET /fapi/v1/premiumIndexKlines` | open/close time, OHLCV | `bars/index/...`, `bars/premium/...` | Append |
| Funding history | `GET /fapi/v1/fundingRate` | `fundingTime`, `fundingRate`, `markPrice` (if returned) | `funding/symbol=<SYM>.parquet` | Append per event |
| Open interest (optional feature) | `GET /fapi/v1/openInterest` | `openInterest`, `time` | `series/openInterest/...` | Snapshot or periodic |

### 3.2 Pagination and rate-limit constraints (implementation guidance)
- Kline endpoints are paginated; requests must page in time order using `startTime/endTime` with a `limit` up to the API maximum (historically 1500).
- Funding history is paginated; requests must page by `fundingTime`.
- Rate limit errors (429) must be handled with backoff and recorded into the dataset manifest.

---

## 4) Dataset Contract (Timestamp Semantics and Schema)

### 4.1 Canonical event time
For replay we require a single unambiguous event time per series:

- **BarEvent** time = `close_time` (UTC). This matches how the live strategy loop treats candles (it operates at bar close).
- **FundingEvent** time = `funding_time` (UTC).
- **SpreadEvent** time = snapshot time (UTC).

### 4.2 Required bar columns
All bar datasets must store:
- `symbol` (string)
- `interval` (string: `1m`, `5m`, `4h`, `1d`, etc.)
- `open_time` (UTC timestamp)
- `close_time` (UTC timestamp)
- `open`, `high`, `low`, `close` (float)
- `volume` (float)

**Do not** store a single ambiguous `timestamp` that may be open or close; store both.

### 4.3 Funding event schema
Funding events must store:
- `symbol`
- `funding_time`
- `funding_rate` (decimal, e.g. `0.0001` for 0.01%)
- `mark_price` (optional; if provided by API or reconstructed from mark series)

### 4.4 Symbol rules schema (normalized)
From `exchangeInfo`, normalize per symbol:
- `tick_size`, `step_size`, `min_qty`, `min_notional`
- `contract_type`, `status`, `quote_asset`
- `effective_date` (from snapshot date)

**Versioning rule**: symbol rules are not assumed constant; replay uses the latest effective rules at the event time (or pins to dataset build rules if explicitly configured).

---

## 5) Offline Data Layout (Shared Store + Snapshot Manifests)

### 5.1 Folder structure
Use a shared append-only store that any backtest can reuse, plus pinned snapshots for reproducibility:
- Store (reusable): `data/datasets/usdm/store/`
- Snapshots (immutable): `data/datasets/usdm/snapshots/<snapshot_id>/`

Example store artifacts:
- `data/datasets/usdm/store/metadata/exchangeInfo/2026-01-06T163500Z.json`
- `data/datasets/usdm/store/metadata/symbol_rules/2026-01-06T163500Z.parquet`
- `data/datasets/usdm/store/universe/2026-01-06.json`
- `data/datasets/usdm/store/bars/trade/interval=4h/symbol=BTCUSDT/date=2026-01-06/part-000.parquet`
- `data/datasets/usdm/store/bars/trade/interval=1d/symbol=BTCUSDT/date=2026-01-06/part-000.parquet` (optional; can also be derived)
- `data/datasets/usdm/store/funding/symbol=BTCUSDT/date=2026-01-06/part-000.parquet`
- `data/datasets/usdm/store/micro/bookTicker/symbol=BTCUSDT/date=2026-01-06/part-000.parquet` (tier 2)

Example snapshot:
- `data/datasets/usdm/snapshots/<snapshot_id>/manifest.json`
- `data/datasets/usdm/snapshots/<snapshot_id>/checksums.sha256`

### 5.2 manifest.json (minimum required fields)
Store:
- `snapshot_id`, `created_at_utc`, `schema_version`
- `product`: `binance_usdm_perp`
- `environment`: `production_market_data` / `testnet_market_data` (explicit)
- `symbols` included, intervals included, time ranges per artifact
- `provenance`: endpoints used + request params + page sizes + errors observed
- `assumptions`: fee schedule, funding application rules, execution model parameters
- `hashes`: per referenced store file sha256 + row counts (pins the snapshot)

---

## 6) Replay / Backtester Architecture (Event-Driven, Multi-Symbol)

### 6.1 Event types (minimum)
- `BarEvent(symbol, interval, open_time, close_time, ohlcv, series="trade|mark|index|premium")`
- `FundingEvent(symbol, funding_time, funding_rate, mark_price?)`
- `UniverseEvent(timestamp, symbols, selection_metadata)`
- `SpreadEvent(symbol, timestamp, bid, ask, bid_qty, ask_qty)` (optional)
- Strategy outputs:
  - `SignalEvent(symbol, side, strength, stop, take_profit?, score, reason)`
  - `TradeProposedEvent(...)`
  - `RiskApprovedEvent(...)` / `RiskRejectedEvent(...)`
  - `OrderPlacedEvent(...)`, `OrderFilledEvent(...)`
  - `PositionOpenedEvent(...)`, `PositionClosedEvent(...)`

### 6.2 Deterministic ordering at identical timestamps
Define a stable processing order, e.g.:
1) Funding events (affect PnL/position state)
2) Bar close events (update indicators; compute signals)
3) Spread events (execution guards)
4) Strategy decisions → proposals
5) Risk gating
6) Execution simulation

This avoids accidental lookahead within the same timestamp and makes results reproducible.

### 6.3 Engine modules (separation of concerns)
1) **DatasetReader**: yields event iterators for each series.
2) **EventMux**: merges multiple iterators into one ordered stream (heap merge).
3) **PortfolioState**: positions, equity, drawdown, open orders.
4) **StrategyRunner**: consumes events, emits candidate trade proposals.
5) **PortfolioSelector**: chooses top-K candidates (supports `max_positions=1`).
6) **RiskGate**: applies deterministic risk engine using per-symbol rules.
7) **ExecutionModel**: converts proposals to fills (ideal, ATR-based, spread-aware, microstructure-aware).
8) **BacktestLedger** (optional but recommended): append-only event log for the backtest run (mirrors live event sourcing).

---

## 7) Funding Model Specification (Decision-Grade)

### 7.1 Funding must be discrete (event-based)
Funding is charged at settlement timestamps. The dataset contains the settlement events, so replay applies funding **only when a FundingEvent occurs**.

### 7.2 Funding payment direction
Funding payer/receiver depends on:
- position side (LONG/SHORT)
- sign of funding rate

Canonical rule:
- If `funding_rate > 0`: longs pay shorts.
- If `funding_rate < 0`: shorts pay longs.

### 7.3 Funding cost calculation
At settlement time `t`:
- Compute position notional at `t` (prefer mark price at `t`):
  - `notional_t = abs(position_qty) * mark_price_t`
- Funding cashflow:
  - LONG: `cashflow = notional_t * funding_rate`
  - SHORT: `cashflow = -notional_t * funding_rate`
- Apply to equity:
  - `equity -= cashflow` (positive cashflow means paying)

**No leverage multiplier**: leverage affects margin usage, not notional exposure.

### 7.4 Funding cadence changes
Do not assume 8h cadence. Use the funding events as given by the dataset; if frequency changes, the event stream naturally captures it.

---

## 8) Execution Realism (Tiered)

### Tier 0: Ideal
- Fixed slippage and fixed fees (current `--slippage-pct`, `--fee-pct`).

### Tier 1: Bar-based probabilistic fills (existing simulator)
- Slippage scales with ATR and volatility regime (`src/backtester/execution_sim.py`).
- Fill probability model must be parameterized and recorded in the dataset manifest.

### Tier 2: Spread-aware
- Join a `SpreadEvent` stream (bookTicker snapshots) and enforce:
  - max spread constraint (`execution.max_spread_pct`)
  - slippage floor of half-spread

### Tier 3: Microstructure-aware (optional heavy)
- Use order book depth + aggTrades to simulate queue position and fill probability.

---

## 9) Strategy Packaging (strategies/<name>/strategy.md)

### 9.1 Strategy contract
Each strategy must declare:
- required inputs (which series, which intervals)
- parameters (typed defaults + constraints)
- signal logic (human-readable spec)
- risk assumptions (what the engine enforces vs what the strategy expects)
- execution assumptions (limit/market style, spread gating)

`config.yaml` references the strategy by `strategy.name` (`config.yaml:19`) and supplies parameter overrides under `strategy.*`.

### 9.2 Validation
At backtest start:
- Validate dataset has required inputs for the chosen strategy.
- Validate overrides are allowed and type-correct against the strategy schema.

---

## 10) Work Breakdown (Tasks)

Tasks start at **Task 27** because the last archived task is `task-26.md` under `tasks/02_archive/`.

- Task 27: Funding model rework (event-based, correct sign, no leverage)
- Task 28: Candle timestamp contract + data normalization/validation
- Task 29: exchangeInfo snapshot + symbol rules versioning + environment routing alignment
- Task 30: Universe builder + dataset manifest/checksums
- Task 31: Event-driven, multi-symbol backtester core
- Task 32: Strategy packaging system (`strategy.md`) + config override validation
- Task 33: Spread proxy dataset + spread-aware execution model

See `tasks/01_current/` for the full task specs.

# Final Prompt

You are a senior quant/dev architect specializing in Binance USD-M perpetuals, offline-reproducible backtesting, and event-driven trading systems. Produce a practical architecture + implementation plan that maps Binance USD-M Futures public APIs to a versioned offline dataset and an event-driven multi-symbol backtester.

## Constraints
- Product: Binance USD-M Futures (FAPI).
- Backtests must be fully reproducible offline once data is downloaded.
- Design must tolerate API failures: 429/418, 5xx, 200 with empty payloads, and partial data ranges.
- Be practical about pagination (klines max limit per call) and rate limits.
- Only propose new config fields when necessary; prefer reusing existing `config.yaml` settings.

## Required Outputs (in Markdown)
1) **API -> Data Contract Mapping**
   - For each need (universe, symbol rules, trade klines, mark price klines, funding, spread proxy), map:
     - Binance endpoint(s)
     - required fields
     - local artifact format and folder path
     - refresh/update policy

2) **Dataset Layout (Offline-First, Versioned)**
   - Define a shared store under `data/datasets/usdm/store/` and snapshots under `data/datasets/usdm/snapshots/<snapshot_id>/`.
   - Include: `manifest.json` (provenance + assumptions + schema versions) and `checksums.sha256`.
   - Define canonical timestamp semantics:
     - Bar replay time = candle close time
     - Funding replay time = settlement time
     - Spread replay time = snapshot time

3) **Minimum Viable Dataset for trend_following_v1**
   - List the minimum artifacts required to reproduce the current strategy offline:
     - trade klines at `4h` (+ deterministic daily derivation)
     - funding history
     - exchangeInfo snapshot -> symbol rules table
   - Explain what execution realism is possible with this minimum tier.

4) **Next Dataset Tier (Execution Realism + Future Strategies)**
   - Add mark price klines, index/premium series, spread snapshots, optional OI/aux series.
   - Explain what new capabilities each tier unlocks (funding realism, liquidation modeling, regime features, spread gating).

5) **Event-Driven Backtester Re-Architecture Plan**
   - Describe how to refactor from a single-symbol bar loop into a unified event stream:
     - `BarEvent`, `FundingEvent`, `SpreadEvent`, `UniverseEvent`
   - Specify deterministic ordering when timestamps collide.
   - Show how multi-symbol works while respecting `risk.max_positions=1`.
   - Define module boundaries: DatasetReader, EventMux, StrategyRunner, RiskGate, ExecutionModel, PortfolioState, optional BacktestLedger.

6) **Strategy Packaging System**
   - Define `strategies/<name>/strategy.md` schema (YAML front matter + markdown body).
   - Show how `config.yaml` references the strategy and applies validated overrides.
   - Provide an example `strategies/trend_following_v1/strategy.md` consistent with current YAML parameters.

7) **Correctness Audit Checklist (Backtest Pitfalls)**
   - Funding accrual timing and double-counting risks
   - Intrabar stop/take assumptions (high/low crossing)
   - Fee model vs futures fee reality (maker/taker, funding)
   - Lookahead avoidance in daily resampling
   - Reproducibility controls (random seed usage)
   - Tick/step/min qty correctness and rounding
   - Whether YAML execution knobs (e.g., `execution.max_spread_pct`) are actually enforced

8) **Scripts + Acceptance Tests**
   - Provide a set of proposed scripts:
     - universe builder
     - exchangeInfo downloader/normalizer
     - kline downloader/validator (paged, rate-limit aware)
     - funding downloader/validator
     - (optional) spread collector or deterministic spread model
   - Provide acceptance tests that prove:
     - offline reproducibility with manifest + checksums
     - deterministic replay ordering
     - correct funding sign and cadence behavior
     - no lookahead in daily aggregation

## Style
- Be explicit and defensive: document assumptions and failure modes.
- Prefer actionable structures (tables, bullet lists, file layout trees).
- Include concrete examples (schemas, sample manifest fields, event type definitions).
