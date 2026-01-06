"""Universe selection logic."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import structlog

from src.config.settings import RiskConfig, UniverseConfig
from src.connectors.rest_client import BinanceRestClient
from src.ledger.bus import EventBus
from src.ledger.events import EventType

if TYPE_CHECKING:
    from src.risk.sizing import SymbolFilters

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class UniverseSymbol:
    symbol: str
    quote_volume: float
    funding_rate: float
    filters: list[dict[str, Any]]
    mark_price: float = 0.0
    min_equity_required: float = 0.0


@dataclass(frozen=True)
class FilteredSymbol:
    """A symbol that was filtered out due to tradeability constraints."""

    symbol: str
    reason: str
    min_equity_required: float
    current_equity: float
    mark_price: float
    min_qty: float
    min_notional: float
    step_size: float


@dataclass
class UniverseSelectionResult:
    """Result of universe selection including filtered symbols."""

    selected: list[UniverseSymbol]
    filtered: list[FilteredSymbol] = field(default_factory=list)


class UniverseSelector:
    """Select tradeable symbols based on liquidity and filters."""

    # Default stop distance as percentage of price (2% is typical for crypto)
    DEFAULT_STOP_DISTANCE_PCT = 2.0

    def __init__(
        self,
        rest: BinanceRestClient,
        config: UniverseConfig,
        risk_config: RiskConfig | None = None,
        equity: float = 0.0,
        event_bus: EventBus | None = None,
    ) -> None:
        self.rest = rest
        self.config = config
        self.risk_config = risk_config
        self.equity = equity
        self.event_bus = event_bus

    def set_equity(self, equity: float) -> None:
        """Update the current account equity for tradeability filtering."""
        self.equity = equity

    async def select(self) -> list[UniverseSymbol]:
        """Select tradeable symbols (legacy method for backwards compatibility)."""
        result = await self.select_with_filtering()
        return result.selected

    async def select_with_filtering(self) -> UniverseSelectionResult:
        """Select tradeable symbols and return filtering information."""
        exchange_info = await self.rest.get_exchange_info()
        symbols = [
            s
            for s in exchange_info.get("symbols", [])
            if s.get("status") == "TRADING"
            and s.get("contractType") == "PERPETUAL"
            and s.get("quoteAsset") == "USDT"
        ]
        tickers = await self.rest.get_24h_tickers()
        ticker_map = {t["symbol"]: t for t in tickers}

        pre_candidates: list[tuple[str, float, list[dict[str, Any]]]] = []
        for sym in symbols:
            ticker = ticker_map.get(sym["symbol"])
            if not ticker:
                continue
            quote_volume = float(ticker.get("quoteVolume", 0))
            if quote_volume < self.config.min_quote_volume_usd:
                continue
            min_notional = self._get_filter_value(sym, "MIN_NOTIONAL")
            if min_notional is not None and min_notional > self.config.max_min_notional_usd:
                continue
            pre_candidates.append((sym["symbol"], quote_volume, sym.get("filters", [])))

        pre_candidates.sort(key=lambda x: x[1], reverse=True)
        shortlist = pre_candidates[:30]

        candidates: list[UniverseSymbol] = []
        filtered: list[FilteredSymbol] = []

        for symbol, quote_volume, filters in shortlist:
            funding = await self.rest.get_funding_rate(symbol)
            if abs(funding * 100) > self.config.max_funding_rate_pct:
                continue

            # Get mark price for tradeability check
            mark_data = await self.rest.get_mark_price(symbol)
            mark_price = float(mark_data.get("markPrice", 0))

            # Extract symbol filters
            symbol_filters = self._extract_symbol_filters(filters)

            # Calculate minimum equity required
            min_equity = self._compute_min_equity_required(
                mark_price=mark_price,
                symbol_filters=symbol_filters,
            )

            # Check if account can trade this symbol
            if self.equity > 0 and min_equity > self.equity:
                filtered_symbol = FilteredSymbol(
                    symbol=symbol,
                    reason=self._get_filter_reason(symbol_filters, mark_price, min_equity),
                    min_equity_required=min_equity,
                    current_equity=self.equity,
                    mark_price=mark_price,
                    min_qty=symbol_filters.min_qty,
                    min_notional=symbol_filters.min_notional,
                    step_size=symbol_filters.step_size,
                )
                filtered.append(filtered_symbol)
                # Emit ledger event for filtering
                await self._emit_filter_event(filtered_symbol)
                log.info(
                    "symbol_filtered_tradeability",
                    symbol=symbol,
                    reason="insufficient_equity",
                    min_equity_required=round(min_equity, 2),
                    current_equity=round(self.equity, 2),
                    mark_price=round(mark_price, 2),
                    min_qty=symbol_filters.min_qty,
                    min_notional=symbol_filters.min_notional,
                )
                continue

            candidates.append(
                UniverseSymbol(
                    symbol=symbol,
                    quote_volume=quote_volume,
                    funding_rate=funding,
                    filters=filters,
                    mark_price=mark_price,
                    min_equity_required=min_equity,
                )
            )

        selected = candidates[: self.config.size]
        return UniverseSelectionResult(selected=selected, filtered=filtered)

    def _compute_min_equity_required(
        self,
        mark_price: float,
        symbol_filters: "SymbolFilters",  # noqa: UP037
    ) -> float:
        """Compute minimum equity required to trade this symbol.

        Uses the risk model to determine what equity is needed to place
        a valid trade that meets min_qty and min_notional requirements.

        Formula:
            min_position_value = max(min_qty * price, min_notional)
            risk_amount_needed = min_position_value * stop_distance_pct
            min_equity = risk_amount_needed / (risk_per_trade_pct / 100)
        """
        if mark_price <= 0:
            return float("inf")

        # Get risk parameters
        risk_pct = 1.0  # Default 1%
        if self.risk_config:
            risk_pct = self.risk_config.risk_per_trade_pct

        # Use default stop distance (2%)
        stop_distance_pct = self.DEFAULT_STOP_DISTANCE_PCT / 100

        # Calculate minimum position value to meet Binance filters
        min_qty_value = symbol_filters.min_qty * mark_price
        min_position_value = max(min_qty_value, symbol_filters.min_notional)

        # Account for step size - round up to next valid quantity
        if symbol_filters.step_size > 0:
            # Ensure we have at least one step size worth of quantity
            step_value = symbol_filters.step_size * mark_price
            min_position_value = max(min_position_value, step_value)

        # Calculate risk amount needed for this position
        # risk_amount = position_value * stop_distance_pct
        risk_amount_needed = min_position_value * stop_distance_pct

        # Calculate equity needed: equity * (risk_pct/100) >= risk_amount_needed
        # equity >= risk_amount_needed / (risk_pct/100)
        min_equity = risk_amount_needed / (risk_pct / 100)

        # Add 20% buffer for safety (slippage, fees, etc.)
        min_equity *= 1.2

        return min_equity

    def _extract_symbol_filters(self, filters: list[dict[str, Any]]) -> "SymbolFilters":  # noqa: UP037
        """Extract SymbolFilters from Binance filter list."""
        # Lazy import to avoid circular dependency
        from src.risk.sizing import SymbolFilters

        min_notional = 5.0
        min_qty = 0.001
        step_size = 0.001
        tick_size = 0.01

        for flt in filters:
            filter_type = flt.get("filterType")
            if filter_type == "MIN_NOTIONAL":
                min_notional = float(flt.get("notional", min_notional))
            elif filter_type == "LOT_SIZE":
                min_qty = float(flt.get("minQty", min_qty))
                step_size = float(flt.get("stepSize", step_size))
            elif filter_type == "PRICE_FILTER":
                tick_size = float(flt.get("tickSize", tick_size))

        return SymbolFilters(
            min_notional=min_notional,
            min_qty=min_qty,
            step_size=step_size,
            tick_size=tick_size,
        )

    def _get_filter_reason(
        self,
        symbol_filters: "SymbolFilters",  # noqa: UP037
        mark_price: float,
        min_equity: float,
    ) -> str:
        """Generate human-readable reason for filtering."""
        min_qty_value = symbol_filters.min_qty * mark_price
        if min_qty_value >= symbol_filters.min_notional:
            return (
                f"minQty constraint: {symbol_filters.min_qty} units @ ${mark_price:.2f} "
                f"= ${min_qty_value:.2f} requires ${min_equity:.2f} equity"
            )
        return (
            f"minNotional constraint: ${symbol_filters.min_notional:.2f} "
            f"requires ${min_equity:.2f} equity"
        )

    @staticmethod
    def _get_filter_value(symbol: dict[str, Any], filter_type: str) -> float | None:
        for flt in symbol.get("filters", []):
            if flt.get("filterType") == filter_type:
                return float(flt.get("notional", flt.get("minNotional", 0)))
        return None

    async def _emit_filter_event(self, filtered: FilteredSymbol) -> None:
        """Emit a SYMBOL_FILTERED event when a symbol is filtered out."""
        if self.event_bus is None:
            return
        try:
            await self.event_bus.publish(
                EventType.SYMBOL_FILTERED,
                {
                    "symbol": filtered.symbol,
                    "reason": filtered.reason,
                    "min_equity_required": filtered.min_equity_required,
                    "current_equity": filtered.current_equity,
                    "mark_price": filtered.mark_price,
                    "min_qty": filtered.min_qty,
                    "min_notional": filtered.min_notional,
                    "step_size": filtered.step_size,
                },
                {"source": "universe_selector"},
            )
        except Exception:
            log.exception("failed_to_emit_symbol_filtered_event", symbol=filtered.symbol)

    async def emit_funding_update(self, symbol: str, funding_rate: float, mark_price: float) -> None:
        """Emit a FUNDING_UPDATE event for a symbol.

        This is called on the 8-hour funding schedule to record the current
        funding rate and estimated carry for ledger reconstruction.
        """
        if self.event_bus is None:
            return
        try:
            # Calculate estimated carry for different position sizes
            # Carry per 8 hours = position_value * funding_rate
            carry_1k = 1000 * funding_rate  # $1k position
            carry_10k = 10000 * funding_rate  # $10k position
            carry_100k = 100000 * funding_rate  # $100k position

            await self.event_bus.publish(
                EventType.FUNDING_UPDATE,
                {
                    "symbol": symbol,
                    "funding_rate": funding_rate,
                    "funding_rate_pct": funding_rate * 100,
                    "mark_price": mark_price,
                    "estimated_carry_1k": carry_1k,
                    "estimated_carry_10k": carry_10k,
                    "estimated_carry_100k": carry_100k,
                    "estimated_daily_carry_1k": carry_1k * 3,  # 3 funding periods per day
                    "estimated_daily_carry_10k": carry_10k * 3,
                    "estimated_daily_carry_100k": carry_100k * 3,
                },
                {"source": "universe_selector"},
            )
            log.info(
                "funding_update_emitted",
                symbol=symbol,
                funding_rate=funding_rate,
                funding_rate_pct=f"{funding_rate * 100:.4f}%",
                mark_price=mark_price,
            )
        except Exception:
            log.exception("failed_to_emit_funding_update", symbol=symbol)
