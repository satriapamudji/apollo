# Configuration Reference

Complete reference for all configuration options in the Apollo.

## Table of Contents

- [Overview](#overview)
- [Configuration Loading](#configuration-loading)
- [Environment Variables](#environment-variables)
- [Run Configuration](#run-configuration)
- [Binance Configuration](#binance-configuration)
- [Strategy Configuration](#strategy-configuration)
- [Regime Configuration](#regime-configuration)
- [Risk Configuration](#risk-configuration)
- [Universe Configuration](#universe-configuration)
- [News Configuration](#news-configuration)
- [LLM Configuration](#llm-configuration)
- [Execution Configuration](#execution-configuration)
- [Backtest Configuration](#backtest-configuration)
- [Paper Simulation Configuration](#paper-simulation-configuration)
- [Crowding Configuration](#crowding-configuration)
- [Storage Configuration](#storage-configuration)
- [Monitoring Configuration](#monitoring-configuration)
- [Reconciliation Configuration](#reconciliation-configuration)
- [Watchdog Configuration](#watchdog-configuration)

---

## Overview

Configuration is managed through a hierarchical system using Pydantic for validation:

- **config.yaml** - Main configuration file
- **.env** - Environment variables for secrets
- **Defaults** - Sensible defaults in code

### Configuration Priority

1. Environment variables (highest priority)
2. config.yaml values
3. Default values (lowest priority)

### Source File

`src/config/settings.py`

---

## Configuration Loading

```python
from src.config.settings import load_settings

# Load from default config.yaml
settings = load_settings()

# Load from custom path
settings = load_settings("custom_config.yaml")

# Environment variable override
# CONFIG_PATH=custom.yaml bot
```

---

## Environment Variables

API credentials and sensitive values should be stored in `.env`:

| Variable | Description |
|----------|-------------|
| `BINANCE_API_KEY` | Live Binance API key |
| `BINANCE_SECRET_KEY` | Live Binance API secret |
| `BINANCE_TESTNET_FUTURE_API_KEY` | Testnet API key |
| `BINANCE_TESTNET_FUTURE_SECRET_KEY` | Testnet API secret |
| `OPENAI_API_KEY` | OpenAI API key (for LLM news classification) |
| `ANTHROPIC_API_KEY` | Anthropic API key (alternative LLM) |
| `RUN_MODE` | Override run.mode |
| `RUN_ENABLE_TRADING` | Override run.enable_trading |
| `RUN_LIVE_CONFIRM` | Override run.live_confirm |
| `CONFIG_PATH` | Custom config file path |

---

## Run Configuration

Controls the trading mode and safety gates.

```yaml
run:
  mode: paper          # paper | testnet | live
  enable_trading: false
  live_confirm: ""     # Must be "YES_I_UNDERSTAND" for live trading
```

| Field | Type | Default | Range | Description |
|-------|------|---------|-------|-------------|
| `mode` | string | `paper` | `paper`, `testnet`, `live` | Trading mode |
| `enable_trading` | bool | `false` | - | Enable order execution |
| `live_confirm` | string | `""` | - | Safety confirmation for live mode |

### Mode Behavior

| Mode | Orders | User Stream | Reconciliation |
|------|--------|-------------|----------------|
| `paper` | Simulated | Optional | Disabled |
| `testnet` | Real (testnet) | Enabled | Enabled |
| `live` | Real (production) | Enabled | Enabled |

### Safety Requirements

- **testnet**: Requires `enable_trading: true` + testnet API keys
- **live**: Requires `enable_trading: true` + live API keys + `live_confirm: YES_I_UNDERSTAND`

---

## Binance Configuration

API endpoints and connection settings.

```yaml
binance:
  base_url: https://fapi.binance.com
  ws_url: wss://fstream.binance.com
  testnet_base_url: https://testnet.binancefuture.com
  testnet_ws_url: wss://stream.binancefuture.com
  recv_window: 5000
  user_stream_enabled: true
  user_stream_keepalive_sec: 1800
  use_production_market_data: false
  market_data_base_url: https://fapi.binance.com
  market_data_ws_url: wss://fstream.binance.com
  time_sync_interval_sec: 60
```

| Field | Type | Default | Range | Description |
|-------|------|---------|-------|-------------|
| `base_url` | string | `https://fapi.binance.com` | - | Production REST endpoint |
| `ws_url` | string | `wss://fstream.binance.com` | - | Production WebSocket endpoint |
| `testnet_base_url` | string | `https://testnet.binancefuture.com` | - | Testnet REST endpoint |
| `testnet_ws_url` | string | `wss://stream.binancefuture.com` | - | Testnet WebSocket endpoint |
| `recv_window` | int | `5000` | 1000-60000 | Request timestamp tolerance (ms) |
| `user_stream_enabled` | bool | `true` | - | Enable WebSocket user data stream |
| `user_stream_keepalive_sec` | int | `1800` | 60-3600 | Keepalive ping interval |
| `use_production_market_data` | bool | `false` | - | Use production for market data in testnet |
| `market_data_base_url` | string | `https://fapi.binance.com` | - | Market data REST endpoint |
| `market_data_ws_url` | string | `wss://fstream.binance.com` | - | Market data WebSocket endpoint |
| `time_sync_interval_sec` | int | `60` | 30-3600 | Server time sync interval |

---

## Strategy Configuration

Trading strategy parameters.

```yaml
strategy:
  name: trend_following_v1
  trend_timeframe: 1d
  entry_timeframe: 4h
  indicators:
    ema_fast: 8
    ema_slow: 21
    atr_period: 14
    rsi_period: 14
    adx_period: 14
    chop_period: 14
    volume_sma_period: 20
  entry:
    style: breakout
    score_threshold: 0.55
    max_breakout_extension_atr: 1.5
    volume_breakout_threshold: 1.5
    volume_confirmation_enabled: true
  exit:
    atr_stop_multiplier: 2.0
    trailing_start_atr: 1.5
    trailing_distance_atr: 1.5
    time_stop_days: 7
    time_stop_min_profit_atr: 0.5
  scoring:
    enabled: true
    threshold: 0.55
    factors:
      trend: 0.35
      volatility: 0.15
      entry_quality: 0.25
      funding: 0.10
      news: 0.15
      liquidity: 0.0
      volume: 0.0
```

### Top-Level Strategy

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | string | `trend_following_v1` | Strategy identifier |
| `trend_timeframe` | string | `1d` | Timeframe for trend detection |
| `entry_timeframe` | string | `4h` | Timeframe for entry signals |

### Indicators

| Field | Type | Default | Range | Description |
|-------|------|---------|-------|-------------|
| `ema_fast` | int | `8` | 2-50 | Fast EMA period |
| `ema_slow` | int | `21` | 5-100 | Slow EMA period |
| `atr_period` | int | `14` | 5-50 | ATR calculation period |
| `rsi_period` | int | `14` | 5-50 | RSI calculation period |
| `adx_period` | int | `14` | 5-50 | ADX calculation period |
| `chop_period` | int | `14` | 5-50 | Choppiness Index period |
| `volume_sma_period` | int | `20` | 5-50 | Volume SMA period |

### Entry

| Field | Type | Default | Range | Description |
|-------|------|---------|-------|-------------|
| `style` | string | `pullback` | `pullback`, `breakout` | Entry style |
| `score_threshold` | float | `0.65` | 0.3-0.95 | Minimum composite score |
| `max_breakout_extension_atr` | float | `1.5` | 0.5-5.0 | Max distance from breakout level |
| `volume_breakout_threshold` | float | `1.5` | 1.0-5.0 | Volume ratio for breakout confirmation |
| `volume_confirmation_enabled` | bool | `true` | - | Require volume confirmation |

### Exit

| Field | Type | Default | Range | Description |
|-------|------|---------|-------|-------------|
| `atr_stop_multiplier` | float | `2.0` | 1.0-5.0 | Initial stop distance in ATR units |
| `trailing_start_atr` | float | `1.5` | 0.5-3.0 | Profit in ATR to start trailing |
| `trailing_distance_atr` | float | `1.5` | 0.5-3.0 | Trailing stop distance in ATR |
| `time_stop_days` | int | `7` | 1-30 | Maximum holding period |
| `time_stop_min_profit_atr` | float | `0.5` | 0.0-2.0 | Min profit for time-based exit |

### Scoring

| Field | Type | Default | Range | Description |
|-------|------|---------|-------|-------------|
| `enabled` | bool | `true` | - | Enable multi-factor scoring |
| `threshold` | float | `0.55` | 0.3-0.95 | Minimum score for entry |
| `factors.trend` | float | `0.35` | 0.0-1.0 | Trend factor weight |
| `factors.volatility` | float | `0.15` | 0.0-1.0 | Volatility factor weight |
| `factors.entry_quality` | float | `0.25` | 0.0-1.0 | Entry quality weight |
| `factors.funding` | float | `0.10` | 0.0-1.0 | Funding rate weight |
| `factors.news` | float | `0.15` | 0.0-1.0 | News sentiment weight |
| `factors.liquidity` | float | `0.0` | 0.0-1.0 | Liquidity weight |
| `factors.volume` | float | `0.0` | 0.0-1.0 | Volume weight |

---

## Regime Configuration

Market regime detection settings.

```yaml
regime:
  enabled: true
  adx_trending_threshold: 25.0
  adx_ranging_threshold: 20.0
  chop_trending_threshold: 50.0
  chop_ranging_threshold: 61.8
  transitional_size_multiplier: 0.5
  volatility_regime_enabled: false
  volatility_contraction_threshold: 0.5
  volatility_expansion_threshold: 2.0
```

| Field | Type | Default | Range | Description |
|-------|------|---------|-------|-------------|
| `enabled` | bool | `true` | - | Enable regime detection |
| `adx_trending_threshold` | float | `25.0` | 10.0-50.0 | ADX above = trending |
| `adx_ranging_threshold` | float | `20.0` | 5.0-40.0 | ADX below = ranging |
| `chop_trending_threshold` | float | `50.0` | 30.0-60.0 | Chop below = trending |
| `chop_ranging_threshold` | float | `61.8` | 50.0-80.0 | Chop above = ranging |
| `transitional_size_multiplier` | float | `0.5` | 0.1-1.0 | Position size in transitional regime |
| `volatility_regime_enabled` | bool | `false` | - | Enable volatility regime |
| `volatility_contraction_threshold` | float | `0.5` | 0.1-1.0 | ATR ratio for contraction |
| `volatility_expansion_threshold` | float | `2.0` | 1.0-5.0 | ATR ratio for expansion |

### Regime Types

| Regime | ADX Condition | Chop Condition | Entry Allowed | Size |
|--------|---------------|----------------|---------------|------|
| TRENDING | >= 25 | <= 50 | Yes | 100% |
| CHOPPY | <= 20 | >= 61.8 | No | 0% |
| TRANSITIONAL | Between | Between | Yes | 50% |

---

## Risk Configuration

Risk management hard limits.

```yaml
risk:
  risk_per_trade_pct: 0.75
  max_leverage: 5
  default_leverage: 2
  max_daily_loss_pct: 3.0
  max_drawdown_pct: 10.0
  max_consecutive_losses: 3
  max_loss_streak_24h: 5
  loss_streak_window_hours: 24
  cooldown_after_loss_hours: 4
  cooldown_after_max_daily_hours: 24
  max_positions: 1
  max_funding_rate_pct: 0.1
```

| Field | Type | Default | Range | Description |
|-------|------|---------|-------|-------------|
| `risk_per_trade_pct` | float | `1.0` | 0.1-2.0 | Max equity risked per trade |
| `max_leverage` | int | `5` | 1-10 | Maximum leverage allowed |
| `default_leverage` | int | `3` | 1-5 | Default leverage for new positions |
| `max_daily_loss_pct` | float | `3.0` | 1.0-10.0 | Daily loss limit (triggers cooldown) |
| `max_drawdown_pct` | float | `10.0` | 5.0-25.0 | Max drawdown (triggers circuit breaker) |
| `max_consecutive_losses` | int | `3` | 2-10 | Consecutive loss limit |
| `max_loss_streak_24h` | int | `5` | 2-20 | Losses in 24h limit |
| `loss_streak_window_hours` | int | `24` | 1-72 | Window for loss streak counting |
| `cooldown_after_loss_hours` | int | `4` | 1-24 | Cooldown after loss |
| `cooldown_after_max_daily_hours` | int | `24` | 12-48 | Cooldown after daily limit |
| `max_positions` | int | `1` | 1-3 | Maximum concurrent positions |
| `max_funding_rate_pct` | float | `0.1` | 0.01-0.5 | Max funding rate to accept |

---

## Universe Configuration

Symbol universe selection criteria.

```yaml
universe:
  min_quote_volume_usd: 50000000
  max_min_notional_usd: 6.0
  max_funding_rate_pct: 0.1
  size: 5
  refresh_interval_hours: 24
  retry_interval_minutes: 5
```

| Field | Type | Default | Range | Description |
|-------|------|---------|-------|-------------|
| `min_quote_volume_usd` | float | `50000000` | >= 1000000 | Minimum 24h volume |
| `max_min_notional_usd` | float | `6.0` | 1.0-100.0 | Max minimum notional |
| `max_funding_rate_pct` | float | `0.1` | 0.01-0.5 | Max funding rate |
| `size` | int | `5` | 3-20 | Number of symbols in universe |
| `refresh_interval_hours` | int | `24` | 1-168 | Refresh frequency |
| `retry_interval_minutes` | int | `5` | 1-60 | Retry on failure |

---

## News Configuration

News ingestion and classification settings.

```yaml
news:
  enabled: true
  classifier_provider: rules
  poll_interval_minutes: 15
  max_age_hours: 24
  high_risk_block_hours: 24
  medium_risk_block_hours: 6
  source_timeout_sec: 10
  sources:
    - url: https://www.coindesk.com/arc/outboundfeeds/rss/
      name: coindesk
      enabled: true
```

| Field | Type | Default | Range | Description |
|-------|------|---------|-------|-------------|
| `enabled` | bool | `true` | - | Enable news ingestion |
| `classifier_provider` | string | `rules` | `rules`, `llm` | Classification method |
| `poll_interval_minutes` | int | `15` | 5-60 | Polling frequency |
| `max_age_hours` | int | `24` | 1-72 | Max news age to consider |
| `high_risk_block_hours` | int | `24` | 6-72 | Block duration for HIGH risk |
| `medium_risk_block_hours` | int | `6` | 1-24 | Block duration for MEDIUM risk |
| `source_timeout_sec` | int | `10` | 1-60 | Source fetch timeout |

### News Sources

Each source has:
- `url` - RSS feed URL
- `name` - Source identifier
- `enabled` - Enable/disable source

---

## LLM Configuration

LLM integration for news classification.

```yaml
llm:
  provider: openai
  model: gpt-4-turbo-preview
  max_tokens: 500
  temperature: 0.1
  rate_limit_per_minute: 10
  request_timeout_sec: 20
  retry_attempts: 2
  retry_backoff_sec: 1.0
```

| Field | Type | Default | Range | Description |
|-------|------|---------|-------|-------------|
| `provider` | string | `openai` | `openai`, `anthropic`, `local` | LLM provider |
| `model` | string | `gpt-4-turbo-preview` | - | Model identifier |
| `max_tokens` | int | `500` | 100-2000 | Max response tokens |
| `temperature` | float | `0.1` | 0.0-1.0 | Response randomness |
| `rate_limit_per_minute` | int | `10` | 1-60 | Request rate limit |
| `request_timeout_sec` | int | `20` | 5-120 | Request timeout |
| `retry_attempts` | int | `2` | 0-5 | Retry count |
| `retry_backoff_sec` | float | `1.0` | 0.1-10.0 | Retry backoff |

---

## Execution Configuration

Order execution settings.

```yaml
execution:
  max_slippage_pct: 0.5
  limit_order_buffer_pct: 0.05
  max_spread_pct: 0.3
  retry_attempts: 3
  retry_initial_delay_ms: 1000
  retry_max_delay_ms: 10000
  order_timeout_sec: 30
  margin_type: ISOLATED
  position_mode: ONE_WAY
  use_dynamic_spread_threshold: true
  spread_threshold_calm_pct: 0.05
  spread_threshold_normal_pct: 0.10
  spread_threshold_volatile_pct: 0.20
  atr_calm_threshold: 2.0
  atr_volatile_threshold: 4.0
  entry_timeout_mode: timeframe
  entry_fallback_timeout_sec: 60
  entry_max_duration_sec: 3600
  entry_expired_action: cancel
  entry_lifecycle_enabled: true
```

| Field | Type | Default | Range | Description |
|-------|------|---------|-------|-------------|
| `max_slippage_pct` | float | `0.5` | 0.1-2.0 | Max acceptable slippage |
| `limit_order_buffer_pct` | float | `0.05` | 0.01-0.2 | Buffer for limit orders |
| `max_spread_pct` | float | `0.3` | 0.05-1.0 | Max spread for entry |
| `retry_attempts` | int | `3` | 1-10 | Order retry attempts |
| `retry_initial_delay_ms` | int | `1000` | 100-5000 | Initial retry delay |
| `retry_max_delay_ms` | int | `10000` | 1000-60000 | Max retry delay |
| `order_timeout_sec` | int | `30` | 10-300 | Order placement timeout |
| `margin_type` | string | `ISOLATED` | `ISOLATED`, `CROSSED` | Margin mode |
| `position_mode` | string | `ONE_WAY` | `ONE_WAY`, `HEDGE` | Position mode |
| `use_dynamic_spread_threshold` | bool | `true` | - | ATR-based spread thresholds |
| `spread_threshold_calm_pct` | float | `0.05` | 0.01-0.5 | Spread limit in calm market |
| `spread_threshold_normal_pct` | float | `0.10` | 0.02-0.5 | Spread limit in normal market |
| `spread_threshold_volatile_pct` | float | `0.20` | 0.05-1.0 | Spread limit in volatile market |
| `atr_calm_threshold` | float | `2.0` | 0.5-5.0 | ATR% for calm classification |
| `atr_volatile_threshold` | float | `4.0` | 1.0-10.0 | ATR% for volatile classification |
| `entry_timeout_mode` | string | `timeframe` | `fixed`, `timeframe`, `unlimited` | Entry order timeout mode |
| `entry_fallback_timeout_sec` | int | `60` | 10-600 | Fallback timeout for timeframe mode |
| `entry_max_duration_sec` | int | `3600` | 60-14400 | Max duration for unlimited mode |
| `entry_expired_action` | string | `cancel` | `cancel`, `convert_market`, `convert_stop` | Action on order expiry |
| `entry_lifecycle_enabled` | bool | `true` | - | Track entry orders across cycles |

---

## Backtest Configuration

Backtesting execution model settings.

```yaml
backtest:
  execution_model: realistic
  slippage_base_bps: 2
  slippage_atr_scale: 1.0
  fill_probability_model: true
  random_seed: 42
  spread_model: conservative
  spread_base_pct: 0.01
  spread_atr_scale: 0.5
  max_spread_pct: 0.3
```

| Field | Type | Default | Range | Description |
|-------|------|---------|-------|-------------|
| `execution_model` | string | `ideal` | `ideal`, `realistic` | Execution simulation model |
| `slippage_base_bps` | float | `2.0` | 0.0-20.0 | Base slippage in basis points |
| `slippage_atr_scale` | float | `1.0` | 0.0-5.0 | ATR-based slippage scaling |
| `fill_probability_model` | bool | `true` | - | Enable stochastic fills |
| `random_seed` | int | `null` | >= 0 | Seed for reproducibility |
| `spread_model` | string | `conservative` | `none`, `conservative` | Spread simulation model |
| `spread_base_pct` | float | `0.01` | 0.0-0.5 | Base spread percentage |
| `spread_atr_scale` | float | `0.5` | 0.0-2.0 | ATR-based spread scaling |
| `max_spread_pct` | float | `0.3` | 0.05-1.0 | Max spread to allow entry |

### Execution Models

- **ideal**: Fixed slippage, all orders fill immediately
- **realistic**: Variable slippage, stochastic fill probability, funding settlements

---

## Paper Simulation Configuration

Realistic simulation for paper trading.

```yaml
paper_sim:
  enabled: true
  slippage_base_bps: 2.0
  slippage_atr_scale: 1.0
  maker_fee_pct: 0.02
  taker_fee_pct: 0.04
  random_seed: null
  partial_fill_rate: 0.20
  use_live_book_ticker: true
  book_ticker_cache_seconds: 1.0
```

| Field | Type | Default | Range | Description |
|-------|------|---------|-------|-------------|
| `enabled` | bool | `true` | - | Enable realistic simulation |
| `slippage_base_bps` | float | `2.0` | 0.0-20.0 | Base slippage |
| `slippage_atr_scale` | float | `1.0` | 0.0-5.0 | ATR scaling for slippage |
| `maker_fee_pct` | float | `0.02` | 0.0-0.5 | Maker fee percentage |
| `taker_fee_pct` | float | `0.04` | 0.0-0.5 | Taker fee percentage |
| `random_seed` | int | `null` | >= 0 | Seed for reproducibility |
| `partial_fill_rate` | float | `0.20` | 0.0-1.0 | Probability of partial fills |
| `use_live_book_ticker` | bool | `true` | - | Fetch real book ticker |
| `book_ticker_cache_seconds` | float | `1.0` | 0.1-60.0 | Cache duration |

---

## Crowding Configuration

Crowding and sentiment data settings.

```yaml
crowding:
  enabled: true
  long_short_extreme: 0.8
  oi_expansion_threshold: 0.1
  taker_imbalance_threshold: 0.6
  funding_volatility_window: 8
  oi_window: 3
  cache_dir: ./data/crowding
  cache_max_entries: 1000
```

| Field | Type | Default | Range | Description |
|-------|------|---------|-------|-------------|
| `enabled` | bool | `true` | - | Enable crowding detection |
| `long_short_extreme` | float | `0.8` | 0.5-0.99 | Extreme long/short ratio |
| `oi_expansion_threshold` | float | `0.1` | 0.0-0.5 | OI expansion threshold |
| `taker_imbalance_threshold` | float | `0.6` | 0.0-1.0 | Taker buy/sell imbalance |
| `funding_volatility_window` | int | `8` | 2-50 | Funding volatility lookback |
| `oi_window` | int | `3` | 2-10 | OI change window |
| `cache_dir` | string | `./data/crowding` | - | Cache directory |
| `cache_max_entries` | int | `1000` | 100-10000 | Max cache entries |

---

## Storage Configuration

File storage paths.

```yaml
storage:
  ledger_path: ./data/ledger
  state_path: ./data/state
  logs_path: ./logs
  data_path: ./data/market
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `ledger_path` | string | `./data/ledger` | Event ledger directory |
| `state_path` | string | `./data/state` | Persisted state directory |
| `logs_path` | string | `./logs` | Log files directory |
| `data_path` | string | `./data/market` | Market data directory |

---

## Monitoring Configuration

Observability and alerting settings.

```yaml
monitoring:
  metrics_port: 9090
  api_port: 8000
  log_level: INFO
  alert_webhooks: []
  log_http: false
  log_http_responses: false
  log_http_max_body_chars: 500
  error_log_max_bytes: 5000000
  error_log_backup_count: 3
```

| Field | Type | Default | Range | Description |
|-------|------|---------|-------|-------------|
| `metrics_port` | int | `9090` | 1024-65535 | Prometheus metrics port |
| `api_port` | int | `8000` | 1024-65535 | Operator API port |
| `log_level` | string | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` | Logging level |
| `alert_webhooks` | list | `[]` | - | Alert webhook URLs |
| `log_http` | bool | `false` | - | Log HTTP requests |
| `log_http_responses` | bool | `false` | - | Log HTTP responses |
| `log_http_max_body_chars` | int | `500` | 0-5000 | Max response body to log |
| `error_log_max_bytes` | int | `5000000` | 100000-50000000 | Error log max size |
| `error_log_backup_count` | int | `3` | 1-20 | Error log rotation count |

---

## Reconciliation Configuration

State verification settings.

```yaml
reconciliation:
  enabled: true
  interval_minutes: 30
  failure_threshold: 3
  allow_in_paper: false
```

| Field | Type | Default | Range | Description |
|-------|------|---------|-------|-------------|
| `enabled` | bool | `true` | - | Enable reconciliation |
| `interval_minutes` | int | `30` | 5-1440 | Reconciliation frequency |
| `failure_threshold` | int | `3` | 1-10 | Failures before alert |
| `allow_in_paper` | bool | `false` | - | Run in paper mode |

---

## Watchdog Configuration

Protective order verification settings.

```yaml
watchdog:
  enabled: true
  interval_sec: 300
  auto_recover: true
```

| Field | Type | Default | Range | Description |
|-------|------|---------|-------|-------------|
| `enabled` | bool | `true` | - | Enable watchdog |
| `interval_sec` | int | `300` | 30-3600 | Check frequency |
| `auto_recover` | bool | `true` | - | Auto-create missing orders |

---

## Example Configurations

### Conservative Live Trading

```yaml
run:
  mode: live
  enable_trading: true
  live_confirm: YES_I_UNDERSTAND

risk:
  risk_per_trade_pct: 0.5
  max_leverage: 3
  default_leverage: 2
  max_drawdown_pct: 5.0
  max_positions: 1

strategy:
  entry:
    score_threshold: 0.70

reconciliation:
  interval_minutes: 15
```

### Aggressive Backtesting

```yaml
backtest:
  execution_model: realistic
  random_seed: 42

risk:
  risk_per_trade_pct: 1.0
  max_positions: 3

strategy:
  entry:
    score_threshold: 0.50
```

### Paper Trading with Real Data

```yaml
run:
  mode: paper
  enable_trading: false

binance:
  use_production_market_data: true

paper_sim:
  enabled: true
  use_live_book_ticker: true
```

---

## Related Documentation

- [System Overview](../00_architecture/01_SystemOverview.md) - Architecture details
- [RUNBOOK](../../RUNBOOK.md) - Operations reference
- [Deployment Guide](../02_operations/01_Deployment.md) - Environment setup
