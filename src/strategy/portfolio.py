"""Cross-sectional portfolio selection for trade allocation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from src.strategy.scoring import CompositeScore

if TYPE_CHECKING:
    from src.ledger.state import Position, TradingState
    from src.models import TradeProposal
    from src.risk.engine import RiskCheckResult


@dataclass
class TradeCandidate:
    """A trade candidate for cross-sectional selection."""

    symbol: str
    proposal: TradeProposal
    risk_result: RiskCheckResult
    score: CompositeScore
    funding_rate: float
    news_risk: str
    candle_timestamp: datetime
    rank: int | None = None  # Assigned after cross-sectional ranking
    selected: bool = False


class PortfolioSelector:
    """Select top K trades cross-sectionally based on score and constraints."""

    def __init__(self, max_positions: int) -> None:
        self.max_positions = max_positions

    def select(
        self,
        candidates: list[TradeCandidate],
        current_positions: dict[str, Position],
        blocked_symbols: set[str],
        state: TradingState,
    ) -> list[TradeCandidate]:
        """
        Select top K eligible candidates.

        Args:
            candidates: All risk-approved trade candidates
            current_positions: Currently open positions
            blocked_symbols: Symbols blocked by news risk
            state: Current trading state (for max_positions check)

        Returns:
            Selected candidates (max K), with ranks assigned
        """
        # Filter out ineligible candidates
        ineligible_reasons: dict[str, str] = {}
        eligible: list[TradeCandidate] = []

        # Count current non-reduce-only positions
        current_entry_count = sum(
            1 for pos in current_positions.values() if pos.symbol not in blocked_symbols
        )

        for candidate in candidates:
            reason = self._check_eligibility(
                candidate, current_positions, blocked_symbols, current_entry_count
            )
            if reason is None:
                eligible.append(candidate)
            else:
                ineligible_reasons[candidate.symbol] = reason

        # Sort by composite score (descending), then tie-breakers
        eligible.sort(key=self._sort_key)

        # Assign ranks and determine selected
        available_slots = max(0, self.max_positions - current_entry_count)
        selected: list[TradeCandidate] = []

        for i, candidate in enumerate(eligible):
            candidate.rank = i + 1
            if i < available_slots:
                candidate.selected = True
                selected.append(candidate)

        return selected

    def _sort_key(self, candidate: TradeCandidate) -> tuple:
        """Sort key: composite score (desc), funding_penalty (desc), liquidity (desc)."""
        return (
            -candidate.score.composite,
            -candidate.score.funding_penalty,
            -candidate.score.liquidity_score,
        )

    def _check_eligibility(
        self,
        candidate: TradeCandidate,
        current_positions: dict[str, Position],
        blocked_symbols: set[str],
        current_entry_count: int,
    ) -> str | None:
        """Check if candidate is eligible. Returns reason if ineligible, None if eligible."""
        # Check if symbol is blocked by news
        if candidate.symbol in blocked_symbols:
            return f"news_blocked ({candidate.news_risk})"

        # Check if already have position in this symbol
        if candidate.symbol in current_positions:
            return "already_have_position"

        # Check if at max positions
        if current_entry_count >= self.max_positions:
            return f"max_positions_reached ({current_entry_count}/{self.max_positions})"

        return None

    def get_selection_summary(
        self,
        all_candidates: list[TradeCandidate],
        selected: list[TradeCandidate],
        ineligible_reasons: dict[str, str],
    ) -> dict:
        """Generate a summary of the selection process for logging/auditing."""
        return {
            "total_candidates": len(all_candidates),
            "selected_count": len(selected),
            "ineligible_count": len(ineligible_reasons),
            "selected": [
                {
                    "symbol": c.symbol,
                    "score": c.score.composite,
                    "rank": c.rank,
                    "side": c.proposal.side,
                }
                for c in selected
            ],
            "not_selected": [
                {
                    "symbol": c.symbol,
                    "score": c.score.composite,
                    "rank": c.rank,
                    "reason": "score_too_low",
                }
                for c in all_candidates
                if c not in selected and c.symbol not in ineligible_reasons
            ],
            "ineligible": [
                {
                    "symbol": symbol,
                    "reason": reason,
                }
                for symbol, reason in ineligible_reasons.items()
            ],
        }
