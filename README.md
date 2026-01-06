# Apollo

AI-assisted trading bot for Binance USD-M perpetual futures with event sourcing, deterministic risk gates, and paper/testnet/live run modes.

## Table of Contents

- [Features](#features)
- [Architecture Overview](#architecture-overview)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [Run Modes](#run-modes)
- [Backtesting](#backtesting)
- [CLI Tools](#cli-tools)
- [Logs and Data](#logs-and-data)
- [Operations](#operations)
- [Documentation](#documentation)

## Features

- **Trend-Following Strategy**: Multi-timeframe analysis (daily trend + 4H entries) with EMA crossover, breakout detection, and volume confirmation
- **Event Sourcing**: All state changes recorded in append-only ledger for complete auditability and replay
- **Deterministic Risk Gates**: Hard-limit risk management (max leverage 5x, max risk 1%, circuit breaker on 10% drawdown)
- **Multi-Factor Scoring**: 11 weighted factors including trend, volatility, entry quality, funding, and news sentiment
- **Market Regime Detection**: ADX/Choppiness-based regime classification blocks entries in ranging markets
- **News Integration**: RSS ingestion with optional LLM classification to block high-risk entries
- **Realistic Backtesting**: Event-driven simulation with variable slippage, fill probability, and funding settlements
- **Operator API**: FastAPI-based REST interface for monitoring and control
- **Multi-Mode Operation**: Paper trading, testnet, and live trading with identical logic

## Architecture Overview

```
                    +-------------------+
                    |    config.yaml    |
                    +--------+----------+
                             |
                    +--------v----------+
                    |    Main Loop      |
                    |    (src/main.py)  |
                    +--------+----------+
                             |
        +--------------------+--------------------+
        |          |         |         |         |
   +----v----+ +---v---+ +---v---+ +---v---+ +---v---+
   |Strategy | | Risk  | |Execute| |Ledger | |Monitor|
   | Loop    | |Engine | |Engine | |/State | |  API  |
   +---------+ +-------+ +-------+ +-------+ +-------+
        |          |         |         |         |
        +----------+---------+---------+---------+
                             |
                    +--------v----------+
                    |   Binance API     |
                    | (REST + WebSocket)|
                    +-------------------+
```

**Core Components:**
- `src/main.py` - Async runtime with concurrent loops (strategy, news, reconciliation, watchdog)
- `src/strategy/` - Signal generation, scoring engine, regime classification
- `src/risk/` - Deterministic risk evaluation with hard limits
- `src/execution/` - Order lifecycle management, paper simulation
- `src/ledger/` - Event sourcing (EventBus, EventLedger, StateManager)
- `src/backtester/` - Historical simulation engine
- `src/connectors/` - Binance REST/WebSocket clients, news ingestion
- `src/monitoring/` - Prometheus metrics, structured logging, alerts

## Installation

### Prerequisites

- Python 3.10+
- pip or pipx

### Install

```bash
# Clone repository
git clone <repository-url>
cd apollo

# Install with dependencies
pip install -e .

# For development (includes pytest, mypy, ruff)
pip install -e ".[dev]"

# For LLM news classification (optional)
pip install -e ".[llm]"
```

### Environment Setup

```bash
# Copy example environment
cp .env.example .env

# Edit with your API keys
# Required for testnet/live: BINANCE_API_KEY, BINANCE_API_SECRET
# Optional for news classification: OPENAI_API_KEY or ANTHROPIC_API_KEY
```

## Quick Start

### 1. Paper Trading (No API Keys Required)

```bash
# Uses default config.yaml (mode: paper)
bot
```

### 2. Testnet Trading

```bash
# In .env, set testnet API keys:
# BINANCE_TESTNET_API_KEY=your_key
# BINANCE_TESTNET_API_SECRET=your_secret

# In config.yaml, set:
#   run.mode: testnet
#   run.enable_trading: true

bot
```

### 3. Live Trading

```bash
# In .env, set production API keys:
# BINANCE_API_KEY=your_key
# BINANCE_API_SECRET=your_secret

# In config.yaml, set:
#   run.mode: live
#   run.enable_trading: true
#   run.live_confirm: YES_I_UNDERSTAND

bot
```

## Configuration

Configuration is loaded from `config.yaml` with environment variable overrides.

### Key Configuration Sections

| Section | Purpose |
|---------|---------|
| `run` | Mode (paper/testnet/live), trading enable, live confirmation |
| `binance` | API endpoints, WebSocket settings, recv_window |
| `strategy` | Indicators, entry/exit rules, scoring factors |
| `regime` | ADX/Choppiness thresholds for regime detection |
| `risk` | Risk per trade, leverage, drawdown limits, position limits |
| `universe` | Symbol selection criteria (volume, size) |
| `news` | News polling, risk classification, blocking duration |
| `execution` | Slippage limits, spread limits, order timeout |
| `monitoring` | Prometheus port, log level, alert webhooks |
| `reconciliation` | Interval, failure threshold |
| `watchdog` | Protective order verification interval |

See [Configuration Reference](docs/04_reference/01_Configuration.md) for complete documentation.

## Run Modes

| Mode | Orders | User Stream | Reconciliation | Use Case |
|------|--------|-------------|----------------|----------|
| `paper` | Simulated | Optional | Disabled | Strategy testing without API |
| `testnet` | Real (testnet) | Yes | Enabled | Integration testing with testnet funds |
| `live` | Real | Yes | Enabled | Production trading (requires confirmation) |

**Safety Gates:**
- `paper`: Never sends orders to Binance
- `testnet`: Requires `enable_trading: true` and testnet API keys
- `live`: Requires `enable_trading: true`, live API keys, AND `live_confirm: YES_I_UNDERSTAND`

## Backtesting

### Run a Backtest

```bash
# Single symbol
backtest --symbol BTCUSDT --interval 4h --data-path ./data/market

# Multiple symbols
backtest --symbols BTCUSDT,ETHUSDT --interval 4h --data-path ./data/market

# With output directory
backtest --symbol BTCUSDT --out-dir ./data/backtests/BTCUSDT

# Realistic execution model (default)
backtest --symbol BTCUSDT --execution-model realistic --random-seed 42

# Ideal execution model (no slippage/fill uncertainty)
backtest --symbol BTCUSDT --execution-model ideal
```

### Download Historical Data

```bash
# Download klines
python -m src.tools.download_klines --symbol BTCUSDT --interval 4h --start 2024-01-01

# Download exchange info
python -m src.tools.download_exchange_info

# Build universe snapshot
python -m src.tools.build_universe --min-volume 50000000 --size 5
```

See [Backtesting Guide](docs/05_backtester/02_RunningBacktests.md) for detailed documentation.

## CLI Tools

| Tool | Command | Purpose |
|------|---------|---------|
| Bot | `bot` | Run trading bot |
| Backtest | `backtest` | Run historical backtest |
| Ack Review | `ack-manual-review` | Clear manual intervention flag |
| Download Klines | `python -m src.tools.download_klines` | Fetch historical OHLCV data |
| Download Info | `python -m src.tools.download_exchange_info` | Fetch symbol rules |
| Build Universe | `python -m src.tools.build_universe` | Build tradeable symbol list |
| Validate Data | `python -m src.tools.validate_dataset` | Validate backtest data integrity |

See [CLI Tools Reference](docs/04_reference/04_CLITools.md) for complete documentation.

## Logs and Data

### File Locations

| Path | Content |
|------|---------|
| `data/ledger/events.jsonl` | Event ledger (all state changes) |
| `data/state/` | Persisted state (pending entries) |
| `data/market/` | Market data (klines CSV) |
| `logs/trades.csv` | Closed trade records |
| `logs/orders.csv` | Order history |
| `logs/thinking.jsonl` | Strategy decision logs |
| `logs/*.log` | Structured application logs |

### Reset Local State

Stop the bot and delete:

```bash
rm data/ledger/events.jsonl
rm data/ledger/sequence.txt
rm logs/orders.csv
rm logs/trades.csv
rm logs/thinking.jsonl
rm logs/bot.*.lock  # If stale lock remains
```

**Note:** This does NOT affect open positions or orders on Binance.

## Operations

### Monitoring

- **Prometheus Metrics**: `http://localhost:9090/metrics` (configurable port)
- **HTTP Logging**: Set `monitoring.log_http: true` in config.yaml

### Manual Intervention

If reconciliation or execution detects an issue, trading pauses until acknowledged:

```bash
ack-manual-review --reason "checked and resolved"
```

### TP/SL Orders

- Protective orders (take-profit/stop-loss) are placed after entry fills
- Fills are ingested via WebSocket user stream
- Watchdog verifies protective orders exist (every 5 minutes by default)

### Circuit Breaker

Trading halts automatically when:
- Drawdown exceeds `risk.max_drawdown_pct` (default: 10%)
- Consecutive losses exceed `risk.max_consecutive_losses` (default: 3)

Recovery requires manual acknowledgment via `ack-manual-review`.

## Documentation

| Document | Description |
|----------|-------------|
| [System Overview](docs/00_architecture/01_SystemOverview.md) | Architecture and design |
| [Module Reference](docs/00_architecture/02_ModuleReference.md) | Module-by-module breakdown |
| [RUNBOOK](RUNBOOK.md) | Operations reference |
| [Deployment Guide](docs/02_operations/01_Deployment.md) | Environment setup |
| [Monitoring Guide](docs/02_operations/02_Monitoring.md) | Observability reference |
| [Getting Started](docs/03_development/01_GettingStarted.md) | Developer onboarding |
| [Adding Strategies](docs/03_development/02_AddingStrategies.md) | Strategy development |
| [Event System](docs/03_development/03_EventSystem.md) | Event sourcing deep dive |
| [Configuration Reference](docs/04_reference/01_Configuration.md) | All config options |
| [API Reference](docs/04_reference/02_API.md) | Operator REST API |
| [Data Schemas](docs/04_reference/03_DataSchemas.md) | Data format reference |
| [CLI Tools](docs/04_reference/04_CLITools.md) | Tool documentation |
| [Backtester Overview](docs/05_backtester/01_Overview.md) | Backtest architecture |
| [Running Backtests](docs/05_backtester/02_RunningBacktests.md) | Backtest usage |

## License

MIT License - See [LICENSE](LICENSE) for details.
