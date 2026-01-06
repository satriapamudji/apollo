# AGENTS.md

This file provides guidance to any AI agents when working with code in this repository.

## Project Overview

AI-assisted trend-following bot for Binance USD-M perpetuals using event sourcing architecture. The bot implements deterministic risk gates and supports paper/testnet/live run modes.

## Development Commands

```bash
# Install in development mode
pip install -e ".[dev,llm]"

# Run the bot (uses RUN_MODE env or config.yaml)
bot

# Backtesting
backtest --symbol BTCUSDT --interval 4h --data-path ./data/market
backtest --symbol BTCUSDT --out-dir ./data/backtests/BTCUSDT

# Download historical klines
python -m src.tools.download_klines --symbol BTCUSDT --interval 4h --start 2024-01-01

# Acknowledge manual review pause
ack-manual-review --reason "checked"

# Run tests
pytest

# Linting
ruff check src/
mypy src/
black src/
```

## Architecture

The system uses an event-driven architecture with three concurrent loops in `src/main.py`:

1. **universe_loop**: Updates tradable symbol universe (~24hr interval)
2. **news_loop**: Ingests and classifies news via LLM (~15 min interval)
3. **strategy_loop**: Generates signals and executes trades (~15 min interval)

### Core Modules

- **connectors/**: Binance API clients (REST/WebSocket) and LLM news classifier
- **execution/**: Order placement, position management, user data stream handling
- **ledger/**: Event sourcing infrastructure - `EventBus` publishes to `EventLedger` (append-only JSONL), `StateManager` rebuilds state from events
- **strategy/**: Signal generation using EMA crossovers, RSI, ATR with multi-factor scoring
- **risk/**: Risk gates (drawdown limits, position limits, loss streaks) and position sizing
- **monitoring/**: Metrics (Prometheus), trade/order/thinking logs, event console logging
- **features/**: Technical indicator computation (EMA, ATR, RSI)

### Event Lifecycle

Events flow through `EventBus` → `EventLedger` → handlers. Key events:
- `SIGNAL_COMPUTED` → `TRADE_PROPOSED` → `RISK_APPROVED`/`RISK_REJECTED`
- `RISK_APPROVED` → `ORDER_PLACED` → `ORDER_FILLED` → `POSITION_OPENED`
- Exit: `POSITION_CLOSED` triggers protective orders (STOP_LOSS, TAKE_PROFIT)

### Configuration

Settings loaded via Pydantic `Settings` from `config.yaml` and environment variables. Priority: env vars > config file > defaults. Key configs: `RunConfig` (mode), `RiskConfig` (hard limits), `StrategyConfig` (indicators, entry/exit rules).

## Run Modes

- **paper**: Simulates all orders, never sends to Binance
- **testnet**: Uses testnet API keys, requires `RUN_ENABLE_TRADING=true`
- **live**: Uses live keys, requires `RUN_ENABLE_TRADING=true` and `RUN_LIVE_CONFIRM=YES_I_UNDERSTAND`

## Data Locations

- Event ledger: `data/ledger/events.jsonl`
- Trade log: `logs/trades.csv`
- Order log: `logs/orders.csv`
- Thinking log: `logs/thinking.jsonl`
- Metrics: `http://localhost:9090/`
