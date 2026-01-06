# CLI Tools Reference

Reference for all command-line tools in the Binance Trend Bot.

## Console Scripts

Installed via `pip install -e .`:

| Command | Module | Description |
|---------|--------|-------------|
| `bot` | `src.main:main` | Run trading bot |
| `backtest` | `src.backtester.runner:main` | Run backtest |
| `ack-manual-review` | `src.tools.ack_manual_review:main` | Clear manual review flag |

---

## bot

Run the trading bot.

### Usage

```bash
bot
```

### Configuration

Uses `config.yaml` by default. Override with environment:

```bash
CONFIG_PATH=custom.yaml bot
```

### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Normal shutdown |
| 1 | Error during startup |

---

## backtest

Run historical backtests.

### Usage

```bash
backtest [OPTIONS]
```

### Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--symbol` | string | - | Single symbol to backtest |
| `--symbols` | string | - | Comma-separated symbols |
| `--interval` | string | `4h` | Bar interval |
| `--data-path` | string | `./data/market` | Market data directory |
| `--config` | string | `config.yaml` | Config file path |
| `--initial-equity` | float | `1000.0` | Starting equity |
| `--execution-model` | string | `realistic` | `ideal` or `realistic` |
| `--random-seed` | int | `None` | Seed for reproducibility |
| `--out-dir` | string | `None` | Output directory for results |
| `--start` | string | `None` | Start date (YYYY-MM-DD) |
| `--end` | string | `None` | End date (YYYY-MM-DD) |

### Examples

```bash
# Single symbol
backtest --symbol BTCUSDT --interval 4h

# Multiple symbols
backtest --symbols BTCUSDT,ETHUSDT,SOLUSDT

# With output
backtest --symbol BTCUSDT --out-dir ./results/btc_test

# Ideal execution (no slippage)
backtest --symbol BTCUSDT --execution-model ideal

# Reproducible run
backtest --symbol BTCUSDT --random-seed 42

# Date range
backtest --symbol BTCUSDT --start 2024-01-01 --end 2024-06-30
```

### Output Files

When `--out-dir` is specified:

| File | Description |
|------|-------------|
| `summary.json` | Performance metrics |
| `trades.csv` | Trade records |
| `equity.csv` | Equity curve |

---

## ack-manual-review

Clear manual review flag to resume trading.

### Usage

```bash
ack-manual-review --reason "description"
```

### Options

| Option | Type | Required | Description |
|--------|------|----------|-------------|
| `--reason` | string | Yes | Acknowledgment reason |

### Examples

```bash
# After checking positions
ack-manual-review --reason "Verified positions match exchange"

# After circuit breaker
ack-manual-review --reason "Reviewed drawdown, market stable"
```

### Alternative

```bash
python -m src.tools.ack_manual_review --reason "checked"
```

---

## download_klines

Download historical OHLCV data from Binance.

### Usage

```bash
python -m src.tools.download_klines [OPTIONS]
```

### Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--symbol` | string | Required | Trading symbol |
| `--interval` | string | `4h` | Kline interval |
| `--start` | string | Required | Start date (YYYY-MM-DD) |
| `--end` | string | Today | End date (YYYY-MM-DD) |
| `--output` | string | `./data/market` | Output directory |

### Examples

```bash
# Download BTC 4h klines
python -m src.tools.download_klines --symbol BTCUSDT --interval 4h --start 2024-01-01

# Download to custom directory
python -m src.tools.download_klines --symbol ETHUSDT --start 2024-01-01 --output ./data/custom

# Download daily klines
python -m src.tools.download_klines --symbol BTCUSDT --interval 1d --start 2023-01-01
```

### Output

Creates `<output>/<SYMBOL>_<interval>.csv` with columns:
- `open_time`, `close_time`, `open`, `high`, `low`, `close`, `volume`

---

## download_exchange_info

Download exchange symbol rules.

### Usage

```bash
python -m src.tools.download_exchange_info [OPTIONS]
```

### Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--output` | string | `./data` | Output directory |
| `--testnet` | flag | False | Use testnet endpoint |

