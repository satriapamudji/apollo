# Binance Trend Bot

AI-assisted trend-following bot for Binance USD-M perpetuals with event sourcing,
deterministic risk gates, and paper/testnet/live run modes.

## Quickstart

1. Copy `.env.example` to `.env` and fill keys as needed.
2. Review `config.yaml` and set `run.mode`, `run.enable_trading`, and `run.live_confirm`.
3. Install and run:

```bash
python -m pip install -e .
bot
```

## Run Modes

- `paper`: never sends orders; emits simulated events only.
- `testnet`: sends orders only if `run.enable_trading=true` and testnet keys exist.
- `live`: sends orders only if `run.enable_trading=true`, live keys exist, and
  `run.live_confirm` equals `YES_I_UNDERSTAND`.

## Logs and Data

- Event ledger: `data/ledger/events.jsonl`
- Trade log: `logs/trades.csv` (opens write a row; closes update it)
- Order log: `logs/orders.csv`
- Thinking log: `logs/thinking.jsonl`
- User stream: listenKey WS (ORDER_TRADE_UPDATE / ACCOUNT_UPDATE) runs in `testnet`/`live` by default.
- Roadmap tasks: `tasks/`

To log REST calls (endpoints + latency + optional response preview), set:
- `monitoring.log_http: true`
- `monitoring.log_http_responses: true`

Metrics: Prometheus exporter on `http://localhost:<monitoring.metrics_port>/`.

## Backtesting

Run a simple backtest from CSV market data:

```bash
backtest --symbol BTCUSDT --interval 4h --data-path ./data/market
```

## Reset (local)

To reset local runtime state/logs (does not affect Binance orders/positions), stop the bot and delete:

- `data/ledger/events.jsonl`
- `data/ledger/sequence.txt`
- `logs/orders.csv`
- `logs/trades.csv`
- `logs/thinking.jsonl`
- `logs/bot.<mode>.lock` (only if a stale lock remains)

## TP/SL and Leverage Notes

- TP/SL orders are placed only after the entry order fills (you'll see `OrderFilled` + `PositionOpened` in the ledger).
- Stop-loss / take-profit fills are ingested from the user stream so trades close even if you didn't exit via the strategy.
- Leverage/margin/position-mode calls emit `AccountSettingUpdated` / `AccountSettingFailed` events in the ledger.

## Manual Review

If reconciliation or execution detects an issue, trading pauses until you acknowledge:

```bash
ack-manual-review --reason "checked"
```

If the console script is unavailable:

```bash
python -m src.tools.ack_manual_review --reason "checked"
```

## News Classification

If no `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` is configured, the news classifier falls back to a local stub (risk=`LOW`).
