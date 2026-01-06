"""Paper mode execution simulator with realistic fills, slippage, and fees.

Uses live bookTicker data for spread and models execution realism so that
paper mode predicts live outcomes (fill rate, slippage, fees) instead of
optimistic instant fills.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from random import Random
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

import structlog

if TYPE_CHECKING:
    from src.config.settings import PaperSimConfig
    from src.connectors.rest_client import BinanceRestClient


class SimulatedOrderStatus(StrEnum):
    """Status of a simulated order."""

    PENDING = "PENDING"  # Order placed, awaiting fill
    FILLED = "FILLED"  # Order fully filled
    PARTIALLY_FILLED = "PARTIALLY_FILLED"  # Order partially filled
    EXPIRED = "EXPIRED"  # Order expired without fill
    CANCELLED = "CANCELLED"  # Order cancelled


@dataclass
class SimulatedOrder:
    """A simulated order in paper mode."""

    client_order_id: str
    symbol: str
    side: Literal["BUY", "SELL"]
    order_type: str
    quantity: float
    price: float | None
    stop_price: float | None
    reduce_only: bool
    status: SimulatedOrderStatus
    created_at: datetime
    trade_id: str | None = None
    # Execution details
    filled_quantity: float = 0.0
    avg_fill_price: float = 0.0
    fees_paid: float = 0.0
    # Fill tracking
    bars_waiting: int = 0
    last_check_time: datetime | None = None


@dataclass
class FillResult:
    """Result of a simulated fill attempt."""

    filled: bool
    fill_price: float
    fill_quantity: float
    is_partial: bool
    slippage_bps: float
    fees: float
    reason: str | None = None  # Reason for no-fill


@dataclass
class BookTickerData:
    """Best bid/ask from bookTicker."""

    bid_price: float
    ask_price: float
    bid_qty: float
    ask_qty: float
    spread_pct: float
    mid_price: float


class VolatilityRegime(StrEnum):
    """Market volatility regime classification."""

    LOW = "LOW"  # Calm market (ATR% < 0.5%)
    NORMAL = "NORMAL"  # Normal volatility (ATR% 0.5-1.5%)
    HIGH = "HIGH"  # High volatility (ATR% > 1.5%)


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


class PaperExecutionSimulator:
    """Simulates realistic order execution for paper trading.

    Features:
    - Uses live bookTicker for spread data
    - Models slippage as function of spread + volatility
    - Models limit order fill probability (price must trade through)
    - Applies maker/taker fees
    - Tracks pending orders with expiration
    """

    def __init__(
        self,
        config: PaperSimConfig,
        rest_client: BinanceRestClient | None = None,
    ) -> None:
        """Initialize the paper execution simulator.

        Args:
            config: Paper simulation configuration
            rest_client: Binance REST client for fetching bookTicker
        """
        self.config = config
        self.rest = rest_client
        self.random = Random(config.random_seed)
        self.log = structlog.get_logger(__name__)

        # Track pending simulated orders
        self._pending_orders: dict[str, SimulatedOrder] = {}

        # Cache last bookTicker data per symbol (avoid excessive API calls)
        self._book_ticker_cache: dict[str, tuple[datetime, BookTickerData]] = {}
        self._cache_ttl_seconds = config.book_ticker_cache_seconds

    async def get_book_ticker(self, symbol: str) -> BookTickerData | None:
        """Fetch best bid/ask for a symbol with caching.

        Args:
            symbol: Trading pair symbol

        Returns:
            BookTickerData or None if fetch failed
        """
        now = datetime.now(timezone.utc)

        # Check cache
        if symbol in self._book_ticker_cache:
            cached_time, cached_data = self._book_ticker_cache[symbol]
            age = (now - cached_time).total_seconds()
            if age < self._cache_ttl_seconds:
                return cached_data

        # Fetch from API
        if self.rest is None:
            return None

        try:
            ticker = await self.rest.get_book_ticker(symbol)
            bid_price = float(ticker.get("bidPrice", 0))
            ask_price = float(ticker.get("askPrice", 0))
            bid_qty = float(ticker.get("bidQty", 0))
            ask_qty = float(ticker.get("askQty", 0))

            if bid_price <= 0 or ask_price <= 0:
                return None

            mid_price = (bid_price + ask_price) / 2
            spread_pct = ((ask_price - bid_price) / mid_price) * 100

            data = BookTickerData(
                bid_price=bid_price,
                ask_price=ask_price,
                bid_qty=bid_qty,
                ask_qty=ask_qty,
                spread_pct=spread_pct,
                mid_price=mid_price,
            )
            self._book_ticker_cache[symbol] = (now, data)
            return data

        except Exception as exc:
            self.log.warning(
                "book_ticker_fetch_failed",
                symbol=symbol,
                error=str(exc),
            )
            return None

    def estimate_slippage(
        self,
        atr: float,
        price: float,
        order_type: str,
        spread_pct: float,
    ) -> float:
        """Estimate slippage as a decimal fraction.

        Args:
            atr: Average True Range in price units
            price: Current asset price
            order_type: Order type - LIMIT or MARKET
            spread_pct: Current spread percentage

        Returns:
            Slippage as decimal (0.001 = 0.1%)
        """
        atr_pct = atr / price if price > 0 else 0.0
        regime = detect_volatility_regime(atr_pct)

        # Base slippage in decimal
        base_slippage = self.config.slippage_base_bps / 10000.0

        # ATR component: scales with volatility
        atr_component = (atr_pct / 100.0) * self.config.slippage_atr_scale

        # Spread component: half the spread contributes to slippage
        spread_component = (spread_pct / 100.0) / 2

        # Volatility regime multipliers
        regime_multiplier = {
            VolatilityRegime.LOW: 0.5,
            VolatilityRegime.NORMAL: 1.0,
            VolatilityRegime.HIGH: 2.0,
        }[regime]

        limit_slippage = (base_slippage + atr_component + spread_component) * regime_multiplier

        # Market order penalty (higher slippage for market orders)
        if order_type == "MARKET":
            return limit_slippage + 0.0003  # +3 bps for market orders

        return limit_slippage

    def estimate_fill_probability(
        self,
        limit_distance_bps: float,
        holding_time_bars: int,
        atr_pct: float,
        spread_pct: float,
    ) -> float:
        """Estimate probability of a limit order being filled.

        Args:
            limit_distance_bps: How far limit price is from market in bps
            holding_time_bars: Number of bars the order is active
            atr_pct: ATR as percentage (e.g., 0.01 = 1%)
            spread_pct: Current spread percentage

        Returns:
            Probability of fill (0.0 to 1.0)
        """
        # Base probability from distance (closer = higher fill rate)
        if limit_distance_bps <= 5:
            distance_prob = 0.85  # Very aggressive
        elif limit_distance_bps <= 10:
            distance_prob = 0.70
        elif limit_distance_bps <= 20:
            distance_prob = 0.50
        elif limit_distance_bps <= 50:
            distance_prob = 0.30
        else:
            distance_prob = 0.15  # Very passive

        # Time component: each additional bar adds fill probability
        time_bonus = min(holding_time_bars * 0.10, 0.40)

        # Volatility bonus: high volatility increases fill probability
        volatility_bonus = 0.0
        if atr_pct > 0.02:  # > 2% ATR
            volatility_bonus = 0.15
        elif atr_pct > 0.015:  # > 1.5% ATR
            volatility_bonus = 0.10
        elif atr_pct > 0.01:  # > 1% ATR
            volatility_bonus = 0.05

        # Wide spread penalty: harder to fill with wide spreads
        spread_penalty = 0.0
        if spread_pct > 0.1:  # > 0.1% spread
            spread_penalty = min(spread_pct * 0.5, 0.20)

        # Calculate final probability
        prob = distance_prob + time_bonus + volatility_bonus - spread_penalty

        # Clamp to valid range
        return max(0.05, min(0.95, prob))

    def calculate_fees(
        self,
        notional: float,
        order_type: str,
    ) -> float:
        """Calculate trading fees for an order.

        Args:
            notional: Order notional value (price * quantity)
            order_type: LIMIT or MARKET

        Returns:
            Fee amount in quote currency
        """
        if order_type == "LIMIT":
            fee_rate = self.config.maker_fee_pct / 100.0
        else:
            fee_rate = self.config.taker_fee_pct / 100.0

        return notional * fee_rate

    async def simulate_fill(
        self,
        symbol: str,
        side: Literal["BUY", "SELL"],
        order_type: str,
        quantity: float,
        limit_price: float | None,
        atr: float,
        holding_bars: int = 1,
    ) -> FillResult:
        """Simulate order fill with realistic execution.

        Args:
            symbol: Trading pair
            side: BUY or SELL
            order_type: LIMIT, MARKET, STOP_MARKET, etc.
            quantity: Order quantity
            limit_price: Limit price (for limit orders)
            atr: Average True Range for volatility estimation
            holding_bars: How many bars the order has been waiting

        Returns:
            FillResult with fill details
        """
        # Get current market data
        book_data = await self.get_book_ticker(symbol)

        if book_data is None:
            # Fallback: assume fill at limit price with minimal slippage
            self.log.warning(
                "book_ticker_unavailable_fallback",
                symbol=symbol,
            )
            fill_price = limit_price or 0.0
            slippage_bps = self.config.slippage_base_bps
            fees = self.calculate_fees(fill_price * quantity, order_type)
            return FillResult(
                filled=True,
                fill_price=fill_price,
                fill_quantity=quantity,
                is_partial=False,
                slippage_bps=slippage_bps,
                fees=fees,
            )

        current_price = book_data.mid_price
        atr_pct = atr / current_price if current_price > 0 else 0.0

        # Calculate slippage
        slippage = self.estimate_slippage(
            atr=atr,
            price=current_price,
            order_type=order_type,
            spread_pct=book_data.spread_pct,
        )
        slippage_bps = slippage * 10000

        # For market orders: always fill with slippage
        if order_type in ("MARKET", "STOP_MARKET", "TAKE_PROFIT_MARKET"):
            if side == "BUY":
                fill_price = book_data.ask_price * (1 + slippage)
            else:
                fill_price = book_data.bid_price * (1 - slippage)

            fees = self.calculate_fees(fill_price * quantity, "MARKET")
            return FillResult(
                filled=True,
                fill_price=fill_price,
                fill_quantity=quantity,
                is_partial=False,
                slippage_bps=slippage_bps,
                fees=fees,
            )

        # For limit orders: check fill probability
        if limit_price is None:
            limit_price = current_price

        # Calculate distance from market
        if side == "BUY":
            # Buy limit: how far below the ask?
            distance = (book_data.ask_price - limit_price) / book_data.ask_price
        else:
            # Sell limit: how far above the bid?
            distance = (limit_price - book_data.bid_price) / book_data.bid_price

        limit_distance_bps = max(0, distance * 10000)

        # Check if limit price is already through the market (immediate fill)
        immediate_fill = False
        if side == "BUY" and limit_price >= book_data.ask_price:
            immediate_fill = True
        elif side == "SELL" and limit_price <= book_data.bid_price:
            immediate_fill = True

        if immediate_fill:
            # Fill immediately at limit price (maker fill)
            fill_price = limit_price
            fees = self.calculate_fees(fill_price * quantity, "LIMIT")
            return FillResult(
                filled=True,
                fill_price=fill_price,
                fill_quantity=quantity,
                is_partial=False,
                slippage_bps=0.0,  # No slippage for limit fills
                fees=fees,
            )

        # Probabilistic fill
        fill_prob = self.estimate_fill_probability(
            limit_distance_bps=limit_distance_bps,
            holding_time_bars=holding_bars,
            atr_pct=atr_pct,
            spread_pct=book_data.spread_pct,
        )

        filled = self.random.random() < fill_prob

        if not filled:
            return FillResult(
                filled=False,
                fill_price=0.0,
                fill_quantity=0.0,
                is_partial=False,
                slippage_bps=0.0,
                fees=0.0,
                reason=f"LIMIT_NOT_FILLED (prob={fill_prob:.2f}, dist={limit_distance_bps:.1f}bps)",
            )

        # Partial fill simulation: use configured partial fill rate
        is_partial = self.random.random() < self.config.partial_fill_rate
        fill_quantity = quantity * 0.5 if is_partial else quantity

        # Limit orders fill at limit price (maker)
        fill_price = limit_price
        fees = self.calculate_fees(fill_price * fill_quantity, "LIMIT")

        return FillResult(
            filled=True,
            fill_price=fill_price,
            fill_quantity=fill_quantity,
            is_partial=is_partial,
            slippage_bps=0.0,  # No slippage for limit fills
            fees=fees,
        )

    def create_simulated_order(
        self,
        symbol: str,
        side: Literal["BUY", "SELL"],
        order_type: str,
        quantity: float,
        price: float | None,
        stop_price: float | None,
        reduce_only: bool,
        trade_id: str | None = None,
    ) -> SimulatedOrder:
        """Create a new simulated order.

        Args:
            symbol: Trading pair
            side: BUY or SELL
            order_type: Order type
            quantity: Order quantity
            price: Limit price
            stop_price: Stop price for stop orders
            reduce_only: Whether order is reduce-only
            trade_id: Associated trade ID

        Returns:
            SimulatedOrder instance
        """
        client_order_id = f"SIM-{uuid4().hex[:12]}"
        order = SimulatedOrder(
            client_order_id=client_order_id,
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            price=price,
            stop_price=stop_price,
            reduce_only=reduce_only,
            status=SimulatedOrderStatus.PENDING,
            created_at=datetime.now(timezone.utc),
            trade_id=trade_id,
        )
        self._pending_orders[client_order_id] = order
        return order

    def get_pending_order(self, client_order_id: str) -> SimulatedOrder | None:
        """Get a pending simulated order."""
        return self._pending_orders.get(client_order_id)

    def remove_pending_order(self, client_order_id: str) -> SimulatedOrder | None:
        """Remove a pending order from tracking."""
        return self._pending_orders.pop(client_order_id, None)

    def get_pending_orders_for_symbol(self, symbol: str) -> list[SimulatedOrder]:
        """Get all pending orders for a symbol."""
        return [o for o in self._pending_orders.values() if o.symbol == symbol]

    def reset_seed(self, seed: int) -> None:
        """Reset the random seed for reproducibility."""
        self.random.seed(seed)
