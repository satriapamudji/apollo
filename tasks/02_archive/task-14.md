# Task 14 — Portfolio Selection: Pick the Best Trade (Not First in List)

## Goal
When `risk.max_positions` is limited (often 1), select entries cross-sectionally rather than trading whichever symbol happens to be processed first.

## Why
Current flow iterates `state.universe` in order; if multiple symbols signal in the same cycle, the first approved entry wins and the rest are skipped due to `max_positions`. This is an avoidable source of underperformance.

## Deliverables
- In each strategy cycle:
  - compute signals for all symbols first
  - score them consistently
  - choose the top K trades given portfolio constraints (`max_positions`, risk, funding/news blocks)
- Add deterministic tie-break rules (e.g., higher score → lower funding penalty → higher liquidity).
- Emit one summary event per cycle that captures:
  - all candidate signals and why the chosen ones won

## Acceptance Criteria
- With `max_positions=1`, the bot always chooses the highest-quality eligible trade in the universe for that candle.
- Decision is auditable from `logs/thinking.jsonl` and the event ledger.

