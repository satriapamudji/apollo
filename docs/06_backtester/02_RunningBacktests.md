# Running Backtests

Practical guide to running backtests with the Binance Trend Bot.

## Quick Start

### Single Symbol Backtest

```bash
backtest --symbol BTCUSDT --interval 4h --data-path ./data/market
```

### With Output

```bash
backtest --symbol BTCUSDT --out-dir ./results/btc_test
```

---

## Complete Workflow

### Step 1: Download Historical Data

```bash
# Download klines
python -m src.tools.download_klines \
    --symbol BTCUSDT \
    --interval 4h \
    --start 2024-01-01

# Download multiple symbols
for SYM in BTCUSDT ETHUSDT SOLUSDT BNBUSDT XRPUSDT; do
    python -m src.tools.download_klines --symbol $SYM --interval 4h --start 2024-01-01
done
```

### Step 2: Download Exchange Info

```bash
python -m src.tools.download_exchange_info
```

### Step 3: Validate Dataset

```bash
python -m src.tools.validate_dataset --data-path ./data/market
```

### Step 4: Run Backtest

```bash
backtest \
    --symbol BTCUSDT \
    --interval 4h \
    --data-path ./data/market \
    --initial-equity 1000 \
    --execution-model realistic \
    --random-seed 42 \
    --out-dir ./results/btc_realistic
```

### Step 5: Analyze Results

```bash
# View summary
cat ./results/btc_realistic/summary.json | jq

# View trades
cat ./results/btc_realistic/trades.csv

# View equity curve
cat ./results/btc_realistic/equity.csv
```

---

## Command Reference

### Basic Options

```bash
backtest [OPTIONS]
```

| Option | Description | Default |
|--------|-------------|---------|
| `--symbol` | Single symbol | - |
| `--symbols` | Comma-separated symbols | - |
| `--interval` | Bar interval | `4h` |
| `--data-path` | Data directory | `./data/market` |
| `--config` | Config file | `config.yaml` |

### Execution Options

| Option | Description | Default |
|--------|-------------|---------|
| `--initial-equity` | Starting equity | `1000.0` |
| `--execution-model` | `ideal` or `realistic` | `realistic` |
| `--random-seed` | Reproducibility seed | `None` |

### Output Options

| Option | Description | Default |
|--------|-------------|---------|
| `--out-dir` | Output directory | `None` |
| `--start` | Start date (YYYY-MM-DD) | Data start |
| `--end` | End date (YYYY-MM-DD) | Data end |

---

## Execution Models

### Ideal Model

Fast iteration, upper-bound performance:

```bash
backtest --symbol BTCUSDT --execution-model ideal
```

**Characteristics**:
- Fixed slippage (2 bps default)
- All orders fill immediately
- No fill probability modeling
- No funding settlements

### Realistic Model

Production-like simulation:

```bash
backtest --symbol BTCUSDT --execution-model realistic --random-seed 42
```

**Characteristics**:
- Variable slippage (ATR-scaled)
- Stochastic fill probability
- Funding rate settlements
- Spread-based entry gating

---

## Configuration

### Backtest-Specific Config

```yaml
# config.yaml
backtest:
  execution_model: realistic
  slippage_base_bps: 2.0
  slippage_atr_scale: 1.0
  fill_probability_model: true
  random_seed: 42
  spread_model: conservative
  spread_base_pct: 0.01
  spread_atr_scale: 0.5
  max_spread_pct: 0.3
```

### Strategy Config

Same as live trading:

```yaml
strategy:
  name: trend_following_v1
  entry:
    style: breakout
    score_threshold: 0.55
  exit:
    atr_stop_multiplier: 2.0
    trailing_start_atr: 1.5

risk:
  risk_per_trade_pct: 0.75
  max_leverage: 5
  max_positions: 1
```

---

## Multi-Symbol Backtest

### Sequential Symbols

```bash
backtest --symbols BTCUSDT,ETHUSDT,SOLUSDT --out-dir ./results/portfolio
```

### Batch Processing

```bash
#!/bin/bash
# batch_backtest.sh

SYMBOLS="BTCUSDT ETHUSDT SOLUSDT BNBUSDT XRPUSDT"
SEED=42

for SYM in $SYMBOLS; do
    echo "Testing $SYM..."
    backtest \
        --symbol $SYM \
        --execution-model realistic \
        --random-seed $SEED \
        --out-dir ./results/$SYM
done

# Aggregate results
echo "Symbol,Return,Sharpe,MaxDD,WinRate" > ./results/summary.csv
for SYM in $SYMBOLS; do
    jq -r "[\"$SYM\", .total_return_pct, .sharpe_ratio, .max_drawdown_pct, .win_rate] | @csv" \
        ./results/$SYM/summary.json >> ./results/summary.csv
done
```

---

## Output Files

### summary.json

```json
{
  "symbol": "BTCUSDT",
  "interval": "4h",
  "start_date": "2024-01-01",
  "end_date": "2024-06-30",
  "initial_equity": 1000.0,
  "final_equity": 1250.0,
  "total_return_pct": 25.0,
  "sharpe_ratio": 1.5,
  "max_drawdown_pct": 8.5,
  "win_rate": 0.55,
  "profit_factor": 1.8,
  "total_trades": 45,
  "winning_trades": 25,
  "losing_trades": 20,
  "avg_win_pct": 3.2,
  "avg_loss_pct": -1.5,
  "avg_holding_hours": 18.5,
  "execution_model": "realistic",
  "random_seed": 42
}
```

### trades.csv