### Examples

```bash
# Download production exchange info
python -m src.tools.download_exchange_info

# Download testnet info
python -m src.tools.download_exchange_info --testnet
```

### Output

Creates `exchange_info.json` with symbol rules (tick size, step size, etc.).

---

## build_universe

Build tradeable symbol universe snapshot.

### Usage

```bash
python -m src.tools.build_universe [OPTIONS]
```

### Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--min-volume` | float | `50000000` | Min 24h quote volume (USD) |
| `--size` | int | `5` | Universe size |
| `--output` | string | `./data` | Output directory |

### Examples

```bash
# Build default universe
python -m src.tools.build_universe

# Custom volume threshold
python -m src.tools.build_universe --min-volume 100000000 --size 10
```

### Output

Creates `universe_snapshot.json` with selected symbols and metrics.

---

## normalize_klines

Normalize kline CSV to canonical format.

### Usage

```bash
python -m src.tools.normalize_klines [OPTIONS]
```

### Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--input` | string | Required | Input CSV file |
| `--output` | string | Required | Output CSV file |
| `--interval` | string | Required | Kline interval |

### Examples

```bash
python -m src.tools.normalize_klines --input raw.csv --output normalized.csv --interval 4h
```

---

## validate_dataset

Validate backtest dataset integrity.

### Usage

```bash
python -m src.tools.validate_dataset [OPTIONS]
```

### Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--data-path` | string | `./data/market` | Data directory |
| `--symbols` | string | All | Comma-separated symbols |
| `--interval` | string | `4h` | Kline interval |

### Checks

- File existence
- Column schema
- Timestamp continuity
- Data gaps
- Price/volume validity

### Examples

```bash
# Validate all data
python -m src.tools.validate_dataset

# Validate specific symbols
python -m src.tools.validate_dataset --symbols BTCUSDT,ETHUSDT
```

---

## build_snapshot

Create exchange info snapshot for backtesting.

### Usage

```bash
python -m src.tools.build_snapshot [OPTIONS]
```

### Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--output` | string | `./data` | Output directory |
| `--symbols` | string | All | Filter symbols |

---

## build_store

Build organized kline data store.

### Usage

```bash
python -m src.tools.build_store [OPTIONS]
```

### Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--input` | string | Required | Input directory |
| `--output` | string | Required | Output directory |
| `--interval` | string | `4h` | Kline interval |

---

## collect_spreads

Collect bid-ask spread data over time.

### Usage

```bash
python -m src.tools.collect_spreads [OPTIONS]
```

### Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--symbols` | string | Required | Comma-separated symbols |
| `--duration` | int | `3600` | Collection duration (seconds) |
| `--interval` | int | `60` | Sample interval (seconds) |
| `--output` | string | `./data` | Output directory |

### Examples

```bash
# Collect 1 hour of spread data
python -m src.tools.collect_spreads --symbols BTCUSDT,ETHUSDT --duration 3600

# Higher frequency sampling
python -m src.tools.collect_spreads --symbols BTCUSDT --interval 10 --duration 600
```

---

## Common Patterns

### Download Data for Backtest

```bash
# Download klines for multiple symbols
for SYM in BTCUSDT ETHUSDT SOLUSDT; do
    python -m src.tools.download_klines --symbol $SYM --interval 4h --start 2024-01-01
done

# Download exchange info
python -m src.tools.download_exchange_info

# Validate dataset
python -m src.tools.validate_dataset

# Run backtest
backtest --symbols BTCUSDT,ETHUSDT,SOLUSDT --out-dir ./results
```

### Prepare Live Environment

```bash
# Download current exchange info
python -m src.tools.download_exchange_info

# Build universe
python -m src.tools.build_universe --min-volume 50000000 --size 5

# Start bot
bot
```

---

## Related Documentation

- [Running Backtests](../05_backtester/02_RunningBacktests.md) - Backtest workflow
- [Configuration Reference](01_Configuration.md) - Config options
- [RUNBOOK](../../RUNBOOK.md) - Operations procedures
