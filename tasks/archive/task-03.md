# Task 03 — Universe Selection Must Respect Account Size (Tradeability Filter)

## Goal
Stop selecting symbols the account cannot trade under the configured risk model and Binance filters.

## Why
With small equity, high-priced assets (e.g., BTC) often fail `minQty/stepSize` constraints at sane risk %. This leads to “no trades” or constant risk rejections, and hides real strategy behavior.

## Deliverables
- Extend `src/strategy/universe.py` to compute and filter by a conservative `min_equity_required` per symbol using:
  - symbol filters (`minQty`, `stepSize`, `minNotional`, `tickSize`)
  - current price (mark price)
  - configured `risk_per_trade_pct` and a representative stop distance (e.g., `2*ATR` proxy or a fixed % cap)
- Emit a ledger event explaining removals (e.g., `UniverseUpdated` metadata or a new `UniverseFiltered` event).

## Acceptance Criteria
- On an account with low equity, the bot avoids repeatedly proposing trades that will always be rejected due to sizing.
- Logs/ledger clearly show why a symbol was excluded (minQty/stepSize/minNotional vs equity).

