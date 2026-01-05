# Binance Trading Bot – Build Spec (Testnet → Live)

## 1) Goal
Build a production-grade, event-sourced Binance USD‑M perpetual futures trading system that:
- Runs safely on **testnet** now and can switch to **live** later via config + explicit confirmation.
- Trades automatically with **deterministic risk controls**, **bracket protection** (SL/TP), and **full observability** (what it did, why, and what the exchange returned).
- Supports a scalable “**influence scoring**” / multi-signal framework, then a later phase for **numeric ML**.

## 2) Current Baseline (Already Implemented)
- Modes: `paper` / `testnet` / `live` (live requires explicit confirmation).
- Event ledger: `data/ledger/events.jsonl` (+ sequence tracking).
- REST client with structured request/response logs (`rest_request`, `rest_response`) and signature redaction.
- Execution engine:
  - Sets **position mode**, **margin type**, **leverage** before entry.
  - Places entry order, then places **SL/TP** only after entry fill.
  - Verifies protective orders exist and raises manual intervention if missing.
- User data stream (listenKey WS): ingests `ORDER_TRADE_UPDATE` / `ACCOUNT_UPDATE` and emits ledger fills/cancels.
- Order/trade logs: `logs/orders.csv`, `logs/trades.csv`; signal reasoning: `logs/thinking.jsonl`.
- Single-instance lock: `logs/bot.<mode>.lock` prevents multiple bots writing to the same ledger.

## 3) Non-Goals (Explicitly Out of Scope)
- “Guaranteed profit” claims.
- High-frequency market making.
- Strategies that require exchange-internal data not publicly available.

## 4) System Requirements

### 4.1 Run Modes & Safety Gates
- **paper**: never places exchange orders.
- **testnet**: places orders only with `run.enable_trading=true` and testnet keys present.
- **live**: places orders only with `run.enable_trading=true`, live keys present, and `run.live_confirm=YES_I_UNDERSTAND`.
- Must fail closed: any ambiguity (missing keys, reconciliation issues, protective orders missing) should pause trading via manual review.

### 4.2 Observability (Must-Have)
- Structured logs to stdout (JSON), plus CSV logs for trades/orders.
- Log each REST call (endpoint + params sans secrets + latency + optional response preview).
- Log key lifecycle events to console (`AccountSettingUpdated`, `OrderPlaced`, `OrderFilled`, `PositionOpened`, `PositionClosed`).
- Metrics endpoint (Prometheus): bot up, loop durations, order counts, fill rates, errors, current exposure.

### 4.3 Execution / Trading Lifecycle (Must-Have)
For each approved entry:
1. Ensure account settings (position mode, margin type, leverage).
2. Place entry order.
3. On fill (prefer WS, fallback REST polling):
   - Emit `PositionOpened` and log to `logs/trades.csv`.
   - Place bracket orders (STOP_MARKET SL + TAKE_PROFIT_MARKET TP).
4. On SL/TP fill (from WS):
   - Emit `PositionClosed` and update `logs/trades.csv`.
5. Reconciliation:
   - Detect external/manual positions/orders and trigger manual review.

### 4.4 Data & Signals (Influence Scoring)
Do **not** rely on TradingView for TA; Binance OHLCV is sufficient. TradingView is optional for UI/visualization only.

Implement an “influence scoring” signal engine where each factor produces a normalized score and confidence:
- **Trend**: EMA slope/cross, higher‑timeframe alignment.
- **Momentum**: RSI/MACD-like derivatives, breakout strength.
- **Volatility/regime**: ATR%, volatility expansion/contraction; avoid chop regimes.
- **Liquidity/volume**: volume breakout, spread constraints, min notional/step size compliance.
- **Funding/crowding**: avoid extreme funding; incorporate as penalty.
- **News risk**: block or downweight high-risk windows from the classifier.

Composite:
- `score = Σ(w_i * s_i)` with config-driven weights and a gating threshold.
- Output includes: `signal_type`, `score`, `reason`, and the exact stop/TP plan.

### 4.5 Chart Visualization / “AI Can See It”
Two options (choose one, don’t do both initially):
1. **Dashboard-first (recommended)**: local web UI (Streamlit or FastAPI+React) showing candles + indicators + current positions + events.
2. **LLM-vision assist**: generate a PNG (candles + overlays) and attach to a multimodal model for periodic review (not for high-frequency decisions).

## 5) Roadmap (Phased)

### Phase 1 — Reliability & Ops (Live Readiness)
- Persistent “pending entry” context so if the bot restarts between `OrderPlaced` and `OrderFilled`, it can still place protective orders after restart.
- Market-data WS to reduce REST polling load (candles/mark price).
- Harden reconciliation: detect “open position but missing SL/TP” and force manual review or auto-replace protection (config-driven).
- Kill switch command: cancel open orders + close positions immediately.

Acceptance:
- No duplicate bots (lock enforced).
- A filled entry always results in SL/TP placement and a `PositionOpened` row in `logs/trades.csv`.
- SL/TP fills always produce `PositionClosed` and update `logs/trades.csv`.

### Phase 2 — Influence Scoring v1 (Rule-Based)
- Implement factor modules and weight config.
- Add score explanation payload to `logs/thinking.jsonl`.
- Add per-factor diagnostics to metrics.

Acceptance:
- Given the same market data, scores are deterministic.
- Signals can be audited from the ledger + thinking logs.

### Phase 3 — UI / Operator Workflow
- Dashboard: positions, orders, recent ledger events, per-symbol charts, and “why” panel.
- Controls: pause/resume, acknowledge manual review, kill switch.

Acceptance:
- Operator can confirm SL/TP exist and see trade lifecycle in one place.

### Phase 4 — Numeric ML (Later, After v1 Is Stable)
- Feature store (OHLCV + derived indicators + regime labels).
- Walk-forward validation + leakage checks.
- Model outputs a probability/expected return used as one factor in influence scoring (not the sole decision-maker).

## 6) Interfaces / Artifacts
- Primary truth: `data/ledger/events.jsonl`.
- Operator logs: `logs/orders.csv`, `logs/trades.csv`, `logs/thinking.jsonl`.
- Config: `config.yaml` + `.env` for secrets.

## 7) “Definition of Done”
- Testnet run completes end-to-end: entry → fill → SL/TP placed → exit → logged.
- Switching to live requires explicit confirmation and uses live endpoints/keys automatically.
- No secrets printed to console (no signatures, no full listenKey).
