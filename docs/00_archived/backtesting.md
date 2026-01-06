# Backtesting

## Overview

The backtester simulates the current strategy on historical OHLCV while keeping the same signal/risk logic as live.

It supports a quick console summary, and an optional report output directory with trade/equity artifacts.

## CLI Usage

```bash
backtest --symbol ETHUSDT [options]
```

Equivalent:

```bash
python -m src.backtester.runner --symbol ETHUSDT [options]
```

### Options

| Argument | Default | Description |
|----------|---------|-------------|
| `--symbol` | (required) | Trading symbol, e.g., `BTCUSDT` |
| `--interval` | `4h` | Interval used in CSV filename |
| `--data-path` | `./data/market` | Path to CSV files |
| `--config` | None | Path to `config.yaml` |
| `--initial-equity` | `100.0` | Starting capital |
| `--fee-pct` | `0.0006` | Trading fee (0.06%) |
| `--slippage-pct` | `0.0005` | Slippage (0.05%) |
| `--out-dir` | None | Output directory for report artifacts |

### Report Output

When `--out-dir` is set, the runner writes:
- `trades.csv` (trade list with entry/exit, PnL, holding time)
- `equity.csv` (equity curve + drawdown)
- `summary.json` (metrics summary)

### Metrics Computed

At minimum:
- total return, win rate, max drawdown
- expectancy, profit factor, average R-multiple
- max consecutive losses
- monthly returns table

## Input Data

Input file path: `{data-path}/{symbol}_{interval}.csv`

Required columns:
- `open`, `high`, `low`, `close`, `volume`

Timestamp column:
- `timestamp` (preferred), or `open_time` (Binance klines style, ms/s)

### Timestamp Convention (Important)

The backtester assumes the timestamp index represents the decision time for that candle.

For strict parity with the live bot (which operates on **closed** candles indexed by close time), the safest approach is to provide `timestamp` as the candle close time.

If your CSV uses Binance `open_time`, it is still usable, but it effectively shifts decision timestamps and can change how daily bars are constructed.

## Lookahead Bias Prevention (Daily Trend)

Daily candles are derived point-in-time from the 4h series using `src/backtester/engine.py:Backtester._get_daily_at_time`, so daily features at time `T` do not depend on 4h bars after `T`.

## Historical Data Download (Free Binance Data)

Download futures klines to `./data/market`:

```bash
python -m src.tools.download_klines --symbol BTCUSDT --interval 4h --start 2024-01-01
```

Add `--funding` to also download funding history to `./data/funding/{symbol}.csv`.
