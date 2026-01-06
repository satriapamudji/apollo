# System Overview

This document provides a comprehensive architectural overview of the Apollo, a production-grade algorithmic trading system for Binance USD-M perpetual futures.

## Table of Contents

- [System Purpose](#system-purpose)
- [High-Level Architecture](#high-level-architecture)
- [Core Components](#core-components)
- [Runtime Loops](#runtime-loops)
- [Data Flow](#data-flow)
- [Event-Driven Architecture](#event-driven-architecture)
- [Run Modes](#run-modes)
- [Safety Mechanisms](#safety-mechanisms)
- [Directory Structure](#directory-structure)

## System Purpose

The Apollo is an AI-assisted trend-following system designed for:

- **Automated Trading**: Executes trend-following strategies on Binance USD-M perpetual futures
- **Risk Management**: Enforces deterministic hard limits on leverage, position sizing, and drawdown
- **Multi-Environment Support**: Identical logic across paper, testnet, and live trading
- **Auditability**: Full event sourcing for complete state reconstruction and analysis
- **Realistic Backtesting**: Event-driven simulation with funding, slippage, and fill probability

## High-Level Architecture

```
                            +-----------------------+
                            |      config.yaml      |
                            |     + .env vars       |
                            +-----------+-----------+
                                        |
                            +-----------v-----------+
                            |    Configuration      |
                            |  (src/config/settings)|
                            +-----------+-----------+
                                        |
        +-------------------------------+-------------------------------+
        |                               |                               |
+-------v-------+              +--------v--------+             +--------v--------+
|   Connectors  |              |   Main Runtime  |             |   Monitoring    |
| (REST/WS/News)|              |  (src/main.py)  |             | (Prometheus/Log)|
+-------+-------+              +--------+--------+             +--------+--------+
        |                               |                               |
        |       +-----------------------+------------------------+      |
        |       |           |           |           |            |      |
        |  +----v----+ +----v----+ +----v----+ +----v-----+ +----v----+ |
        |  |Universe | |  News   | |Strategy | |Reconcile | |Watchdog | |
        |  |  Loop   | |  Loop   | |  Loop   | |  Loop    | |  Loop   | |
        |  +---------+ +---------+ +----+----+ +----------+ +---------+ |
        |                               |                               |
        |              +----------------+----------------+              |
        |              |                |                |              |
        |         +----v----+      +----v----+      +----v----+         |
        |         | Signal  |      |  Risk   |      |Portfolio|         |
        |         |Generator|      | Engine  |      |Selector |         |
        |         +---------+      +---------+      +----+----+         |
        |                                                |              |
        |                                           +----v----+         |
        |                                           |Execution|         |
        |                                           | Engine  |         |
        |                                           +----+----+         |
        |                                                |              |
+-------v-------+                                  +-----v-----+        |
|   Binance     |<---------------------------------|   Event   |--------+
|  REST + WS    |                                  |   Ledger  |
+---------------+                                  +-----------+
                                                         |
                                                   +-----v-----+
                                                   |   State   |
                                                   |  Manager  |
                                                   +-----------+
```

## Core Components

### Configuration (`src/config/settings.py`)

Pydantic-based configuration system with hierarchical validation:

- **Settings**: Root configuration object containing all subsystems
- **Loading Priority**: Environment variables > config.yaml > defaults
- **Validation**: Trading gates prevent unsafe mode combinations
- **Key Configs**: `RunConfig`, `StrategyConfig`, `RiskConfig`, `UniverseConfig`, `ExecutionConfig`

### Event Sourcing (`src/ledger/`)

All state changes are recorded as immutable events:

| Component | File | Purpose |
|-----------|------|---------|
| EventBus | `bus.py` | Pub/sub event distribution |
| EventLedger | `store.py` | Append-only JSONL persistence |
| StateManager | `state.py` | State reconstruction from events |
| Event Types | `events.py` | 20+ event type definitions |

### Strategy Engine (`src/strategy/`)

Multi-timeframe trend-following with composite scoring:

| Component | File | Purpose |
|-----------|------|---------|
| SignalGenerator | `signals.py` | Entry/exit signal generation |
| ScoringEngine | `scoring.py` | 11-factor composite scoring |
| RegimeClassifier | `regime.py` | ADX/Choppiness regime detection |
| UniverseSelector | `universe.py` | Symbol universe selection |
| PortfolioSelector | `portfolio.py` | Cross-sectional trade ranking |

### Risk Engine (`src/risk/engine.py`)

Deterministic hard-limit risk evaluation:

| Limit | Default | Description |
|-------|---------|-------------|
| Max Risk/Trade | 1.0% | Maximum equity risked per position |
| Max Leverage | 5x | Hard leverage cap |
| Max Daily Loss | 3.0% | Daily loss circuit breaker |
| Max Drawdown | 10.0% | Peak-to-trough drawdown limit |
| Max Consecutive Losses | 3 | Loss streak circuit breaker |
| Max Positions | 1 | Concurrent position limit |

### Execution Engine (`src/execution/engine.py`)

Order lifecycle management:

- **Entry/Exit Execution**: Places orders with proper sizing and pricing
- **Protective Orders**: Auto-places TP/SL after entry fills
- **Trailing Stops**: Dynamic stop updates based on price movement
- **Paper Simulation**: Realistic fill simulation with slippage
- **State Persistence**: Pending entries survive restarts

### Connectors (`src/connectors/`)

External service adapters:

| Connector | File | Purpose |
|-----------|------|---------|
| REST Client | `rest_client.py` | Binance REST API (signed requests, rate limiting) |
| WebSocket | `ws_client.py` | Real-time market data and user stream |
| News Ingester | `news.py` | RSS feed polling |
| News Classifier | `news_classifier.py` | Rule-based or LLM classification |
| LLM Adapter | `llm.py` | OpenAI/Anthropic integration |

### Backtester (`src/backtester/`)

Historical simulation engine:

| Component | File | Purpose |
|-----------|------|---------|
| Runner | `runner.py` | CLI interface |
| Engine | `engine.py` | Core backtest logic |
| ReplayEngine | `replay_engine.py` | Event-driven multi-symbol replay |
| ExecutionSim | `execution_sim.py` | Realistic fill simulation |
| Funding | `funding.py` | Funding rate settlements |
| Reporting | `reporting.py` | Performance metrics |

## Runtime Loops

The main runtime (`src/main.py:732-742`) executes 8 concurrent async loops:

```python
await asyncio.gather(
    universe_loop(),       # Symbol universe refresh (24h)
    news_loop(),           # News ingestion & classification (15min)
    strategy_loop(),       # Signal generation & execution (15min)
    reconciliation_loop(), # Exchange state verification (30min)
    user_stream.run(),     # WebSocket order/account updates
    watchdog_loop(),       # Protective order verification (5min)
    api_server(),          # Operator REST API
    telemetry_loop(),      # Metrics & daily summaries (5min)
)
```

### Universe Loop (`src/main.py:294-324`)
- **Frequency**: Every 24 hours (retry every 5 minutes on failure)
- **Purpose**: Refresh tradeable symbol universe
- **Events**: `UNIVERSE_UPDATED`

### News Loop (`src/main.py:326-361`)
- **Frequency**: Every 15 minutes
- **Purpose**: Ingest and classify cryptocurrency news
- **Events**: `NEWS_INGESTED`, `NEWS_CLASSIFIED`

### Strategy Loop (`src/main.py:413-672`)
- **Frequency**: Every 15 minutes
- **Purpose**: Generate signals, evaluate risk, execute trades
- **Phases**:
  1. Collect candidates from universe
  2. Cross-sectional portfolio selection
  3. Execute selected trades
  4. Emit cycle summary
- **Events**: `SIGNAL_COMPUTED`, `TRADE_PROPOSED`, `RISK_APPROVED/REJECTED`, `TRADE_CYCLE_COMPLETED`

### Reconciliation Loop (`src/main.py:363-411`)
- **Frequency**: Every 30 minutes
- **Purpose**: Verify internal state matches Binance account
- **Events**: `RECONCILIATION_COMPLETED`, `MANUAL_INTERVENTION` (on discrepancy)

### User Stream (`src/execution/user_stream.py`)
- **Frequency**: Continuous WebSocket
- **Purpose**: Real-time order fills and account updates
- **Events**: `ORDER_FILLED`, `ORDER_CANCELLED`, `POSITION_CLOSED`

### Watchdog Loop (`src/main.py:674-685`)
- **Frequency**: Every 5 minutes (configurable)
- **Purpose**: Verify protective orders (TP/SL) exist on exchange
- **Action**: Auto-recover missing orders if enabled

### API Server (`src/main.py:687-704`)
- **Port**: 8000 (configurable)
- **Purpose**: Operator REST interface for monitoring and control

### Telemetry Loop (`src/main.py:706-730`)
- **Frequency**: Every 5 minutes
- **Purpose**: Update Prometheus metrics, generate daily summaries

## Data Flow

### Entry Flow

```
Market Data (Binance REST)
    |
    v
+-------------------+
| Fetch 1D + 4H     |
| Klines            |
+--------+----------+
         |
         v
+--------+----------+
| Feature Pipeline  |
| (EMA, ATR, RSI,   |
|  ADX, Chop)       |
+--------+----------+
         |
         v
+--------+----------+
| Regime Classifier |
| (TRENDING/CHOPPY) |
+--------+----------+
         |
         v
+--------+----------+
| Signal Generator  |
| (LONG/SHORT/NONE) |
+--------+----------+
         |
         v
+--------+----------+
| Scoring Engine    |
| (11 factors)      |
+--------+----------+
         |
         v
+--------+----------+
| Risk Engine       |
| (hard limits)     |
+--------+----------+
         |
    [APPROVED?]
         |
    +----+----+
    |         |
   YES        NO
    |         |
    v         v
+---+---+  +--+---+
|Portfo-|  |EVENT:|
|lio    |  |RISK_ |
|Select |  |REJECT|
+---+---+  +------+
    |
    v
+---+----------+
| Execution    |
| Engine       |
+---+----------+
    |
    v
+---+----------+
| Binance API  |
| (place order)|
+--------------+
```

### Exit Flow

```
Open Position
    |
    +-------------------+-------------------+
    |                   |                   |
    v                   v                   v
+--------+        +----------+        +---------+
| Signal |        | Trailing |        | TP/SL   |
| EXIT   |        | Stop Hit |        | Fill    |
+--------+        +----------+        +---------+
    |                   |                   |
    +-------------------+-------------------+
                        |
                        v
               +--------+--------+
               | Execution Engine|
               | (close position)|
               +--------+--------+
                        |
                        v
               +--------+--------+
               | EVENT:          |
               | POSITION_CLOSED |
               +-----------------+
```

## Event-Driven Architecture

### Event Flow

```
+------------+      +------------+      +------------+
|  Producer  |----->|  EventBus  |----->|  Handlers  |
| (any loop) |      | (publish)  |      | (multiple) |
+------------+      +-----+------+      +------------+
                          |
                    +-----v------+
                    | EventLedger|
                    | (persist)  |
                    +-----+------+
                          |
                    +-----v------+
                    |StateManager|
                    | (rebuild)  |
                    +------------+
```

### Key Event Types

| Category | Events |
|----------|--------|
| Market | `MARKET_TICK`, `CANDLE_CLOSE`, `FUNDING_UPDATE` |
| News | `NEWS_INGESTED`, `NEWS_CLASSIFIED` |
| Universe | `UNIVERSE_UPDATED`, `SYMBOL_FILTERED` |
| Signals | `SIGNAL_COMPUTED`, `TRADE_PROPOSED` |
| Risk | `RISK_APPROVED`, `RISK_REJECTED` |
| Execution | `ORDER_PLACED`, `ORDER_FILLED`, `ORDER_CANCELLED`, `ORDER_PARTIAL_FILL` |
| Position | `POSITION_OPENED`, `POSITION_CLOSED` |
| System | `SYSTEM_STARTED`, `CIRCUIT_BREAKER_TRIGGERED`, `MANUAL_INTERVENTION`, `RECONCILIATION_COMPLETED` |

## Run Modes

### Paper Mode
- **Orders**: Simulated locally (never sent to Binance)
- **User Stream**: Optional (disabled by default)
- **Reconciliation**: Disabled (no exchange state to compare)
- **Use Case**: Strategy development and testing

### Testnet Mode
- **Orders**: Real orders to Binance Testnet
- **User Stream**: Enabled
- **Reconciliation**: Enabled
- **Use Case**: Integration testing with fake funds
- **Requirements**: `enable_trading: true`, testnet API keys

### Live Mode
- **Orders**: Real orders to Binance Production
- **User Stream**: Enabled
- **Reconciliation**: Enabled
- **Use Case**: Production trading
- **Requirements**: `enable_trading: true`, live API keys, `live_confirm: YES_I_UNDERSTAND`

## Safety Mechanisms

### Trading Gates (`src/config/settings.py`)
- Paper mode can never send real orders
- Testnet requires explicit `enable_trading: true`
- Live requires `enable_trading: true` AND `live_confirm: YES_I_UNDERSTAND`

### Risk Limits (`src/risk/engine.py`)
- Hard limits are never bypassed (no soft limits)
- Max leverage capped at 5x regardless of exchange allowance
- Risk per trade capped at 1% of equity

### Circuit Breaker
- Triggers on max drawdown (10%) or consecutive losses (3)
- Halts all trading until manual acknowledgment
- Cancels open orders and exits positions on trigger

### Reconciliation (`src/main.py:745-783`)
- Compares internal state with Binance account every 30 minutes
- Detects position/order/equity discrepancies
- Triggers `MANUAL_INTERVENTION` on mismatch
- Consecutive failures trigger alert at threshold

### Watchdog (`src/main.py:674-685`)
- Verifies protective orders (TP/SL) exist on exchange
- Auto-recovers missing orders if `auto_recover: true`
- Runs every 5 minutes by default

### Single Instance Lock (`src/utils/single_instance.py`)
- Prevents multiple bot instances from running concurrently
- Lock file at `logs/bot.<mode>.lock`

## Directory Structure

```
apollo/
├── src/
│   ├── main.py              # Entry point, runtime loops
│   ├── models.py            # Core data models
│   ├── config/              # Configuration system
│   │   └── settings.py      # Pydantic settings
│   ├── strategy/            # Signal generation
│   │   ├── signals.py       # SignalGenerator
│   │   ├── scoring.py       # ScoringEngine
│   │   ├── regime.py        # RegimeClassifier
│   │   ├── universe.py      # UniverseSelector
│   │   ├── portfolio.py     # PortfolioSelector
│   │   └── package.py       # Strategy spec system
│   ├── risk/                # Risk management
│   │   ├── engine.py        # RiskEngine
│   │   └── sizing.py        # Position sizing
│   ├── execution/           # Order execution
│   │   ├── engine.py        # ExecutionEngine
│   │   ├── paper_simulator.py
│   │   ├── state_store.py   # Pending entry persistence
│   │   └── user_stream.py   # WebSocket handler
│   ├── ledger/              # Event sourcing
│   │   ├── events.py        # Event type definitions
│   │   ├── bus.py           # EventBus
│   │   ├── store.py         # EventLedger
│   │   └── state.py         # StateManager
│   ├── connectors/          # External services
│   │   ├── rest_client.py   # Binance REST
│   │   ├── ws_client.py     # WebSocket client
│   │   ├── news.py          # News ingestion
│   │   ├── news_classifier.py
│   │   └── llm.py           # LLM adapter
│   ├── features/            # Technical indicators
│   │   ├── indicators.py
│   │   └── pipeline.py
│   ├── monitoring/          # Observability
│   │   ├── metrics.py       # Prometheus
│   │   ├── logging.py       # Structured logging
│   │   ├── trade_log.py     # Trade CSV
│   │   ├── order_log.py     # Order CSV
│   │   ├── thinking_log.py  # Strategy decisions
│   │   └── alert_webhooks.py
│   ├── backtester/          # Historical simulation
│   │   ├── runner.py        # CLI
│   │   ├── engine.py        # Core logic
│   │   ├── replay_engine.py # Event-driven replay
│   │   └── reporting.py     # Performance metrics
│   ├── api/                 # Operator interface
│   │   └── operator.py      # FastAPI app
│   ├── tools/               # CLI utilities
│   │   ├── download_klines.py
│   │   ├── download_exchange_info.py
│   │   └── ...
│   └── utils/               # Utilities
│       ├── binance.py
│       └── single_instance.py
├── strategies/              # Strategy specifications
│   └── <name>/strategy.md   # YAML front matter
├── data/                    # Runtime data
│   ├── ledger/              # Event ledger
│   ├── state/               # Persisted state
│   └── market/              # Market data CSV
├── logs/                    # Runtime logs
│   ├── trades.csv
│   ├── orders.csv
│   └── thinking.jsonl
├── tests/                   # Test suite
├── config.yaml              # Main configuration
├── pyproject.toml           # Package metadata
└── README.md                # Project overview
```

## Related Documentation

- [Module Reference](02_ModuleReference.md) - Detailed module breakdown
- [Configuration Reference](../04_reference/01_Configuration.md) - All config options
- [Event System](../03_development/03_EventSystem.md) - Event sourcing deep dive
- [RUNBOOK](../../RUNBOOK.md) - Operations reference
