"""Realistic execution simulation for backtesting.

Models variable slippage, partial fills, and limit order fill probability
to make backtest results more realistic.
"""

from __future__ import annotations

from enum import StrEnum
from random import Random
from typing import Literal


class VolatilityRegime(StrEnum):
    """Market volatility regime classification."""

    LOW = "LOW"      # Calm market (ATR% < 0.5%)
    NORMAL = "NORMAL"  # Normal volatility (ATR% 0.5-1.5%)
    HIGH = "HIGH"    # High volatility (ATR% > 1.5%)


def detect_volatility_regime(atr_pct: float) -> VolatilityRegime:
    """Detect volatility regime based on ATR percentage.

    Args:
        atr_pct: ATR as a percentage of price (e.g., 0.01 = 1%)

    Returns:
        Volatility regime classification
    """
    if atr_pct < 0.005:  # < 0.5%
        return VolatilityRegime.LOW
    elif atr_pct < 0.015:  # 0.5-1.5%
        return VolatilityRegime.NORMAL
    else:  # > 1.5%
        return VolatilityRegime.HIGH


def estimate_slippage(
    atr: float,
    price: float,
    order_type: Literal["LIMIT", "MARKET"],
    volatility_regime: VolatilityRegime,
    base_bps: float = 2.0,
    atr_scale: float = 1.0,
) -> float:
    """Estimate slippage as a decimal fraction.

    Args:
        atr: Average True Range in price units
        price: Current asset price
        order_type: Order type - LIMIT or MARKET
        volatility_regime: Current market volatility regime
        base_bps: Base slippage in basis points (default: 2 bps = 0.02%)
        atr_scale: Scaling factor for ATR component (default: 1.0)

    Returns:
        Slippage as decimal (0.001 = 0.1%)

    Formula:
        Base slippage: base_bps / 10000
        ATR scaling: +0.01% per 1% ATR (atr_pct / 100)
        Volatility multiplier: LOW=0.5x, NORMAL=1x, HIGH=2x
        Market orders: +0.03% vs limit
    """
    atr_pct = atr / price

    # Base slippage in decimal
    base_slippage = base_bps / 10000.0

    # ATR component: +0.01% per 1% ATR
    atr_component = (atr_pct / 100.0) * atr_scale

    # Volatility regime multipliers
    regime_multiplier = {
        VolatilityRegime.LOW: 0.5,
        VolatilityRegime.NORMAL: 1.0,
        VolatilityRegime.HIGH: 2.0,
    }[volatility_regime]

    limit_slippage = (base_slippage + atr_component) * regime_multiplier

    # Market order penalty (higher slippage for market orders). Compute via
    # addition to ensure deterministic arithmetic (tests assert exact delta).
    if order_type == "MARKET":
        return limit_slippage + 0.0003

    return limit_slippage


def estimate_fill_probability(
    limit_distance_bps: float,
    holding_time_bars: int,
    volatility: float,
    base_fill_prob: float = 1.0,
) -> float:
    """Estimate probability of a limit order being filled.

    Args:
        limit_distance_bps: How far limit price is from market in bps
                           (positive = buy below market, sell above market)
        holding_time_bars: Number of bars the order is active
        volatility: ATR as percentage (e.g., 0.01 = 1%)
        base_fill_prob: Base fill probability multiplier (default: 0.5)

    Returns:
        Probability of fill (0.0 to 1.0)

    Model:
        - Aggressive limit (within 5 bps): ~80% fill in 1 bar
        - Passive limit (20+ bps): ~20% fill in 1 bar
        - Each additional bar: +15% cumulative (capped)
        - High volatility: +20% fill probability
    """
    # Base probability from distance (closer = higher fill rate)
    if limit_distance_bps <= 5:
        # Aggressive limit: high base fill rate
        distance_prob = 0.8
    elif limit_distance_bps <= 10:
        distance_prob = 0.6
    elif limit_distance_bps <= 20:
        distance_prob = 0.4
    else:
        # Passive limit: low base fill rate
        distance_prob = 0.2

    # Time component: each additional bar adds 15%, capped at 95%
    time_bonus = min(holding_time_bars * 0.15, 0.95 - distance_prob)

    # Volatility bonus: high volatility increases fill probability
    volatility_bonus = 0.0
    if volatility > 0.02:  # > 2% ATR
        volatility_bonus = 0.2
    elif volatility > 0.015:  # > 1.5% ATR
        volatility_bonus = 0.1
    elif volatility > 0.01:  # > 1% ATR
        volatility_bonus = 0.05

    # Apply base fill probability multiplier
    base_fill = base_fill_prob

    # Calculate final probability
    prob = (distance_prob + time_bonus + volatility_bonus) * base_fill

    # Clamp to valid range
    return max(0.0, min(1.0, prob))


class ExecutionSimulator:
    """Simulates order execution with realistic fill behavior.

    Attributes:
        random: Random number generator for probabilistic fills
    """

    def __init__(
        self,
        random_seed: int | None = None,
    ) -> None:
        """Initialize the execution simulator.

        Args:
            random_seed: Seed for reproducibility. If None, uses system entropy.
        """
        self.random = Random(random_seed)

    def fill_order(
        self,
        proposal_price: float,
        current_price: float,
        order_type: Literal["LIMIT", "MARKET"],
        atr: float,
        atr_pct: float,
        limit_distance_bps: float = 0.0,
        holding_time_bars: int = 1,
    ) -> tuple[bool, float, bool]:
        """Simulate order execution and determine fill details.

        Args:
            proposal_price: The price the order was placed at
            current_price: Current market price
            order_type: Order type - LIMIT or MARKET
            atr: ATR in price units
            atr_pct: ATR as percentage of price
            limit_distance_bps: Distance from market for limit orders
            holding_time_bars: Bars the order has been waiting

        Returns:
            Tuple of (filled: bool, fill_price: float, is_partial: bool)
        """
        regime = detect_volatility_regime(atr_pct)

        # Calculate slippage for this execution
        slippage = estimate_slippage(
            atr=atr,
            price=current_price,
            order_type=order_type,
            volatility_regime=regime,
        )

        # Determine if order fills
        if order_type == "MARKET":
            # Market orders always fill (with slippage)
            filled = True
            partial = False
        else:
            # Limit orders: check fill probability
            fill_prob = estimate_fill_probability(
                limit_distance_bps=limit_distance_bps,
                holding_time_bars=holding_time_bars,
                volatility=atr_pct,
            )
            filled = self.random.random() < fill_prob

            # Partial fill simulation: 50% chance if filled
            partial = filled and self.random.random() < 0.5

        # Calculate fill price with slippage
        if filled:
            if proposal_price >= current_price:
                # Buy order: price goes up
                fill_price = proposal_price * (1 + slippage)
            else:
                # Sell order: price goes down
                fill_price = proposal_price * (1 - slippage)
        else:
            fill_price = proposal_price

        return filled, fill_price, partial

    def reset_seed(self, seed: int) -> None:
        """Reset the random seed for reproducibility.

        Args:
            seed: New random seed
        """
        self.random.seed(seed)
