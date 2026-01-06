# Task 06 — Funding Cost Model (Live + Backtest)

## Goal
Make funding an explicit part of the strategy and PnL, not a hand-waved afterthought.

## Why
On perpetuals, funding can dominate returns, especially for multi-day holds and crowded trades.

## Deliverables
- Live:
  - emit `FundingUpdate` events per symbol on a schedule
  - include estimated funding carry in the decision record (`thinking.jsonl`)
- Backtest:
  - if funding history is available, subtract funding payments during holding periods
  - if not available, support a conservative constant funding assumption for stress testing

## Acceptance Criteria
- Backtest report shows PnL with and without funding.
- Live ledger can reconstruct “what funding was assumed when this trade was taken”.

