# Task 20 — Live Performance Telemetry (Expectancy, Fill Rate, Costs)

## Goal
Expose live strategy health metrics that answer: “Are we actually making money, and why?”

## Why
Profitability in production is usually killed by costs and execution (fees, funding, slippage, missed fills), not by the indicator math.

## Deliverables
- Add derived metrics (Prometheus + daily CSV/JSON snapshot):
  - fill rate (entries placed vs filled)
  - average slippage bps (entry/exit)
  - fees estimate, funding paid/received
  - expectancy per trade and rolling profit factor
  - time-in-market, average holding time
- Add a daily “operator summary” log line + artifact in `logs/`.

## Acceptance Criteria
- Operator can tell within one day whether performance issues are signal-quality or execution/cost-related.
- Metrics align with ledger/trades.csv (no hidden state).