| Column | Description |
|--------|-------------|
| `trade_id` | Unique identifier |
| `symbol` | Trading symbol |
| `side` | LONG or SHORT |
| `entry_time` | Entry timestamp |
| `entry_price` | Entry price |
| `quantity` | Position size |
| `leverage` | Position leverage |
| `exit_time` | Exit timestamp |
| `exit_price` | Exit price |
| `pnl` | Realized P&L |
| `pnl_pct` | P&L percentage |
| `reason` | Exit reason |
| `holding_hours` | Duration |

### equity.csv

| Column | Description |
|--------|-------------|
| `timestamp` | Date |
| `equity` | Account equity |
| `drawdown_pct` | Current drawdown |

---

## Interpreting Results

### Key Metrics

| Metric | Good | Warning | Poor |
|--------|------|---------|------|
| Sharpe Ratio | > 1.5 | 1.0-1.5 | < 1.0 |
| Max Drawdown | < 10% | 10-20% | > 20% |
| Win Rate | > 50% | 40-50% | < 40% |
| Profit Factor | > 1.5 | 1.0-1.5 | < 1.0 |

### Warning Signs

- **High Sharpe + Few Trades**: Not statistically significant
- **High Win Rate + Low Profit Factor**: Small wins, big losses
- **Sharp Drawdowns**: Strategy vulnerable to specific conditions
- **Ideal >> Realistic**: Sensitive to execution quality

### Validation Steps

1. **Statistical significance**: Minimum 30+ trades
2. **Regime diversity**: Test across different market conditions
3. **Parameter sensitivity**: Small changes shouldn't break strategy
4. **Out-of-sample**: Reserve data for final validation

---

## Parameter Optimization

### Manual Grid Search

```bash
#!/bin/bash
# optimize.sh

for THRESHOLD in 0.45 0.50 0.55 0.60 0.65; do
    # Create temp config
    cat config.yaml | sed "s/score_threshold: .*/score_threshold: $THRESHOLD/" > /tmp/config_$THRESHOLD.yaml

    backtest \
        --symbol BTCUSDT \
        --config /tmp/config_$THRESHOLD.yaml \
        --random-seed 42 \
        --out-dir ./optimize/$THRESHOLD

    SHARPE=$(jq '.sharpe_ratio' ./optimize/$THRESHOLD/summary.json)
    echo "Threshold $THRESHOLD: Sharpe = $SHARPE"
done
```

### Walk-Forward Analysis

```bash
#!/bin/bash
# walk_forward.sh

# Train: 2024-01-01 to 2024-04-30
# Test: 2024-05-01 to 2024-06-30

backtest --symbol BTCUSDT --start 2024-01-01 --end 2024-04-30 \
    --out-dir ./wf/train

backtest --symbol BTCUSDT --start 2024-05-01 --end 2024-06-30 \
    --out-dir ./wf/test

echo "Train Sharpe: $(jq '.sharpe_ratio' ./wf/train/summary.json)"
echo "Test Sharpe: $(jq '.sharpe_ratio' ./wf/test/summary.json)"
```

---

## Troubleshooting

### No Trades

**Possible Causes**:
- Score threshold too high
- Regime filter blocking entries
- Data doesn't have trending periods

**Debug**:
```bash
# Run with DEBUG logging
backtest --symbol BTCUSDT 2>&1 | grep -E "(signal|regime|score)"
```

### Poor Performance

**Check**:
1. Compare ideal vs realistic execution
2. Review slippage assumptions
3. Check if funding costs are significant
4. Verify spread assumptions aren't too conservative

### Inconsistent Results

**Solution**: Use `--random-seed` for reproducibility:
```bash
backtest --symbol BTCUSDT --random-seed 42
```

### Memory Issues

For large datasets:
```bash
# Process date ranges sequentially
backtest --symbol BTCUSDT --start 2024-01-01 --end 2024-03-31
backtest --symbol BTCUSDT --start 2024-04-01 --end 2024-06-30
```

---

## Best Practices

1. **Start with ideal model**: Understand strategy potential
2. **Use realistic for decisions**: Production estimates
3. **Always set random seed**: Reproducibility
4. **Test multiple symbols**: Generalization
5. **Reserve out-of-sample data**: Final validation
6. **Document assumptions**: Slippage, fees, funding
7. **Compare to benchmarks**: Buy-and-hold, other strategies
8. **Check regime performance**: How does it handle different markets?

---

## Example Scripts

### Full Backtest Pipeline

```bash
#!/bin/bash
# full_backtest.sh

set -e

SYMBOL=${1:-BTCUSDT}
START=${2:-2024-01-01}
SEED=42

echo "=== Downloading Data ==="
python -m src.tools.download_klines --symbol $SYMBOL --interval 4h --start $START

echo "=== Validating Data ==="
python -m src.tools.validate_dataset --symbols $SYMBOL

echo "=== Running Ideal Backtest ==="
backtest --symbol $SYMBOL --execution-model ideal --random-seed $SEED \
    --out-dir ./results/${SYMBOL}_ideal

echo "=== Running Realistic Backtest ==="
backtest --symbol $SYMBOL --execution-model realistic --random-seed $SEED \
    --out-dir ./results/${SYMBOL}_realistic

echo "=== Results ==="
echo "Ideal:"
jq '{return: .total_return_pct, sharpe: .sharpe_ratio, maxdd: .max_drawdown_pct}' \
    ./results/${SYMBOL}_ideal/summary.json

echo "Realistic:"
jq '{return: .total_return_pct, sharpe: .sharpe_ratio, maxdd: .max_drawdown_pct}' \
    ./results/${SYMBOL}_realistic/summary.json
```

---

## Related Documentation

- [Backtester Overview](01_Overview.md) - Architecture
- [CLI Tools](../04_reference/04_CLITools.md) - Command reference
- [Configuration Reference](../04_reference/01_Configuration.md) - All options
- [Data Schemas](../04_reference/03_DataSchemas.md) - Data formats
