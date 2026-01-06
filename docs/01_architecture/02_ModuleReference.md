# Module Reference

Detailed breakdown of each module in the Apollo codebase.

## Table of Contents

- [Overview](#overview)
- [Core Modules](#core-modules)
- [Strategy Module](#strategy-module)
- [Risk Module](#risk-module)
- [Execution Module](#execution-module)
- [Ledger Module](#ledger-module)
- [Connectors Module](#connectors-module)
- [Features Module](#features-module)
- [Monitoring Module](#monitoring-module)
- [Backtester Module](#backtester-module)
- [Data Module](#data-module)
- [API Module](#api-module)
- [Tools Module](#tools-module)
- [Utils Module](#utils-module)
- [Module Dependencies](#module-dependencies)

---

## Overview

The codebase is organized into functional modules under `src/`:

```
src/
├── main.py              # Application entry point
├── models.py            # Core data models
├── config/              # Configuration management
├── strategy/            # Signal generation & scoring
├── risk/                # Risk management
├── execution/           # Order execution
├── ledger/              # Event sourcing
├── connectors/          # External services
├── features/            # Technical indicators
├── monitoring/          # Observability
├── backtester/          # Historical simulation
├── data/                # Data models
├── api/                 # Operator interface
├── tools/               # CLI utilities
└── utils/               # Helpers
```

---

## Core Modules

### Entry Point (`src/main.py`)

**Purpose**: Main runtime loop coordinating all async tasks.

**Key Functions**:
- `main()` - Entry point, calls `main_async()`
- `main_async()` - Async runtime orchestrating all loops
- `_reconcile()` - Exchange state verification
- `_recover_pending_entries()` - Restore pending orders on restart
- `_kill_switch()` - Emergency position closure

**Runtime Loops**:
| Loop | Frequency | Purpose |
|------|-----------|---------|
| `universe_loop()` | 24h | Refresh symbol universe |
| `news_loop()` | 15min | Ingest and classify news |
| `strategy_loop()` | 15min | Generate signals, execute trades |
| `reconciliation_loop()` | 30min | Verify exchange state |
| `user_stream.run()` | Continuous | WebSocket order updates |
| `watchdog_loop()` | 5min | Verify protective orders |
| `api_server()` | Continuous | Operator REST API |
| `telemetry_loop()` | 5min | Metrics and daily summaries |

**Line Count**: ~855 lines

---

### Core Models (`src/models.py`)

**Purpose**: Shared data models used across modules.

**Key Classes**:

```python
@dataclass
class TradeProposal:
    """Proposed trade with entry details."""
    symbol: str
    side: str                    # "LONG" | "SHORT"
    entry_price: float
    stop_price: float
    take_profit: float | None
    atr: float
    leverage: int
    score: CompositeScore | None
    funding_rate: float
    news_risk: str              # "LOW" | "MEDIUM" | "HIGH"
    trade_id: str
    created_at: datetime
    candle_timestamp: datetime
```

**Line Count**: ~60 lines

---

### Configuration (`src/config/settings.py`)

**Purpose**: Pydantic-based configuration with YAML + env var support.

**Key Classes**:
- `Settings` - Root configuration object
- `RunConfig` - Trading mode settings
- `StrategyConfig` - Strategy parameters
- `RiskConfig` - Risk limits
- `ExecutionConfig` - Order execution settings
- `BacktestConfig` - Backtest parameters
- `MonitoringConfig` - Observability settings

**Key Functions**:
- `load_settings(config_path)` - Load from YAML + env vars
- `create_default_config(path)` - Generate default config

**Line Count**: ~730 lines

---

## Strategy Module

**Location**: `src/strategy/`

### Signal Generator (`signals.py`)

**Purpose**: Generate entry/exit signals from market data.

**Key Classes**:

```python
class SignalGenerator:
    """Multi-timeframe signal generation."""

    def generate(
        self,
        symbol: str,
        daily_df: pd.DataFrame,
        fourh_df: pd.DataFrame,
        funding_rate: float,
        news_risk: str,
        open_position: Position | None,
        current_time: datetime
    ) -> Signal:
        """Generate signal for symbol."""

@dataclass(frozen=True)
class Signal:
    symbol: str
    signal_type: SignalType      # LONG, SHORT, EXIT, NONE
    score: CompositeScore | None
    price: float
    atr: float
    entry_price: float | None
    stop_price: float | None
    take_profit: float | None
    reason: str | None
    trade_id: str | None
    timestamp: datetime | None
    regime: RegimeClassification | None
```

**Signal Types**:
- `LONG` - Long entry signal
- `SHORT` - Short entry signal
- `EXIT` - Exit open position
- `NONE` - No action

---

### Scoring Engine (`scoring.py`)

**Purpose**: Multi-factor composite scoring for entry decisions.

**Key Classes**:

```python
class ScoringEngine:
    """11-factor scoring system."""

    def compute(
        self,
        direction: str,
        price: float,
        ema_fast: float,
        ema_slow: float,
        atr: float,
        entry_distance_atr: float,
        funding_rate: float,
        news_risk: str,
        volume_ratio: float,
        crowding_data: dict | None
    ) -> CompositeScore:
        """Compute composite score."""

@dataclass(frozen=True)
class CompositeScore:
    trend_score: float
    volatility_score: float
    entry_quality: float
    funding_penalty: float
    news_modifier: float
    liquidity_score: float
    crowding_score: float
    funding_volatility_score: float
    oi_expansion_score: float
    taker_imbalance_score: float
    volume_score: float
    composite: float             # Final score [0.0, 1.0]
```

**Scoring Factors** (default weights):
| Factor | Weight | Description |
|--------|--------|-------------|
| Trend | 0.35 | EMA alignment + momentum |
| Volatility | 0.15 | ATR% regime scoring |
| Entry Quality | 0.25 | Distance from breakout level |
| Funding | 0.10 | Direction-aligned funding |
| News | 0.15 | News sentiment modifier |

---

### Regime Classifier (`regime.py`)

**Purpose**: Classify market regime using ADX and Choppiness Index.

**Key Classes**:

```python
class RegimeClassifier:
    """ADX/Choppiness-based regime detection."""

    def classify(
        self,
        adx: float,
        choppiness: float,
        atr_pct: float | None,
        atr_sma: float | None
    ) -> RegimeClassification:
        """Classify current market regime."""

@dataclass(frozen=True)
class RegimeClassification:
    regime: RegimeType           # TRENDING, CHOPPY, TRANSITIONAL
    adx: float
    chop: float
    blocks_entry: bool
    size_multiplier: float
    volatility_regime: VolatilityRegimeType | None
```

**Regime Types**:
| Regime | ADX | Chop | Entry | Size |
|--------|-----|------|-------|------|
| TRENDING | >= 25 | <= 50 | Yes | 100% |
| CHOPPY | <= 20 | >= 61.8 | No | 0% |
| TRANSITIONAL | Between | Between | Yes | 50% |

---

### Universe Selector (`universe.py`)

**Purpose**: Select tradeable symbol universe from Binance.

**Key Classes**:

```python
class UniverseSelector:
    """Symbol universe selection based on volume and filters."""

    async def select_with_filtering(self) -> UniverseSelectionResult:
        """Select universe with detailed filtering info."""

class UniverseSelectionResult:
    selected: list[UniverseSymbol]
    filtered: list[FilteredSymbol]
    timestamp: datetime
```

**Selection Criteria**:
- Minimum 24h quote volume
- Maximum minimum notional
- Maximum funding rate
- USDT perpetuals only

---

### Portfolio Selector (`portfolio.py`)

**Purpose**: Cross-sectional ranking and selection of trade candidates.

**Key Classes**:

```python
class PortfolioSelector:
    """Cross-sectional trade selection."""

    def select(
        self,
        candidates: list[TradeCandidate],
        current_positions: dict[str, Position],
        blocked_symbols: set[str],
        state: TradingState
    ) -> list[TradeCandidate]:
        """Select top candidates for execution."""

@dataclass
class TradeCandidate:
    symbol: str
    proposal: TradeProposal
    risk_result: RiskCheckResult
    score: CompositeScore | None
    funding_rate: float
    news_risk: str
    candle_timestamp: pd.Timestamp
    selected: bool = False
    rank: int | None = None
```

---

### Strategy Package (`package.py`)

**Purpose**: Load and validate strategy specifications from YAML.

**Key Functions**:
- `load_strategy_spec(name)` - Load strategy spec from `strategies/<name>/strategy.md`
- `validate_strategy_config(spec, config)` - Validate config against spec
- `validate_data_requirements(spec, intervals, has_funding)` - Validate data availability

**Strategy Spec Format**:
```yaml
---
name: trend_following_v1
version: 1
requires:
  bars:
    - series: trade
      interval: 4h
  derived:
    - interval: 1d
      from: 4h
  funding: true
parameters:
  ema_fast:
    type: int
    default: 8
    min: 2
    max: 50
---
# Strategy description in markdown
```

---

## Risk Module

**Location**: `src/risk/`

### Risk Engine (`engine.py`)

**Purpose**: Deterministic risk evaluation with hard limits.

**Key Classes**:

```python
class RiskEngine:
    """Hard-limit risk evaluation."""

    def evaluate(
        self,
        state: TradingState,
        proposal: TradeProposal,
        symbol_filters: SymbolFilters | None,
        now: datetime
    ) -> RiskCheckResult:
        """Evaluate trade proposal against risk limits."""

@dataclass
class RiskCheckResult:
    approved: bool
    reasons: list[str]           # Rejection reasons
    adjusted_leverage: int | None
    adjusted_quantity: float | None
    circuit_breaker: bool
```

**Risk Checks**:
- Position count limit
- Daily loss limit
- Drawdown circuit breaker
- Stop-loss distance validation
- Leverage limits
- Funding rate limits
- Spread constraints

---

### Position Sizing (`sizing.py`)

**Purpose**: Calculate position size based on risk parameters.

**Key Functions**:
- `calculate_position_size()` - ATR-based position sizing
- `apply_symbol_filters()` - Round to valid lot size

---

## Execution Module

**Location**: `src/execution/`

### Execution Engine (`engine.py`)

**Purpose**: Order lifecycle management and execution.

**Key Classes**:

```python
class ExecutionEngine:
    """Order execution and lifecycle management."""

    async def execute_entry(
        self,
        proposal: TradeProposal,
        risk_result: RiskCheckResult,
        filters: SymbolFilters | None,
        state: TradingState
    ) -> ExecutionResult:
        """Execute entry order."""

    async def execute_exit(
        self,
        position: Position,
        price: float,
        reason: str
    ) -> ExecutionResult:
        """Execute exit order."""

    async def update_trailing_stop(
        self,
        position: Position,
        current_price: float,
        atr: float,
        tick_size: float
    ) -> None:
        """Update trailing stop if conditions met."""
```

**Execution Modes**:
- **Live/Testnet**: Real orders to Binance
- **Paper**: Simulated with realistic fill behavior

---

### Paper Simulator (`paper_simulator.py`)

**Purpose**: Realistic order simulation for paper trading.

**Features**:
- Variable slippage (ATR-scaled)
- Stochastic fill probability
- Partial fills
- Fee calculation
- Real book ticker integration

---

### State Store (`state_store.py`)

**Purpose**: Persist pending entry context across restarts.

**Key Classes**:

```python
class PendingEntryStore:
    """JSON file persistence for pending entries."""

    def save(self, client_order_id: str, entry: PendingEntry) -> None
    def load(self, client_order_id: str) -> PendingEntry | None
    def remove(self, client_order_id: str) -> None
    def load_all(self) -> dict[str, PendingEntry]
```

---

### User Stream (`user_stream.py`)

**Purpose**: WebSocket handler for Binance user data stream.

**Key Classes**:

```python
class UserDataStream:
    """WebSocket user data stream handler."""

    async def run(self) -> None:
        """Connect and process user data events."""
```

**Events Handled**:
- `ORDER_TRADE_UPDATE` - Order fills, cancellations
- `ACCOUNT_UPDATE` - Account balance changes

---

## Ledger Module

**Location**: `src/ledger/`

### Event Types (`events.py`)

**Purpose**: Define all event types for event sourcing.

**Event Categories**:

| Category | Events |
|----------|--------|
| System | `SYSTEM_STARTED`, `SHUTDOWN_INITIATED` |
| Market | `MARKET_TICK`, `CANDLE_CLOSE`, `FUNDING_UPDATE` |
| News | `NEWS_INGESTED`, `NEWS_CLASSIFIED` |
| Universe | `UNIVERSE_UPDATED`, `SYMBOL_FILTERED` |
| Signals | `SIGNAL_COMPUTED`, `TRADE_PROPOSED` |
| Risk | `RISK_APPROVED`, `RISK_REJECTED` |
| Execution | `ORDER_PLACED`, `ORDER_FILLED`, `ORDER_CANCELLED`, `ORDER_PARTIAL_FILL` |
| Position | `POSITION_OPENED`, `POSITION_CLOSED` |
| Account | `ACCOUNT_SETTING_UPDATED`, `ACCOUNT_SETTING_FAILED` |
| Alert | `MANUAL_INTERVENTION`, `CIRCUIT_BREAKER_TRIGGERED` |
| Reconciliation | `RECONCILIATION_COMPLETED` |
| Cycle | `TRADE_CYCLE_COMPLETED` |

---

### Event Bus (`bus.py`)

**Purpose**: Pub/sub event distribution.

**Key Classes**:

```python
class EventBus:
    """Publish/subscribe event bus."""

    def register(
        self,
        event_type: EventType,
        handler: Callable[[Event], Awaitable[None]]
    ) -> None:
        """Register handler for event type."""

    async def publish(
        self,
        event_type: EventType,
        payload: dict,
        metadata: dict | None = None
    ) -> Event:
        """Publish event to all registered handlers."""
```

---

### Event Ledger (`store.py`)

**Purpose**: Append-only JSONL event persistence.

**Key Classes**:

```python
class EventLedger:
    """Append-only event storage."""

    async def append(self, event: Event) -> None:
        """Append event to ledger."""

    def load_all(self) -> list[Event]:
        """Load all events from ledger."""
```

**Storage Format**: JSONL (one JSON object per line)

---

### State Manager (`state.py`)

**Purpose**: State reconstruction from events.

**Key Classes**:

```python
class StateManager:
    """Manages trading state from events."""

    def rebuild(self, events: list[Event]) -> None:
        """Rebuild state from event history."""

    def apply_event(self, event: Event) -> None:
        """Apply single event to state."""

    @property
    def state(self) -> TradingState:
        """Get current trading state."""

@dataclass
class TradingState:
    equity: float
    positions: dict[str, Position]
    open_orders: dict[str, Order]
    daily_pnl: float
    max_drawdown: float
    consecutive_losses: int
    universe: list[str]
    requires_manual_review: bool
    circuit_breaker_active: bool
```

---

## Connectors Module

**Location**: `src/connectors/`

### REST Client (`rest_client.py`)

**Purpose**: Async Binance REST API client.

**Key Classes**:

```python
class BinanceRestClient:
    """Async Binance Futures REST client."""

    # Market Data
    async def get_klines(symbol, interval, limit) -> list
    async def get_funding_rate(symbol) -> float
    async def get_exchange_info() -> dict
    async def get_book_ticker(symbol) -> dict

    # Account
    async def get_account_info() -> dict
    async def get_position_risk() -> list
    async def get_open_orders() -> list

    # Trading
    async def place_order(...) -> dict
    async def cancel_order(symbol, order_id) -> dict

    # Account Settings
    async def set_leverage(symbol, leverage) -> dict
    async def set_margin_type(symbol, margin_type) -> dict
```

**Features**:
- Rate limiting (1200 weight/minute)
- Request signing (HMAC-SHA256)
- Server time synchronization
- Automatic endpoint selection (testnet/live)

---

### WebSocket Client (`ws_client.py`)

**Purpose**: WebSocket connection management.

**Features**:
- Auto-reconnect
- Keepalive pings
- Message buffering

---

### News Ingester (`news.py`)

**Purpose**: RSS feed polling for cryptocurrency news.

**Key Classes**:

```python
class NewsIngester:
    """RSS news feed ingester."""

    async def fetch_async(self) -> list[NewsItem]:
        """Fetch news from all enabled sources."""
```

---

### News Classifier (`news_classifier.py`)

**Purpose**: Classify news risk level.

**Implementations**:
- `RuleBasedNewsClassifier` - Keyword-based classification
- `LLMNewsClassifierAdapter` - LLM-based classification

**Risk Levels**: `LOW`, `MEDIUM`, `HIGH`

---

### LLM Adapter (`llm.py`)

**Purpose**: OpenAI/Anthropic API integration.

**Key Classes**:

```python
class LLMClassifier:
    """LLM API adapter."""

    async def classify(self, prompt: str) -> str:
        """Send classification request to LLM."""
```

---

## Features Module

**Location**: `src/features/`

### Indicators (`indicators.py`)

**Purpose**: Technical indicator calculations.

**Functions**:
- `compute_ema(series, period)` - Exponential Moving Average
- `compute_atr(high, low, close, period)` - Average True Range
- `compute_rsi(close, period)` - Relative Strength Index
- `compute_adx(high, low, close, period)` - Average Directional Index
- `compute_choppiness(high, low, close, period)` - Choppiness Index
- `compute_volume_sma(volume, period)` - Volume SMA

---

### Feature Pipeline (`pipeline.py`)

**Purpose**: Batch computation of all indicators.

**Key Classes**:

```python
class FeaturePipeline:
    """Compute all technical features."""

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add all indicator columns to DataFrame."""
```

**Output Columns**:
- `ema_fast`, `ema_slow`
- `atr`, `atr_pct`
- `rsi`
- `adx`, `plus_di`, `minus_di`
- `chop`
- `volume_sma`, `volume_ratio`

---

## Monitoring Module

**Location**: `src/monitoring/`

### Metrics (`metrics.py`)

**Purpose**: Prometheus metrics collection.

**Key Metrics**:
| Metric | Type | Description |
|--------|------|-------------|
| `loop_last_tick_age_sec` | Gauge | Loop health indicator |
| `ws_connected` | Gauge | WebSocket status |
| `ws_last_message_age_sec` | Gauge | WS message age |
| `open_positions` | Gauge | Position count |
| `daily_pnl_pct` | Gauge | Daily P&L |
| `max_drawdown_pct` | Gauge | Peak drawdown |
| `rest_request_latency_seconds` | Histogram | API latency |
| `reconciliation_consecutive_failures` | Gauge | Recon failures |

---

### Logging (`logging.py`)

**Purpose**: Structured logging setup.

**Functions**:
- `configure_logging(level, path, config)` - Initialize logging

---

### Trade Logger (`trade_log.py`)

**Purpose**: CSV trade record logging.

**Output**: `logs/trades.csv`

---

### Order Logger (`order_log.py`)

**Purpose**: CSV order history logging.

**Output**: `logs/orders.csv`

---

### Thinking Logger (`thinking_log.py`)

**Purpose**: Strategy decision logging.

**Output**: `logs/thinking.jsonl`

---

### Alert Webhooks (`alert_webhooks.py`)

**Purpose**: Send alerts to external webhooks.

**Trigger Events**:
- `MANUAL_INTERVENTION`
- `CIRCUIT_BREAKER_TRIGGERED`

---

### Performance Telemetry (`performance_telemetry.py`)

**Purpose**: Daily performance summaries.

---

### Event Console (`event_console.py`)

**Purpose**: Console output for key events.

---

## Backtester Module

**Location**: `src/backtester/`

### Runner (`runner.py`)

**Purpose**: CLI interface for backtesting.

**Usage**:
```bash
backtest --symbol BTCUSDT --interval 4h --data-path ./data/market
```

---

### Engine (`engine.py`)

**Purpose**: Core backtest logic.

**Key Classes**:

```python
class BacktestEngine:
    """Single-symbol backtest execution."""

    def run(
        self,
        df: pd.DataFrame,
        initial_equity: float
    ) -> BacktestResult:
        """Run backtest on historical data."""
```

---

### Replay Engine (`replay_engine.py`)

**Purpose**: Multi-symbol event-driven backtest.

**Features**:
- Unified event stream across symbols
- Deterministic ordering
- Strategy/risk/execution integration

---

### Execution Simulation (`execution_sim.py`)

**Purpose**: Realistic fill simulation.

**Models**:
- Variable slippage (ATR-scaled)
- Stochastic fill probability
- Partial fills

---

### Funding (`funding.py`)

**Purpose**: Funding rate settlement modeling.

**Models**:
- Discrete settlements (every 8h)
- Constant rate stress testing

---

### Spread (`spread.py`)

**Purpose**: Bid-ask spread modeling.

**Models**:
- Conservative (ATR-scaled)
- Historical data provider
- Hybrid model

---

### Reporting (`reporting.py`)

**Purpose**: Performance metrics calculation.

**Metrics**:
- Sharpe ratio
- Max drawdown
- Win rate
- Profit factor
- Trade statistics

---

### Data (`data.py`)

**Purpose**: Data loading utilities.

**Functions**:
- `load_ohlcv_csv(path, interval)` - Load kline CSV
- `load_funding_csv(symbol, path)` - Load funding rates
- `load_spread_csv(symbol, path)` - Load spread data

---

### Events (`events.py`)

**Purpose**: Backtest event definitions.

**Event Types**:
- `BarEvent` - OHLCV bar close
- `FundingEvent` - Funding settlement
- `SpreadEvent` - Spread snapshot
- `UniverseEvent` - Universe update

---

## Data Module

**Location**: `src/data/`

### Models (`models.py`)

**Purpose**: Data schema definitions.

**Key Classes**:
- `UniverseSymbol` - Symbol with 24h metrics
- `UniverseSnapshot` - Complete universe snapshot
- `SymbolRules` - Trading rules from exchangeInfo
- `DatasetManifest` - Backtest dataset specification
- `ExecutionAssumptions` - Execution model parameters

---

### Crowding (`crowding.py`)

**Purpose**: Crowding and sentiment data.

**Data Sources** (public Binance):
- Long/short ratio
- Open interest
- Taker buy/sell volume

---

## API Module

**Location**: `src/api/`

### Operator API (`operator.py`)

**Purpose**: FastAPI REST interface for monitoring and control.

**Endpoints**:
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/state` | GET | Current trading state |
| `/positions` | GET | Open positions |
| `/orders` | GET | Open orders |
| `/events` | GET | Recent events |
| `/config` | GET | Active configuration |
| `/pause` | POST | Pause trading |
| `/resume` | POST | Resume trading |

---

## Tools Module

**Location**: `src/tools/`

### Download Klines (`download_klines.py`)

**Purpose**: Download historical OHLCV data.

```bash
python -m src.tools.download_klines --symbol BTCUSDT --interval 4h --start 2024-01-01
```

---

### Download Exchange Info (`download_exchange_info.py`)

**Purpose**: Download symbol trading rules.

```bash
python -m src.tools.download_exchange_info
```

---

### Build Universe (`build_universe.py`)

**Purpose**: Build tradeable symbol universe.

```bash
python -m src.tools.build_universe --min-volume 50000000 --size 5
```

---

### Validate Dataset (`validate_dataset.py`)

**Purpose**: Validate backtest data integrity.

```bash
python -m src.tools.validate_dataset --data-path ./data/market
```

---

### Normalize Klines (`normalize_klines.py`)

**Purpose**: Normalize kline CSV to canonical format.

---

### Build Snapshot (`build_snapshot.py`)

**Purpose**: Create exchange info snapshot.

---

### Build Store (`build_store.py`)

**Purpose**: Build organized kline data store.

---

### Collect Spreads (`collect_spreads.py`)

**Purpose**: Collect historical spread data.

---

### Ack Manual Review (`ack_manual_review.py`)

**Purpose**: Clear manual intervention flag.

```bash
ack-manual-review --reason "verified"
```

---

## Utils Module

**Location**: `src/utils/`

### Binance Utils (`binance.py`)

**Purpose**: Binance-specific helpers.

**Functions**:
- `parse_symbol_filters(filters)` - Parse exchangeInfo filters
- `round_to_tick_size(price, tick_size)` - Round to valid price
- `round_to_step_size(qty, step_size)` - Round to valid quantity

---

### Single Instance Lock (`single_instance.py`)

**Purpose**: Prevent multiple bot instances.

**Key Classes**:

```python
class SingleInstanceLock:
    """File-based single instance lock."""

    def acquire(self) -> None:
        """Acquire lock, raises if already held."""

    def release(self) -> None:
        """Release lock."""
```

---

## Module Dependencies

```
main.py
├── config/settings.py
├── strategy/
│   ├── signals.py      → features/
│   ├── scoring.py
│   ├── regime.py
│   ├── universe.py     → connectors/rest_client.py
│   ├── portfolio.py
│   └── package.py
├── risk/engine.py
├── execution/
│   ├── engine.py       → connectors/rest_client.py
│   ├── user_stream.py  → connectors/ws_client.py
│   └── state_store.py
├── ledger/
│   ├── events.py
│   ├── bus.py
│   ├── store.py
│   └── state.py
├── connectors/
│   ├── rest_client.py
│   ├── ws_client.py
│   ├── news.py
│   └── news_classifier.py → llm.py
├── monitoring/
│   ├── metrics.py
│   ├── logging.py
│   └── ...
└── api/operator.py     → ledger/, config/
```

---

## Related Documentation

- [System Overview](01_SystemOverview.md) - High-level architecture
- [Event System](../03_development/03_EventSystem.md) - Event sourcing details
- [Configuration Reference](../04_reference/01_Configuration.md) - All config options
