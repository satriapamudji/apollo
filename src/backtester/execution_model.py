"""Execution models for backtesting order fills.

Provides different execution assumptions:
- IdealExecution: Perfect fills at signal price (+ fixed slippage)
- RealisticExecution: Probabilistic fills based on volatility (wraps ExecutionSimulator)

These models determine how trade proposals become filled orders in backtest.
"""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from src.backtester.execution_sim import ExecutionSimulator

if TYPE_CHECKING:
    from src.models import TradeProposal


@dataclass(frozen=True)
class FillResult:
    """Result of an execution simulation.

    Attributes:
        filled: Whether the order was filled (even partially)
        fill_price: Actual execution price (None if not filled)
        fill_quantity: Quantity filled (may be less than requested for partials)
        is_partial: Whether this was a partial fill
        slippage_bps: Realized slippage in basis points
        rejection_reason: Reason for rejection if not filled
    """

    filled: bool
    fill_price: float | None = None
    fill_quantity: float | None = None
    is_partial: bool = False
    slippage_bps: float = 0.0
    rejection_reason: str | None = None


class ExecutionModel(Protocol):
    """Protocol for execution simulation.

    Implementations determine how orders are filled during backtest:
    - Whether the order fills at all
    - At what price
    - With what slippage
    """

    @abstractmethod
    def simulate_fill(
        self,
        proposal: TradeProposal,
        current_price: float,
        atr: float,
        requested_quantity: float,
    ) -> FillResult:
        """Simulate order execution.

        Args:
            proposal: Trade proposal with entry price
            current_price: Current market price
            atr: Average True Range for volatility context
            requested_quantity: Desired position size

        Returns:
            FillResult describing the execution outcome
        """
        ...


class IdealExecution:
    """Perfect execution with fixed slippage.

    All orders fill at the proposal price plus a fixed slippage percentage.
    This is the simplest model, useful for baseline comparisons.
    """

    def __init__(self, slippage_pct: float = 0.0005) -> None:
        """Initialize with slippage percentage.

        Args:
            slippage_pct: Fixed slippage as decimal (0.0005 = 5 bps)
        """
        self.slippage_pct = slippage_pct

    def simulate_fill(
        self,
        proposal: TradeProposal,
        current_price: float,
        atr: float,
        requested_quantity: float,
    ) -> FillResult:
        """Simulate perfect fill with fixed slippage.

        Long orders pay slippage above entry, shorts pay below.
        """
        if proposal.side == "LONG":
            fill_price = proposal.entry_price * (1 + self.slippage_pct)
        else:
            fill_price = proposal.entry_price * (1 - self.slippage_pct)

        slippage_bps = self.slippage_pct * 10000

        return FillResult(
            filled=True,
            fill_price=fill_price,
            fill_quantity=requested_quantity,
            is_partial=False,
            slippage_bps=slippage_bps,
        )


class RealisticExecution:
    """Probabilistic execution with volatility-dependent outcomes.

    Wraps ExecutionSimulator to provide:
    - Fill probability based on order type and volatility regime
    - Partial fills in high volatility
    - Variable slippage based on ATR
    """

    def __init__(self, random_seed: int | None = None) -> None:
        """Initialize with optional random seed for reproducibility.

        Args:
            random_seed: Seed for deterministic simulation
        """
        self.simulator = ExecutionSimulator(random_seed=random_seed)

    def simulate_fill(
        self,
        proposal: TradeProposal,
        current_price: float,
        atr: float,
        requested_quantity: float,
    ) -> FillResult:
        """Simulate execution with realistic market dynamics.

        Uses ExecutionSimulator for probabilistic fill determination.
        """
        atr_pct = atr / current_price if current_price > 0 else 0.0

        # Convert proposal side to fill_order side format
        # LONG -> BUY, SHORT -> SELL
        order_side = "BUY" if proposal.side == "LONG" else "SELL"

        filled, fill_price, is_partial = self.simulator.fill_order(
            proposal_price=proposal.entry_price,
            current_price=current_price,
            order_type="LIMIT",
            atr=atr,
            atr_pct=atr_pct,
            side=order_side,
        )

        if not filled:
            return FillResult(
                filled=False,
                rejection_reason="fill_probability_miss",
            )

        # Calculate actual slippage
        if proposal.side == "LONG":
            slippage_bps = ((fill_price - proposal.entry_price) / proposal.entry_price) * 10000
        else:
            slippage_bps = ((proposal.entry_price - fill_price) / proposal.entry_price) * 10000

        # Adjust quantity for partial fills
        fill_quantity = requested_quantity * 0.5 if is_partial else requested_quantity

        return FillResult(
            filled=True,
            fill_price=fill_price,
            fill_quantity=fill_quantity,
            is_partial=is_partial,
            slippage_bps=abs(slippage_bps),
        )


class SpreadAwareExecution:
    """Execution model that rejects trades when spread exceeds threshold.

    Stub for Task 33 - combines with another execution model and adds
    spread-based rejection logic.
    """

    def __init__(
        self,
        base_model: ExecutionModel,
        max_spread_bps: float = 10.0,
    ) -> None:
        """Initialize with base execution model.

        Args:
            base_model: Underlying execution model to use when spread is OK
            max_spread_bps: Maximum allowable spread in basis points
        """
        self.base_model = base_model
        self.max_spread_bps = max_spread_bps
        self._current_spread_bps: float | None = None

    def set_current_spread(self, spread_bps: float) -> None:
        """Set the current spread for the next fill simulation.

        Called by the backtest engine when a SpreadEvent is processed.
        """
        self._current_spread_bps = spread_bps

    def simulate_fill(
        self,
        proposal: TradeProposal,
        current_price: float,
        atr: float,
        requested_quantity: float,
    ) -> FillResult:
        """Simulate fill with spread check.

        Rejects if current spread exceeds threshold.
        """
        if self._current_spread_bps is not None:
            if self._current_spread_bps > self.max_spread_bps:
                return FillResult(
                    filled=False,
                    rejection_reason=(
                        f"spread_too_wide ({self._current_spread_bps:.1f} > {self.max_spread_bps})"
                    ),
                )

        return self.base_model.simulate_fill(
            proposal=proposal,
            current_price=current_price,
            atr=atr,
            requested_quantity=requested_quantity,
        )


def create_execution_model(
    model_type: str = "ideal",
    slippage_pct: float = 0.0005,
    random_seed: int | None = None,
    max_spread_bps: float | None = None,
) -> ExecutionModel:
    """Factory function for creating execution models.

    Args:
        model_type: "ideal" or "realistic"
        slippage_pct: Fixed slippage for ideal model
        random_seed: Random seed for realistic model
        max_spread_bps: If provided, wraps model with spread-aware execution

    Returns:
        Configured ExecutionModel instance
    """
    if model_type == "realistic":
        base: ExecutionModel = RealisticExecution(random_seed=random_seed)
    else:
        base = IdealExecution(slippage_pct=slippage_pct)

    if max_spread_bps is not None:
        return SpreadAwareExecution(base, max_spread_bps=max_spread_bps)

    return base
