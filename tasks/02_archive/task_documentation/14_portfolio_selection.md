# Task 14: Portfolio Selection (Cross-Sectional Trade Selection)

## What Was Before

The `strategy_loop` in `src/main.py` processed symbols sequentially and immediately executed the first approved trade. If `max_positions=1` and multiple symbols signaled in the same cycle, the first one in the universe list would win:

```python
# Old behavior (sequential execution)
for symbol in state.universe:
    # ... generate signal ...
    if risk_result.approved:
        # Immediately executes - first approved wins!
        await execution_engine.execute_entry(proposal, risk_result, filters, state)
```

## What Was Changed

### 1. New `TradeCandidate` dataclass and `PortfolioSelector` class (`src/strategy/portfolio.py`)

Created a new module for cross-sectional trade selection:

```python
@dataclass
class TradeCandidate:
    symbol: str
    proposal: TradeProposal
    risk_result: RiskCheckResult
    score: CompositeScore
    funding_rate: float
    news_risk: str
    candle_timestamp: datetime
    rank: int | None = None
    selected: bool = False

class PortfolioSelector:
    """Select top K trades cross-sectionally based on score and constraints."""

    def select(self, candidates, current_positions, blocked_symbols, state):
        # Filter ineligible candidates
        # Sort by composite score, funding_penalty, liquidity_score
        # Return top K with ranks assigned
```

### 2. New event type (`src/ledger/events.py`)

Added `TRADE_CYCLE_COMPLETED` event type to capture cycle summary.

### 3. Refactored `strategy_loop` (`src/main.py`)

Changed from sequential execution to four-phase approach:

**Phase 1: Collect all candidates** (don't execute yet)
- Generate signals for all symbols
- Evaluate risk for entry signals
- Collect approved trades into `candidates` list

**Phase 2: Cross-sectional selection**
- Filter out ineligible candidates (news blocked, already have position, max positions reached)
- Sort by composite score (desc), funding penalty (desc), liquidity (desc)
- Assign ranks and select top K

**Phase 3: Execute selected trades**
- Execute only the selected trades
- Publish `RISK_APPROVED` event for each

**Phase 4: Emit cycle summary**
- Publish `TRADE_CYCLE_COMPLETED` event with:
  - Total candidates
  - Selected trades with ranks
  - Ineligible reasons for non-selected trades

## Why This Change Was Needed

**Problem Scenario:**
1. Universe has symbols [A, B, C] with `max_positions=1`
2. All three generate signals in the same cycle
3. A is first alphabetically, so it gets executed immediately
4. B and C (potentially higher quality) are rejected due to `max_positions`

**Solution:**
Now all candidates are collected first, ranked by score, and the top K are selected. The highest quality eligible trade wins, not the first one in the list.

## Tie-Breaking Rules

When scores are equal, the selection uses deterministic tie-breakers:
1. Higher composite score wins
2. If tied, higher funding penalty score wins (favoring favorable funding)
3. If tied, higher liquidity score wins (favoring tighter spreads)

## Auditability

The `TRADE_CYCLE_COMPLETED` event provides complete audit trail:
- All candidate symbols and their scores
- Why each candidate was/wasn't selected
- Rankings for transparency

## Files Modified

1. **New file**: `src/strategy/portfolio.py` - TradeCandidate and PortfolioSelector
2. **Modified**: `src/main.py` - Refactored strategy_loop
3. **Modified**: `src/ledger/events.py` - Added TRADE_CYCLE_COMPLETED event

## Acceptance Criteria Met

- With `max_positions=1`, the bot now chooses the highest-quality eligible trade
- Decision is auditable from `logs/thinking.jsonl` and event ledger via `TRADE_CYCLE_COMPLETED`
