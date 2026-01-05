# Runbook

## Paper (default)

1. `run.mode: paper`
2. `run.enable_trading: false`
3. Start: `bot`

## Testnet

1. Add testnet keys to `.env`:
   - `BINANCE_TESTNET_FUTURE_API_KEY`
   - `BINANCE_TESTNET_FUTURE_SECRET_KEY`
2. Set in `config.yaml`:
   - `run.mode: testnet`
   - `run.enable_trading: true`
3. Start: `bot`

## Live (explicit confirmation required)

1. Add live keys to `.env`:
   - `BINANCE_API_KEY`
   - `BINANCE_SECRET_KEY`
2. Set in `config.yaml`:
   - `run.mode: live`
   - `run.enable_trading: true`
   - `run.live_confirm: YES_I_UNDERSTAND`
3. Start: `bot`

## Manual Review Flow

1. Stop trading or confirm exchange state (positions/orders).
2. Acknowledge manual review:
   - `ack-manual-review --reason "checked"`
3. Restart the bot.

## Reset Local State (Files)

This resets only local state/logs. It does **not** cancel orders or close positions on Binance.

1. Stop the bot.
2. Delete:
   - `data/ledger/events.jsonl`
   - `data/ledger/sequence.txt`
   - `logs/orders.csv`
   - `logs/trades.csv`
   - `logs/thinking.jsonl`
3. If you see `another_instance_running`, delete `logs/bot.<mode>.lock` only after confirming no bot process is running.

## Common Checks

- If `strategy_paused` appears: check `data/ledger/events.jsonl` for
  `ManualInterventionDetected`.
- `logs/trades.csv` writes a row when a position opens (blank exit fields) and updates it when closed.
- `logs/orders.csv` logs order placement/fills/cancels.
- `logs/thinking.jsonl` records per-signal reasoning.
- If you only see `OrderPlaced` (and not `OrderFilled`/`PositionOpened`), the entry order likely hasn't filled yet, so TP/SL won't appear.
- In `testnet`/`live`, the bot connects to the Binance user data stream and ingests `ORDER_TRADE_UPDATE` to log fills and stop/TP exits.

## Single Instance / Stopping the Bot (Windows)

- The `bot` process uses a lock file at `logs/bot.<mode>.lock`; if you see `another_instance_running`, stop the other process.
- PowerShell: list likely processes: `Get-Process bot,python -ErrorAction SilentlyContinue`
- PowerShell: kill by PID: `Stop-Process -Id <PID> -Force`
- Git Bash: kill by PID via PowerShell: `powershell.exe -NoProfile -Command "Stop-Process -Id <PID> -Force"`
- Git Bash: `taskkill` needs arg-conv disabled: `MSYS2_ARG_CONV_EXCL='*' taskkill /PID <PID> /F`

## Debug HTTP

To log Binance REST endpoints and responses:
- `monitoring.log_http: true`
- `monitoring.log_http_responses: true`

## Debug User Stream

- Disable user stream (not recommended): `binance.user_stream_enabled: false`
- To confirm the user stream is delivering account events, cancel an order in the Binance UI and look for `OrderCancelled` with `metadata.source="user_stream"` in `data/ledger/events.jsonl` (and a new row in `logs/orders.csv`).
