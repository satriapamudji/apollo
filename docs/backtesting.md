# Backtesting

## Overview

The repository includes a simple backtester that reuses the live signal generator and risk engine on historical OHLCV.

It is currently best used for smoke tests and quick iteration, not for “this will be profitable live” conclusions.

## Run

```bash
backtest --symbol ETHUSDT --interval 4h --data-path ./data/market
```

Equivalent:

```bash
python -m src.backtester.runner --symbol ETHUSDT --interval 4h --data-path ./data/market
```

## Input Data

The backtester expects a CSV at:

`{data-path}/{symbol}_{interval}.csv`

Supported timestamp columns:
- `open_time` (Binance klines style, in ms or s)
- `timestamp`

Required columns:
- `open`, `high`, `low`, `close`, `volume`

## Known Limitations (Tracked Tasks)

- Lookahead bias in daily resampling: `tasks/task-00.md`
- Minimal reporting: `tasks/task-01.md`
- No built-in historical data downloader yet: `tasks/task-02.md`
- Funding costs not modeled: `tasks/task-06.md`
- Paper/backtest execution realism (fills/spread/slippage) incomplete: `tasks/task-19.md`

