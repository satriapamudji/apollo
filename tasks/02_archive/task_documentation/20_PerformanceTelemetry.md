# Task 20: Live Performance Telemetry

## Summary

Added live performance telemetry to answer: "Are we actually making money, and why?" Tracks fill rate, slippage, fees, funding, expectancy, profit factor, and time-in-market metrics via Prometheus gauges and daily JSON/CSV snapshots.

## What Existed Before

- Basic trade logging via `trades.csv` (entry/exit prices, PnL)
- Order logging via `orders.csv`
- Prometheus metrics for positions and account state
- No aggregated performance metrics (win rate, expectancy, fill rate)
- No cost tracking (fees, funding)
- No way to distinguish signal-quality issues from execution/cost issues

## What Was Changed

### New Prometheus Metrics (`src/monitoring/metrics.py`)

**Fill Rate:**
- `orders_placed_total` - Counter of entry orders placed
- `orders_filled_total` - Counter of entry orders filled
- `fill_rate_pct` - Gauge showing orders_filled / orders_placed * 100

**Slippage:**
- `avg_entry_slippage_bps` - Average entry slippage in basis points
- `avg_exit_slippage_bps` - Average exit slippage in basis points

**Costs:**
- `fees_paid_total` - Total trading fees paid (USDT)
- `funding_received_total` - Total funding fees received
- `funding_paid_total` - Total funding fees paid
- `net_funding` - Net funding (received - paid)

**Performance:**
- `expectancy_per_trade` - Average PnL per closed trade
- `profit_factor_session` - Gross profit / gross loss (session)
- `profit_factor_7d` - Rolling 7-day profit factor
- `profit_factor_30d` - Rolling 30-day profit factor
- `win_rate_pct` - Winning trades / total trades * 100

**Time:**
- `time_in_market_pct` - Percentage of time with open positions
- `avg_holding_time_hours` - Average trade duration
- `trades_closed_total` - Counter of closed trades

### Binance API Endpoint (`src/connectors/rest_client.py`)
- `get_income_history()` - Fetches fee and funding data from `/fapi/v1/income`
- Supports filtering by `income_type` (COMMISSION, FUNDING_FEE), symbol, time range

### Performance Telemetry Module (`src/monitoring/performance_telemetry.py`)

**Data Classes:**
- `CostSummary` - Aggregates fees_paid, funding_received, funding_paid with computed net_funding and total_costs
- `TradeSummary` - Aggregates trade metrics with computed win_rate, expectancy, profit_factor, avg_holding_hours, slippage
- `ExecutionSummary` - Tracks orders_placed, orders_filled with computed fill_rate_pct
- `DailySummary` - Complete daily snapshot with `to_dict()` for JSON and `to_log_line()` for logging

**PerformanceTelemetry Class:**
- Event handlers for ORDER_PLACED, ORDER_FILLED, POSITION_OPENED, POSITION_CLOSED
- `update_metrics()` - Periodic update of all Prometheus metrics (every 5 minutes)
- `fetch_costs_from_binance()` - Pulls actual fee/funding data from exchange
- `compute_from_trades_csv()` - Computes metrics from trades.csv with optional time window
- `generate_daily_summary()` - Creates complete daily performance summary
- `write_daily_snapshot()` - Writes JSON and CSV to `logs/daily_summary_YYYY-MM-DD.*`
- `run_daily_summary()` - Combined generate + write + log

### Main Loop Integration (`src/main.py`)
- `telemetry_loop()` added to asyncio.gather
- Updates metrics every 5 minutes
- Generates daily summary at UTC 00:00

### Module Export (`src/monitoring/__init__.py`)
- Added `PerformanceTelemetry` to exports

### Tests (`tests/test_performance_telemetry.py`)
- 40 comprehensive tests covering:
  - CostSummary, TradeSummary, ExecutionSummary computations
  - DailySummary serialization (to_dict, to_log_line)
  - Event handlers (order placed/filled, position opened/closed)
  - Slippage tracking
  - Time-in-market calculation
  - CSV parsing with time windows
  - Daily snapshot file generation
  - Binance API cost fetching

## Key Design Decisions

1. **Metrics Update Frequency** - Every 5 minutes via `telemetry_loop()`, balancing freshness vs overhead
2. **Daily Snapshot Timing** - UTC 00:00, generating both JSON (machine-readable) and CSV (spreadsheet-compatible)
3. **Rolling Windows** - Profit factor computed for session, 7-day, and 30-day windows to show trend
4. **Reduce-Only Filtering** - Only entry orders (non-reduce-only) count toward fill rate
5. **Simulate Mode Awareness** - Skips Binance API calls when in paper/simulate mode
6. **Session-Based Tracking** - Some metrics (fill rate, slippage samples) reset on restart; trades.csv is source of truth for historical data

## Reasoning

- Operators need to quickly diagnose whether poor performance is due to:
  - **Signal quality** (low win rate, poor expectancy)
  - **Execution issues** (low fill rate, high slippage)
  - **Cost drag** (high fees, unfavorable funding)
- Daily snapshots provide audit trail and enable external analysis
- Prometheus metrics enable real-time dashboards and alerting
- Rolling windows (7d, 30d) help distinguish noise from trend

## Files Modified

- `src/monitoring/metrics.py` - Added 17 new Prometheus metrics
- `src/connectors/rest_client.py` - Added `get_income_history()` endpoint
- `src/monitoring/performance_telemetry.py` - New (~630 lines)
- `src/monitoring/__init__.py` - Added export
- `src/main.py` - Added telemetry wiring and loop
- `tests/test_performance_telemetry.py` - New (40 tests)

## Output Artifacts

- `logs/daily_summary_YYYY-MM-DD.json` - Full daily summary as JSON
- `logs/daily_summary_YYYY-MM-DD.csv` - Same data in CSV format
- Prometheus metrics at `http://localhost:<port>/` - Real-time gauges

## Example Daily Summary Log Line

```
date=2026-01-01 trades=5 win_rate=60.0% expectancy=6.20 profit_factor=1.85 fill_rate=83.3% avg_slippage_bps=3.5 fees=12.50 net_funding=-2.30 time_in_market=45.2%
```
