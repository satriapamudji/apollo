"""Order execution engine for Binance Futures."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import ROUND_DOWN, ROUND_UP, Decimal
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

import httpx
import structlog

from src.config.settings import ExecutionConfig, Settings
from src.connectors.rest_client import BinanceRestClient
from src.execution.paper_simulator import PaperExecutionSimulator
from src.execution.state_store import PendingEntryStore
from src.ledger.bus import EventBus
from src.ledger.events import Event, EventType
from src.ledger.state import Position, TradingState
from src.models import TradeProposal
from src.risk.engine import RiskCheckResult, RiskEngine
from src.risk.sizing import PositionSizer, SymbolFilters

if TYPE_CHECKING:
    from src.monitoring.metrics import Metrics


@dataclass(frozen=True)
class OrderIntent:
    symbol: str
    side: Literal["BUY", "SELL"]
    order_type: str
    quantity: float
    price: float | None
    stop_price: float | None
    reduce_only: bool
    client_order_id: str


@dataclass(frozen=True)
class PendingEntry:
    proposal: TradeProposal
    stop_price: float | None
    tick_size: float
    lifecycle_state: str = "OPEN"  # OPEN, WAITING_FILL
    candle_timestamp: datetime | None = None
    attempt_count: int = 1
    original_client_order_id: str | None = None


@dataclass(frozen=True)
class EntryExecutionResult:
    placed: bool
    reason: str | None = None


class ExecutionEngine:
    """Submit and monitor orders after risk approval."""

    def __init__(
        self,
        settings: Settings,
        rest: BinanceRestClient,
        event_bus: EventBus,
        state_path: str | None = None,
    ) -> None:
        self.settings = settings
        self.rest = rest
        self.event_bus = event_bus
        self.config: ExecutionConfig = settings.execution
        self.log = structlog.get_logger(__name__)
        capped_risk = min(settings.risk.risk_per_trade_pct, RiskEngine.HARD_MAX_RISK_PCT)
        capped_leverage = min(settings.risk.max_leverage, RiskEngine.HARD_MAX_LEVERAGE)
        self.sizer = PositionSizer(risk_per_trade_pct=capped_risk, max_leverage=capped_leverage)
        self.trading_enabled, self.trading_block_reasons = settings.trading_gate()
        self.simulate = not self.trading_enabled
        self._margin_type_set: set[str] = set()
        self._leverage_set: dict[str, int] = {}
        self._position_mode_set = False
        self._pending_entries: dict[str, PendingEntry] = {}
        self._state_store: PendingEntryStore | None = None
        self._metrics: Metrics | None = None
        # Spread data captured during pre-trade check for use in ORDER_PLACED event
        self._last_spread_data: dict[str, Any] | None = None
        # Initialize paper simulator for realistic simulation mode
        self._paper_simulator: PaperExecutionSimulator | None = None
        if self.simulate and settings.paper_sim.enabled:
            self._paper_simulator = PaperExecutionSimulator(
                config=settings.paper_sim,
                rest_client=rest,
            )
        if state_path:
            self._state_store = PendingEntryStore(state_path)
            # Load persisted entries into memory
            for client_order_id, context in (self._state_store.load_all() or {}).items():
                entry = self._deserialize_entry(context)
                if entry:
                    self._pending_entries[client_order_id] = entry

    def set_metrics(self, metrics: Metrics) -> None:
        """Set the metrics instance for recording spread data.

        This allows deferred injection after ExecutionEngine is created,
        since Metrics may be instantiated later in the startup sequence.
        """
        self._metrics = metrics

    async def execute_entry(
        self,
        proposal: TradeProposal,
        risk_result: RiskCheckResult,
        symbol_filters: SymbolFilters,
        state: TradingState,
    ) -> EntryExecutionResult:
        if not risk_result.approved or not proposal.is_entry:
            return EntryExecutionResult(placed=False, reason="NOT_APPROVED")
        if risk_result.adjusted_entry_threshold and proposal.score:
            if proposal.score.composite < risk_result.adjusted_entry_threshold:
                return await self._skip_entry(
                    proposal,
                    "ENTRY_SCORE_BELOW_THRESHOLD",
                    {
                        "score": proposal.score.composite,
                        "threshold": risk_result.adjusted_entry_threshold,
                    },
                )

        adjusted_stop = proposal.stop_price
        if risk_result.adjusted_stop_multiplier and proposal.atr > 0:
            stop_distance = risk_result.adjusted_stop_multiplier * proposal.atr
            adjusted_stop = (
                proposal.entry_price - stop_distance
                if proposal.side == "LONG"
                else proposal.entry_price + stop_distance
            )

        sizing = self.sizer.calculate_size(
            equity=state.equity,
            entry_price=proposal.entry_price,
            stop_price=adjusted_stop or proposal.entry_price,
            symbol_filters=symbol_filters,
            leverage=proposal.leverage,
        )
        if not sizing:
            return await self._skip_entry(proposal, "SIZING_FAILED")

        quantity = sizing.quantity * risk_result.size_multiplier
        if symbol_filters.step_size > 0:
            step = Decimal(str(symbol_filters.step_size))
            quantity = float(
                (Decimal(str(quantity)) / step).to_integral_value(rounding=ROUND_DOWN) * step
            )
        if quantity < symbol_filters.min_qty:
            return await self._skip_entry(
                proposal,
                "MIN_QTY_NOT_MET",
                {"quantity": quantity, "min_qty": symbol_filters.min_qty},
            )
        if quantity * proposal.entry_price < symbol_filters.min_notional:
            return await self._skip_entry(
                proposal,
                "MIN_NOTIONAL_NOT_MET",
                {
                    "quantity": quantity,
                    "entry_price": proposal.entry_price,
                    "min_notional": symbol_filters.min_notional,
                },
            )
        if quantity <= 0:
            return await self._skip_entry(
                proposal,
                "NON_POSITIVE_QUANTITY",
                {"quantity": quantity},
            )
        if any(
            existing.symbol == proposal.symbol and not existing.reduce_only
            for existing in state.open_orders.values()
        ):
            return await self._skip_entry(proposal, "OPEN_ORDER_EXISTS")

        # Check spread and slippage constraints before proceeding
        is_acceptable, rejection_reason = await self._check_spread_slippage(
            proposal, proposal.entry_price, atr=proposal.atr
        )
        if not is_acceptable:
            # Build rejection payload with spread data
            rejection_payload: dict[str, Any] = {
                "symbol": proposal.symbol,
                "reason": rejection_reason,
                "trade_id": proposal.trade_id,
                "entry_price": proposal.entry_price,
            }
            if self._last_spread_data:
                rejection_payload.update(self._last_spread_data)
            await self.event_bus.publish(
                EventType.RISK_REJECTED,
                rejection_payload,
                {"source": "execution_engine", "trade_id": proposal.trade_id},
            )
            self.log.info(
                "trade_rejected_microstructure",
                symbol=proposal.symbol,
                reason=rejection_reason,
                trade_id=proposal.trade_id,
            )
            return await self._skip_entry(
                proposal,
                "MICROSTRUCTURE_REJECTED",
                {"reason_detail": rejection_reason},
            )

        # Check for existing pending entry from the same candle (lifecycle tracking)
        if self.config.entry_lifecycle_enabled and proposal.candle_timestamp:
            existing = self._find_pending_for_candle(proposal.symbol, proposal.candle_timestamp)
            if existing:
                self.log.info(
                    "resuming_order_tracking",
                    symbol=proposal.symbol,
                    client_order_id=existing.original_client_order_id or "unknown",
                    candle_timestamp=proposal.candle_timestamp.isoformat()
                    if proposal.candle_timestamp
                    else None,
                    trade_id=proposal.trade_id,
                )
                # Resume tracking - don't place a new order
                return await self._skip_entry(proposal, "PENDING_ENTRY_EXISTS")

        await self._ensure_account_settings(proposal.symbol, proposal.leverage)

        client_order_id = self._client_order_id(proposal.symbol, proposal.side)
        if client_order_id in state.open_orders:
            return await self._skip_entry(proposal, "DUPLICATE_CLIENT_ORDER_ID")

        order_type = "LIMIT"
        price = self._limit_price(proposal.entry_price, proposal.side, symbol_filters.tick_size)
        order = OrderIntent(
            symbol=proposal.symbol,
            side="BUY" if proposal.side == "LONG" else "SELL",
            order_type=order_type,
            quantity=quantity,
            price=price,
            stop_price=None,
            reduce_only=False,
            client_order_id=client_order_id,
        )

        try:
            await self._place_order(order, proposal.trade_id)
        except httpx.HTTPStatusError as exc:
            error_text = exc.response.text if exc.response is not None else str(exc)
            await self.event_bus.publish(
                EventType.ORDER_CANCELLED,
                {
                    "symbol": order.symbol,
                    "client_order_id": order.client_order_id,
                    "reason": error_text,
                },
                {"source": "execution_engine", "trade_id": proposal.trade_id},
            )
            self.log.warning(
                "order_rejected",
                symbol=order.symbol,
                error=error_text,
                status_code=getattr(exc.response, "status_code", None),
            )
            return EntryExecutionResult(placed=False, reason="ORDER_PLACE_FAILED")
        except httpx.RequestError as exc:
            error_text = str(exc)
            await self.event_bus.publish(
                EventType.ORDER_CANCELLED,
                {
                    "symbol": order.symbol,
                    "client_order_id": order.client_order_id,
                    "reason": error_text,
                },
                {"source": "execution_engine", "trade_id": proposal.trade_id},
            )
            self.log.warning("order_submit_failed", symbol=order.symbol, error=error_text)
            return EntryExecutionResult(placed=False, reason="ORDER_PLACE_FAILED")

        self._pending_entries[order.client_order_id] = PendingEntry(
            proposal=proposal,
            stop_price=adjusted_stop,
            tick_size=symbol_filters.tick_size,
            lifecycle_state="OPEN",
            candle_timestamp=proposal.candle_timestamp,
            attempt_count=1,
            original_client_order_id=order.client_order_id,
        )
        # Persist for restart safety
        if self._state_store:
            self._state_store.save(
                order.client_order_id,
                self._serialize_entry(self._pending_entries[order.client_order_id]),
            )

        order_status = None
        full_fill = True
        if not self.simulate and order_type != "MARKET":
            order_status = await self._await_order_fill(order, proposal)
            status = (order_status or {}).get("status")
            executed_qty = float((order_status or {}).get("executedQty", 0))
            avg_price = float((order_status or {}).get("avgPrice", 0))
            if status != "FILLED":
                full_fill = False
                if executed_qty > 0:
                    await self.event_bus.publish(
                        EventType.ORDER_PARTIAL_FILL,
                        {
                            "symbol": order.symbol,
                            "side": order.side,
                            "quantity": executed_qty,
                            "client_order_id": order.client_order_id,
                            "price": avg_price or proposal.entry_price,
                        },
                        {"source": "execution_engine", "trade_id": proposal.trade_id},
                    )
                    fill_qty = executed_qty
                    fill_price = avg_price or proposal.entry_price
                    await self._finalize_entry(order.client_order_id, fill_qty, fill_price)
                    await self._cancel_order_safe(order, proposal.trade_id, "PARTIAL_FILL_TIMEOUT")
                    return EntryExecutionResult(placed=True, reason="PARTIAL_FILL_TIMEOUT")
                else:
                    await self._cancel_order_safe(order, proposal.trade_id, status or "TIMEOUT")
                    return EntryExecutionResult(placed=True, reason="ORDER_TIMEOUT")
            else:
                fill_qty = executed_qty or order.quantity
                fill_price = avg_price or proposal.entry_price
                sim_metadata = {}  # No simulation metadata for live fills
        elif self._paper_simulator:
            # Realistic paper simulation with slippage and fill probability
            fill_result = await self._paper_simulator.simulate_fill(
                symbol=order.symbol,
                side=order.side,
                order_type=order.order_type,
                quantity=order.quantity,
                limit_price=order.price,
                atr=proposal.atr,
                holding_bars=1,
            )
            if not fill_result.filled:
                # Order did not fill - emit cancellation/expiration
                await self.event_bus.publish(
                    EventType.ORDER_EXPIRED,
                    {
                        "symbol": order.symbol,
                        "client_order_id": order.client_order_id,
                        "reason": fill_result.reason or "SIMULATED_NO_FILL",
                        "simulated": True,
                    },
                    {"source": "execution_engine", "trade_id": proposal.trade_id},
                )
                self.log.info(
                    "simulated_order_not_filled",
                    symbol=order.symbol,
                    reason=fill_result.reason,
                    trade_id=proposal.trade_id,
                )
                # Clean up pending entry
                self._pending_entries.pop(order.client_order_id, None)
                if self._state_store:
                    self._state_store.remove(order.client_order_id)
                return EntryExecutionResult(placed=True, reason="SIMULATED_NO_FILL")
            if fill_result.is_partial:
                # Partial fill in simulation
                full_fill = False
                await self.event_bus.publish(
                    EventType.ORDER_PARTIAL_FILL,
                    {
                        "symbol": order.symbol,
                        "side": order.side,
                        "quantity": fill_result.fill_quantity,
                        "client_order_id": order.client_order_id,
                        "price": fill_result.fill_price,
                        "simulated": True,
                        "slippage_bps": fill_result.slippage_bps,
                        "fees": fill_result.fees,
                    },
                    {"source": "execution_engine", "trade_id": proposal.trade_id},
                )
                fill_qty = fill_result.fill_quantity
                fill_price = fill_result.fill_price
                await self._finalize_entry(order.client_order_id, fill_qty, fill_price)
                return EntryExecutionResult(placed=True, reason="SIMULATED_PARTIAL_FILL")
            # Full fill
            fill_qty = fill_result.fill_quantity
            fill_price = fill_result.fill_price
            # Store simulation metadata for ORDER_FILLED event
            sim_metadata = {
                "simulated": True,
                "slippage_bps": fill_result.slippage_bps,
                "fees": fill_result.fees,
            }
        else:
            # Fallback: instant fill at exact price (legacy behavior)
            fill_qty = order.quantity
            fill_price = proposal.entry_price
            sim_metadata = {}

        if full_fill:
            fill_payload: dict[str, Any] = {
                "symbol": order.symbol,
                "side": order.side,
                "quantity": fill_qty,
                "client_order_id": order.client_order_id,
                "price": fill_price,
            }
            fill_payload.update(sim_metadata)
            await self.event_bus.publish(
                EventType.ORDER_FILLED,
                fill_payload,
                {"source": "execution_engine", "trade_id": proposal.trade_id},
            )
        return EntryExecutionResult(placed=True)

    async def _skip_entry(
        self,
        proposal: TradeProposal,
        reason: str,
        details: dict[str, Any] | None = None,
    ) -> EntryExecutionResult:
        payload: dict[str, Any] = {
            "symbol": proposal.symbol,
            "trade_id": proposal.trade_id,
            "reason": reason,
        }
        if details:
            payload.update(details)
        await self.event_bus.publish(
            EventType.ENTRY_SKIPPED,
            payload,
            {"source": "execution_engine", "trade_id": proposal.trade_id},
        )
        self.log.info(
            "entry_skipped",
            symbol=proposal.symbol,
            reason=reason,
            trade_id=proposal.trade_id,
        )
        return EntryExecutionResult(placed=False, reason=reason)

    async def execute_exit(
        self,
        position: Position,
        exit_price: float,
        reason: str,
    ) -> None:
        client_order_id = self._client_order_id(position.symbol, "EXIT")
        order = OrderIntent(
            symbol=position.symbol,
            side="SELL" if position.side == "LONG" else "BUY",
            order_type="MARKET",
            quantity=position.quantity,
            price=None,
            stop_price=None,
            reduce_only=True,
            client_order_id=client_order_id,
        )
        try:
            await self._place_order(order, position.trade_id)
        except httpx.HTTPStatusError as exc:
            error_text = exc.response.text if exc.response is not None else str(exc)
            self.log.warning(
                "exit_order_rejected",
                symbol=position.symbol,
                error=error_text,
                status_code=getattr(exc.response, "status_code", None),
            )
            return
        except httpx.RequestError as exc:
            error_text = str(exc)
            self.log.warning("exit_order_submit_failed", symbol=position.symbol, error=error_text)
            return
        fill_price = exit_price
        fill_qty = position.quantity
        order_id = None
        sim_metadata: dict[str, Any] = {}
        if not self.simulate:
            status = await self._await_order_fill(order)
            if status:
                order_id = status.get("orderId")
                filled = status.get("status") == "FILLED"
                executed_qty = float(status.get("executedQty", 0))
                avg_price = float(status.get("avgPrice", 0))
                if executed_qty <= 0 or not filled:
                    await self.event_bus.publish(
                        EventType.MANUAL_INTERVENTION,
                        {
                            "action": "EXIT_NOT_FILLED",
                            "symbol": position.symbol,
                            "status": status.get("status"),
                            "executed_qty": executed_qty,
                            "order_id": order_id,
                        },
                        {"source": "execution_engine", "trade_id": position.trade_id},
                    )
                    return
                fill_qty = executed_qty
                if avg_price > 0:
                    fill_price = avg_price
        elif self._paper_simulator:
            # Realistic exit simulation with market order slippage
            # Market exits always fill but with slippage
            # Use a conservative ATR estimate: 0.5% of exit price (typical crypto volatility)
            estimated_atr = exit_price * 0.005
            fill_result = await self._paper_simulator.simulate_fill(
                symbol=order.symbol,
                side=order.side,
                order_type="MARKET",
                quantity=order.quantity,
                limit_price=None,
                atr=estimated_atr,
                holding_bars=1,
            )
            # Market orders always fill
            fill_qty = fill_result.fill_quantity
            fill_price = fill_result.fill_price
            sim_metadata = {
                "simulated": True,
                "slippage_bps": fill_result.slippage_bps,
                "fees": fill_result.fees,
            }
        # Fallback: use exit_price directly (legacy behavior)
        pnl = (fill_price - position.entry_price) * fill_qty
        if position.side == "SHORT":
            pnl = -pnl

        close_payload: dict[str, Any] = {
            "symbol": position.symbol,
            "quantity": fill_qty,
            "exit_price": fill_price,
            "realized_pnl": pnl,
            "reason": reason,
            "trade_id": position.trade_id,
            "entry_price": position.entry_price,
            "side": position.side,
            "order_id": order_id,
        }
        close_payload.update(sim_metadata)
        await self.event_bus.publish(
            EventType.POSITION_CLOSED,
            close_payload,
            {"source": "execution_engine", "trade_id": position.trade_id},
        )

    async def handle_order_filled(self, event: Event) -> None:
        payload = event.payload or {}
        client_order_id = payload.get("client_order_id") or payload.get("clientOrderId")
        if not client_order_id:
            return
        pending = self._pending_entries.get(str(client_order_id))
        if not pending:
            return
        qty = float(payload.get("quantity", 0) or 0)
        price = float(payload.get("price", 0) or 0)
        if qty <= 0:
            return
        if price <= 0:
            price = pending.proposal.entry_price
        # Remove from state store before finalizing (no longer pending)
        if self._state_store:
            self._state_store.remove(str(client_order_id))
        await self._finalize_entry(str(client_order_id), qty, price)

    async def handle_order_cancelled(self, event: Event) -> None:
        payload = event.payload or {}
        client_order_id = payload.get("client_order_id") or payload.get("clientOrderId")
        if not client_order_id:
            return
        self._pending_entries.pop(str(client_order_id), None)
        # Remove from state store (order is no longer pending)
        if self._state_store:
            self._state_store.remove(str(client_order_id))

    async def _finalize_entry(
        self, client_order_id: str, fill_qty: float, fill_price: float
    ) -> None:
        pending = self._pending_entries.pop(client_order_id, None)
        if not pending:
            return
        proposal = pending.proposal
        adjusted_stop = pending.stop_price
        tick_size = pending.tick_size

        await self._place_protective_orders(
            proposal,
            fill_qty,
            adjusted_stop,
            tick_size,
        )

        await self.event_bus.publish(
            EventType.POSITION_OPENED,
            {
                "symbol": proposal.symbol,
                "side": proposal.side,
                "quantity": fill_qty,
                "entry_price": fill_price,
                "stop_price": adjusted_stop,
                "take_profit": proposal.take_profit,
                "leverage": proposal.leverage,
                "trade_id": proposal.trade_id,
            },
            {"source": "execution_engine", "trade_id": proposal.trade_id},
        )

        verified = await self._verify_protective_orders(
            proposal.symbol,
            adjusted_stop,
            proposal.take_profit,
            tick_size,
            proposal.side,
        )
        if not verified:
            await self.event_bus.publish(
                EventType.MANUAL_INTERVENTION,
                {
                    "action": "PROTECTIVE_ORDER_MISSING",
                    "symbol": proposal.symbol,
                    "stop_price": adjusted_stop,
                    "take_profit": proposal.take_profit,
                },
                {"source": "execution_engine", "trade_id": proposal.trade_id},
            )

    async def cancel_order(self, symbol: str, client_order_id: str) -> None:
        if self.simulate:
            await self.event_bus.publish(
                EventType.ORDER_CANCELLED,
                {"symbol": symbol, "client_order_id": client_order_id},
                {"source": "execution_engine"},
            )
            return
        error_text = None
        try:
            await self.rest.cancel_order(symbol, client_order_id)
        except httpx.HTTPStatusError as exc:
            error_text = exc.response.text if exc.response is not None else str(exc)
            self.log.warning("cancel_order_failed", symbol=symbol, error=error_text)
        except httpx.RequestError as exc:
            error_text = str(exc)
            self.log.warning("cancel_order_failed", symbol=symbol, error=error_text)
        payload: dict[str, Any] = {"symbol": symbol, "client_order_id": client_order_id}
        if error_text:
            payload["cancel_error"] = error_text
        await self.event_bus.publish(
            EventType.ORDER_CANCELLED, payload, {"source": "execution_engine"}
        )

    async def _cancel_order_safe(
        self, order: OrderIntent, trade_id: str | None, reason: str
    ) -> None:
        error_text = None
        if not self.simulate:
            try:
                await self.rest.cancel_order(order.symbol, order.client_order_id)
            except httpx.HTTPStatusError as exc:
                error_text = exc.response.text if exc.response is not None else str(exc)
                self.log.warning(
                    "cancel_order_failed",
                    symbol=order.symbol,
                    error=error_text,
                    status_code=getattr(exc.response, "status_code", None),
                )
            except httpx.RequestError as exc:
                error_text = str(exc)
                self.log.warning("cancel_order_failed", symbol=order.symbol, error=error_text)
        payload = {
            "symbol": order.symbol,
            "client_order_id": order.client_order_id,
            "reason": reason,
        }
        if error_text:
            payload["cancel_error"] = error_text
        await self.event_bus.publish(
            EventType.ORDER_CANCELLED,
            payload,
            {"source": "execution_engine", "trade_id": trade_id},
        )

    async def _ensure_account_settings(self, symbol: str, leverage: int) -> None:
        if self.simulate:
            return
        if not self._position_mode_set:
            dual_side = self.config.position_mode == "HEDGE"
            try:
                response = await self.rest.set_position_mode(dual_side)
                self._position_mode_set = True
                await self.event_bus.publish(
                    EventType.ACCOUNT_SETTING_UPDATED,
                    {
                        "setting": "position_mode",
                        "value": self.config.position_mode,
                        "dual_side_position": dual_side,
                        "response": response,
                    },
                    {"source": "execution_engine"},
                )
            except httpx.HTTPStatusError as exc:
                error_text = exc.response.text if exc.response is not None else str(exc)
                self.log.warning("position_mode_set_failed", error=error_text)
                if "No need to change position side" in error_text:
                    self._position_mode_set = True
                    await self.event_bus.publish(
                        EventType.ACCOUNT_SETTING_UPDATED,
                        {
                            "setting": "position_mode",
                            "value": self.config.position_mode,
                            "dual_side_position": dual_side,
                            "already_set": True,
                            "error": error_text,
                        },
                        {"source": "execution_engine"},
                    )
                else:
                    await self.event_bus.publish(
                        EventType.ACCOUNT_SETTING_FAILED,
                        {
                            "setting": "position_mode",
                            "value": self.config.position_mode,
                            "dual_side_position": dual_side,
                            "error": error_text,
                        },
                        {"source": "execution_engine"},
                    )
            except httpx.RequestError as exc:
                error_text = str(exc)
                self.log.warning("position_mode_set_failed", error=error_text)
                await self.event_bus.publish(
                    EventType.ACCOUNT_SETTING_FAILED,
                    {
                        "setting": "position_mode",
                        "value": self.config.position_mode,
                        "dual_side_position": dual_side,
                        "error": error_text,
                    },
                    {"source": "execution_engine"},
                )
        if symbol not in self._margin_type_set:
            try:
                response = await self.rest.set_margin_type(symbol, self.config.margin_type)
                await self.event_bus.publish(
                    EventType.ACCOUNT_SETTING_UPDATED,
                    {
                        "setting": "margin_type",
                        "symbol": symbol,
                        "value": self.config.margin_type,
                        "response": response,
                    },
                    {"source": "execution_engine"},
                )
            except httpx.HTTPStatusError as exc:
                error_text = exc.response.text if exc.response is not None else str(exc)
                if "No need to change margin type" not in error_text:
                    self.log.warning("margin_type_set_failed", symbol=symbol, error=error_text)
                    await self.event_bus.publish(
                        EventType.ACCOUNT_SETTING_FAILED,
                        {
                            "setting": "margin_type",
                            "symbol": symbol,
                            "value": self.config.margin_type,
                            "error": error_text,
                        },
                        {"source": "execution_engine"},
                    )
                else:
                    await self.event_bus.publish(
                        EventType.ACCOUNT_SETTING_UPDATED,
                        {
                            "setting": "margin_type",
                            "symbol": symbol,
                            "value": self.config.margin_type,
                            "already_set": True,
                            "error": error_text,
                        },
                        {"source": "execution_engine"},
                    )
            except httpx.RequestError as exc:
                error_text = str(exc)
                self.log.warning("margin_type_set_failed", symbol=symbol, error=error_text)
                await self.event_bus.publish(
                    EventType.ACCOUNT_SETTING_FAILED,
                    {
                        "setting": "margin_type",
                        "symbol": symbol,
                        "value": self.config.margin_type,
                        "error": error_text,
                    },
                    {"source": "execution_engine"},
                )
            self._margin_type_set.add(symbol)
        if self._leverage_set.get(symbol) != leverage:
            try:
                response = await self.rest.set_leverage(symbol, leverage)
                self._leverage_set[symbol] = leverage
                await self.event_bus.publish(
                    EventType.ACCOUNT_SETTING_UPDATED,
                    {
                        "setting": "leverage",
                        "symbol": symbol,
                        "value": leverage,
                        "response": response,
                    },
                    {"source": "execution_engine"},
                )
            except httpx.HTTPStatusError as exc:
                error_text = exc.response.text if exc.response is not None else str(exc)
                self.log.warning("leverage_set_failed", symbol=symbol, error=error_text)
                await self.event_bus.publish(
                    EventType.ACCOUNT_SETTING_FAILED,
                    {
                        "setting": "leverage",
                        "symbol": symbol,
                        "value": leverage,
                        "error": error_text,
                    },
                    {"source": "execution_engine"},
                )
            except httpx.RequestError as exc:
                error_text = str(exc)
                self.log.warning("leverage_set_failed", symbol=symbol, error=error_text)
                await self.event_bus.publish(
                    EventType.ACCOUNT_SETTING_FAILED,
                    {
                        "setting": "leverage",
                        "symbol": symbol,
                        "value": leverage,
                        "error": error_text,
                    },
                    {"source": "execution_engine"},
                )

    async def _verify_protective_orders(
        self,
        symbol: str,
        stop_price: float | None,
        take_profit: float | None,
        tick_size: float,
        side: str,
    ) -> bool:
        if self.simulate:
            return True
        expected_stop = None
        expected_tp = None
        if stop_price is not None:
            expected_stop = self._round_price(stop_price, tick_size, round_up=(side == "SHORT"))
        if take_profit is not None:
            expected_tp = self._round_price(take_profit, tick_size, round_up=(side == "LONG"))
        if expected_stop is None and expected_tp is None:
            return True

        for _ in range(3):
            try:
                orders = await self.rest.get_open_orders(symbol)
            except httpx.HTTPStatusError as exc:
                error_text = exc.response.text if exc.response is not None else str(exc)
                self.log.warning("protective_orders_fetch_failed", symbol=symbol, error=error_text)
                await asyncio.sleep(2)
                continue
            except httpx.RequestError as exc:
                error_text = str(exc)
                self.log.warning("protective_orders_fetch_failed", symbol=symbol, error=error_text)
                await asyncio.sleep(2)
                continue

            found_stop = expected_stop is None
            found_tp = expected_tp is None
            for order in orders:
                if not order.get("reduceOnly"):
                    continue
                order_type = order.get("type")
                stop_val = float(order.get("stopPrice", 0) or 0)
                if expected_stop is not None and order_type == "STOP_MARKET":
                    if abs(stop_val - expected_stop) <= tick_size * 1.1:
                        found_stop = True
                if expected_tp is not None and order_type == "TAKE_PROFIT_MARKET":
                    if abs(stop_val - expected_tp) <= tick_size * 1.1:
                        found_tp = True
            if found_stop and found_tp:
                return True
            await asyncio.sleep(2)
        return False

    async def _await_order_fill(
        self,
        order: OrderIntent,
        proposal: TradeProposal | None = None,
    ) -> dict[str, Any] | None:
        deadline = self._compute_entry_deadline()
        last_status: dict[str, Any] | None = None
        while time.time() < deadline:
            try:
                status = await self.rest.get_order(
                    order.symbol, client_order_id=order.client_order_id
                )
            except httpx.HTTPStatusError as exc:
                error_text = exc.response.text if exc.response is not None else str(exc)
                self.log.warning(
                    "order_status_failed",
                    symbol=order.symbol,
                    error=error_text,
                    status_code=getattr(exc.response, "status_code", None),
                )
                await asyncio.sleep(2)
                continue
            except httpx.RequestError as exc:
                error_text = str(exc)
                self.log.warning("order_status_failed", symbol=order.symbol, error=error_text)
                await asyncio.sleep(2)
                continue
            last_status = status
            state = status.get("status")
            if state in {"FILLED", "CANCELED", "REJECTED", "EXPIRED"}:
                return status
            await asyncio.sleep(2)

        # Deadline reached - handle expiration (only for entry orders with proposal)
        if proposal is not None:
            self.log.info(
                "order_deadline_reached",
                symbol=order.symbol,
                client_order_id=order.client_order_id,
                timeout_mode=self.config.entry_timeout_mode,
                trade_id=proposal.trade_id,
            )
            # Get attempt count from pending entry
            pending = self._pending_entries.get(order.client_order_id)
            attempt_count = pending.attempt_count if pending else 1

            # Cancel the order and handle expiration
            await self._cancel_order_safe(order, proposal.trade_id, "DEADLINE_REACHED")
            await self._handle_order_expired(order, proposal, attempt_count)

            # Remove from pending entries
            self._pending_entries.pop(order.client_order_id, None)
            if self._state_store:
                self._state_store.remove(order.client_order_id)

        return {"status": "EXPIRED", "clientOrderId": order.client_order_id}

    async def _place_order(self, order: OrderIntent, trade_id: str | None) -> None:
        payload = {
            "symbol": order.symbol,
            "side": order.side,
            "type": order.order_type,
            "quantity": order.quantity,
            "newClientOrderId": order.client_order_id,
            "reduceOnly": "true" if order.reduce_only else "false",
        }
        if order.price is not None:
            payload["price"] = order.price
            payload["timeInForce"] = "GTC"
        if order.stop_price is not None:
            payload["stopPrice"] = order.stop_price

        # Build ORDER_PLACED event payload
        order_event_payload: dict[str, Any] = {
            "symbol": order.symbol,
            "side": order.side,
            "quantity": order.quantity,
            "price": order.price,
            "order_type": order.order_type,
            "client_order_id": order.client_order_id,
            "stop_price": order.stop_price,
            "reduce_only": order.reduce_only,
        }

        # Include spread data for entry orders (non-reduce_only)
        if not order.reduce_only and self._last_spread_data:
            order_event_payload.update(self._last_spread_data)

        if self.simulate:
            order_event_payload["order_id"] = f"SIM-{uuid4()}"
            await self.event_bus.publish(
                EventType.ORDER_PLACED,
                order_event_payload,
                {"source": "execution_engine", "trade_id": trade_id},
            )
            return

        response = await self.rest.place_order(payload)
        order_event_payload["order_id"] = response.get("orderId")
        await self.event_bus.publish(
            EventType.ORDER_PLACED,
            order_event_payload,
            {"source": "execution_engine", "trade_id": trade_id},
        )

    async def _place_protective_orders(
        self,
        proposal: TradeProposal,
        quantity: float,
        stop_price: float | None,
        tick_size: float,
    ) -> None:
        if stop_price is None:
            return
        side = "SELL" if proposal.side == "LONG" else "BUY"
        # Place initial stop loss
        rounded_stop = self._round_price(
            stop_price,
            tick_size,
            round_up=(proposal.side == "SHORT"),
        )
        stop_order = OrderIntent(
            symbol=proposal.symbol,
            side=side,
            order_type="STOP_MARKET",
            quantity=quantity,
            price=None,
            stop_price=rounded_stop,
            reduce_only=True,
            client_order_id=self._client_order_id(proposal.symbol, f"SL-{proposal.side}"),
        )
        await self._place_order(stop_order, proposal.trade_id)

        # Place partial take profit at 2*ATR (25% of position)
        # The remaining 75% is governed by trailing stop
        if proposal.atr and proposal.atr > 0:
            partial_qty = round(quantity * 0.25, 8)  # 25% of position
            if partial_qty > 0:
                tp_distance = 2.0 * proposal.atr  # Conservative target at 2*ATR
                tp_price = (
                    proposal.entry_price + tp_distance
                    if proposal.side == "LONG"
                    else proposal.entry_price - tp_distance
                )
                rounded_tp = self._round_price(
                    tp_price,
                    tick_size,
                    round_up=(proposal.side == "LONG"),
                )
                tp_order = OrderIntent(
                    symbol=proposal.symbol,
                    side=side,
                    order_type="TAKE_PROFIT_MARKET",
                    quantity=partial_qty,
                    price=None,
                    stop_price=rounded_tp,
                    reduce_only=True,
                    client_order_id=self._client_order_id(
                        proposal.symbol, f"TP-PARTIAL-{proposal.side}"
                    ),
                )
                await self._place_order(tp_order, proposal.trade_id)

    async def update_trailing_stop(
        self,
        position: Position,
        current_price: float,
        atr: float,
        tick_size: float,
    ) -> bool:
        """
        Update trailing stop for an open position.

        Args:
            position: The open position
            current_price: Current market price
            atr: Average True Range for distance calculations
            tick_size: Price tick size for rounding

        Returns:
            True if stop was updated successfully, False otherwise
        """
        trailing_start = self.settings.strategy.exit.trailing_start_atr * atr
        trailing_distance = self.settings.strategy.exit.trailing_distance_atr * atr

        # Calculate current unrealized profit in ATR terms
        if position.side == "LONG":
            unrealized_profit = current_price - position.entry_price
            high_water_mark = (
                position.stop_price + (trailing_start + trailing_distance)
                if position.stop_price
                else current_price
            )
            # Start trailing only after price has moved trailing_start in favor
            if unrealized_profit < trailing_start:
                return False  # Not yet at trailing start threshold
            # New stop should trail trailing_distance behind the high
            new_stop = current_price - trailing_distance
        else:  # SHORT
            unrealized_profit = position.entry_price - current_price
            high_water_mark = (
                position.stop_price - (trailing_start + trailing_distance)
                if position.stop_price
                else current_price
            )
            if unrealized_profit < trailing_start:
                return False  # Not yet at trailing start threshold
            new_stop = current_price + trailing_distance

        # Don't widen the stop - only move it in favor
        if position.side == "LONG" and position.stop_price and new_stop <= position.stop_price:
            return False  # Stop would not improve
        if position.side == "SHORT" and position.stop_price and new_stop >= position.stop_price:
            return False  # Stop would not improve

        # Round the stop price
        side = "SELL" if position.side == "LONG" else "BUY"
        rounded_stop = self._round_price(
            new_stop,
            tick_size,
            round_up=(position.side == "SHORT"),
        )

        # Find the existing stop order
        existing_order_id = None
        state = None
        # Try to get state from event bus (this is a bit hacky, but needed for order lookup)
        # In a cleaner design, we'd have direct access to state
        open_orders = []  # Will be populated from state if available

        # For now, place a new stop order with updated price
        # The old stop will be orphaned but that's okay - Binance will execute the first one triggered
        # A more sophisticated implementation would cancel the old order first

        new_stop_order = OrderIntent(
            symbol=position.symbol,
            side=side,
            order_type="STOP_MARKET",
            quantity=position.quantity,
            price=None,
            stop_price=rounded_stop,
            reduce_only=True,
            client_order_id=self._client_order_id(position.symbol, f"SL-TRAIL-{position.side}"),
        )

        try:
            await self._place_order(new_stop_order, position.trade_id)
            self.log.info(
                "trailing_stop_updated",
                symbol=position.symbol,
                side=position.side,
                old_stop=position.stop_price,
                new_stop=rounded_stop,
                current_price=current_price,
                trade_id=position.trade_id,
            )
            return True
        except Exception as exc:
            error_text = str(exc)
            response = getattr(exc, "response", None)
            if response is not None:
                error_text = getattr(response, "text", error_text)
            self.log.error(
                "trailing_stop_update_failed",
                symbol=position.symbol,
                error=error_text,
                trade_id=position.trade_id,
            )
            # Safety: publish manual intervention
            await self.event_bus.publish(
                EventType.MANUAL_INTERVENTION,
                {
                    "action": "TRAILING_STOP_UPDATE_FAILED",
                    "symbol": position.symbol,
                    "error": error_text,
                    "attempted_stop": rounded_stop,
                },
                {"source": "execution_engine", "trade_id": position.trade_id},
            )
            return False

    async def verify_protective_orders(self, state: TradingState) -> None:
        """
        Verify that all open positions have protective orders on-exchange.

        If orders are missing and auto_recover is enabled, replace them.
        Emits events: PROTECTIVE_ORDERS_VERIFIED, PROTECTIVE_ORDERS_MISSING, PROTECTIVE_ORDERS_REPLACED
        """
        if not self.settings.watchdog.enabled:
            return

        for symbol, position in state.positions.items():
            try:
                exchange_orders = await self.rest.get_open_orders(symbol)
                exchange_client_ids = {order.get("clientOrderId", "") for order in exchange_orders}

                # Check for expected protective order patterns
                # Patterns: T_{symbol}_SL-{LONG|SHORT}_*, T_{symbol}_SL-TRAIL-{LONG|SHORT}_*
                side_pattern = position.side  # LONG or SHORT
                has_stop_loss = any(
                    f"SL-{side_pattern}" in cid or f"SL-TRAIL-{side_pattern}" in cid
                    for cid in exchange_client_ids
                )
                has_take_profit = any(
                    f"TP-PARTIAL-{side_pattern}" in cid for cid in exchange_client_ids
                )

                missing_types = []
                if not has_stop_loss:
                    missing_types.append("STOP_LOSS")
                if not has_take_profit:
                    missing_types.append("TAKE_PROFIT")

                if missing_types:
                    # Orders are missing
                    self.log.warning(
                        "protective_orders_missing",
                        symbol=symbol,
                        missing_types=missing_types,
                        auto_recover=self.settings.watchdog.auto_recover,
                    )
                    await self.event_bus.publish(
                        EventType.PROTECTIVE_ORDERS_MISSING,
                        {
                            "symbol": symbol,
                            "missing_types": missing_types,
                            "auto_recover": self.settings.watchdog.auto_recover,
                        },
                        {"source": "watchdog"},
                    )

                    if self.settings.watchdog.auto_recover:
                        # Auto-replace missing orders
                        replaced_types, order_ids = await self._replace_protective_orders(position)
                        if replaced_types:
                            self.log.info(
                                "protective_orders_replaced",
                                symbol=symbol,
                                replaced_types=replaced_types,
                                order_ids=order_ids,
                            )
                            await self.event_bus.publish(
                                EventType.PROTECTIVE_ORDERS_REPLACED,
                                {
                                    "symbol": symbol,
                                    "replaced_types": replaced_types,
                                    "order_ids": order_ids,
                                },
                                {"source": "watchdog"},
                            )
                else:
                    # All protective orders are present
                    await self.event_bus.publish(
                        EventType.PROTECTIVE_ORDERS_VERIFIED,
                        {"symbol": symbol, "has_sl": True, "has_tp": has_take_profit},
                        {"source": "watchdog"},
                    )

            except Exception as exc:
                self.log.error(
                    "protective_orders_check_failed",
                    symbol=symbol,
                    error=str(exc),
                )

    async def _replace_protective_orders(self, position: Position) -> tuple[list[str], list[str]]:
        """
        Replace missing protective orders for a position.

        Returns:
            tuple of (replaced_types: list[str], order_ids: list[str])
        """
        replaced_types: list[str] = []
        order_ids: list[str] = []

        # Get symbol filters for tick size
        symbol_filters = await self._get_symbol_filters(position.symbol)
        tick_size = symbol_filters.tick_size if symbol_filters else 0.0001

        # Re-create stop loss order
        if position.stop_price:
            side: Literal["BUY", "SELL"] = "SELL" if position.side == "LONG" else "BUY"
            rounded_stop = self._round_price(
                position.stop_price,
                tick_size,
                round_up=(position.side == "SHORT"),
            )
            sl_order = OrderIntent(
                symbol=position.symbol,
                side=side,
                order_type="STOP_MARKET",
                quantity=position.quantity,
                price=None,
                stop_price=rounded_stop,
                reduce_only=True,
                client_order_id=self._client_order_id(position.symbol, f"SL-{position.side}"),
            )
            try:
                result = await self._place_order(sl_order, position.trade_id or "")
                if result:
                    order_ids.append(str(result.get("orderId", "")))
                replaced_types.append("STOP_LOSS")
            except Exception as exc:
                self.log.error(
                    "protective_order_replacement_failed",
                    symbol=position.symbol,
                    order_type="STOP_LOSS",
                    error=str(exc),
                )

        return replaced_types, order_ids

    async def _get_symbol_filters(self, symbol: str) -> SymbolFilters | None:
        """Get symbol filters for a given symbol."""
        from src.risk.sizing import SymbolFilters

        try:
            exchange_info = await self.rest.get_exchange_info()
            for s in exchange_info.get("symbols", []):
                if s.get("symbol") == symbol:
                    filters = s.get("filters", [])
                    tick_size = 0.0001
                    min_qty = 0.0
                    step_size = 0.0
                    min_notional = 0.0
                    for f in filters:
                        if f.get("filterType") == "PRICE_FILTER":
                            tick_size = float(f.get("tickSize", 0.0001))
                        elif f.get("filterType") == "LOT_SIZE":
                            min_qty = float(f.get("minQty", 0.0))
                            step_size = float(f.get("stepSize", 0.0))
                        elif f.get("filterType") == "MIN_NOTIONAL":
                            min_notional = float(f.get("notional", 0.0))
                    return SymbolFilters(
                        tick_size=tick_size,
                        min_qty=min_qty,
                        step_size=step_size,
                        min_notional=min_notional,
                    )
        except Exception as exc:
            self.log.warning("symbol_filters_fetch_failed", symbol=symbol, error=str(exc))
        return None

    def _client_order_id(self, symbol: str, side: str) -> str:
        side_tag = side[0].upper() if side else "X"
        timestamp = str(int(time.time() * 1000))[-10:]
        nonce = uuid4().hex[:4]
        client_id = f"T_{symbol}_{side_tag}_{timestamp}_{nonce}"
        return client_id[:36]

    async def _check_spread_slippage(
        self,
        proposal: TradeProposal,
        expected_price: float,
        atr: float | None = None,
    ) -> tuple[bool, str | None]:
        """
        Check if market conditions are acceptable for execution.

        Args:
            proposal: The trade proposal being evaluated
            expected_price: Expected entry price
            atr: ATR value in price units (optional, used for dynamic threshold)

        Returns:
            tuple of (is_acceptable: bool, rejection_reason: str | None)
        """
        # Reset spread data
        self._last_spread_data = None

        # Skip checks in simulation mode
        if self.simulate:
            return True, None

        try:
            ticker = await self.rest.get_book_ticker(proposal.symbol)
        except (httpx.HTTPStatusError, httpx.RequestError, ValueError) as exc:
            self.log.warning("book_ticker_fetch_failed", symbol=proposal.symbol, error=str(exc))
            # Allow execution if we can't fetch ticker (fail open for safety)
            return True, None

        best_bid = float(ticker["bidPrice"])
        best_ask = float(ticker["askPrice"])

        # Calculate spread %
        mid_price = (best_bid + best_ask) / 2
        spread_pct = ((best_ask - best_bid) / mid_price) * 100

        # Store spread data for ORDER_PLACED event
        self._last_spread_data = {
            "spread_at_entry_pct": round(spread_pct, 4),
            "bid": best_bid,
            "ask": best_ask,
        }

        # Determine spread threshold
        if self.config.use_dynamic_spread_threshold and atr is not None and expected_price > 0:
            # Convert ATR to percentage of price
            atr_pct = (atr / expected_price) * 100

            # Select threshold based on market volatility
            if atr_pct < self.config.atr_calm_threshold:
                max_spread_pct = self.config.spread_threshold_calm_pct
                market_regime = "calm"
            elif atr_pct > self.config.atr_volatile_threshold:
                max_spread_pct = self.config.spread_threshold_volatile_pct
                market_regime = "volatile"
            else:
                max_spread_pct = self.config.spread_threshold_normal_pct
                market_regime = "normal"

            self._last_spread_data["atr_pct"] = round(atr_pct, 4)
            self._last_spread_data["market_regime"] = market_regime
            self._last_spread_data["dynamic_threshold_pct"] = max_spread_pct
        else:
            # Use fixed threshold
            max_spread_pct = self.config.max_spread_pct
            self._last_spread_data["dynamic_threshold_pct"] = max_spread_pct

        # Record spread metric
        if self._metrics is not None:
            self._metrics.trade_spread_pct.observe(spread_pct)

        # Check spread constraint
        if spread_pct > max_spread_pct:
            reason = f"SPREAD_TOO_WIDE: {spread_pct:.3f}% > {max_spread_pct:.3f}%"
            self.log.warning(
                "spread_too_wide",
                symbol=proposal.symbol,
                spread_pct=spread_pct,
                max_spread_pct=max_spread_pct,
                bid=best_bid,
                ask=best_ask,
                trade_id=proposal.trade_id,
                atr_pct=self._last_spread_data.get("atr_pct"),
                market_regime=self._last_spread_data.get("market_regime"),
            )
            # Record rejection metric
            if self._metrics is not None:
                self._metrics.spread_rejections_total.inc()
            return False, reason

        # Check slippage constraint (deviation from expected entry to current mark price)
        mark_price = float(ticker.get("markPrice", mid_price))
        slippage_pct = abs((mark_price - expected_price) / expected_price) * 100

        if slippage_pct > self.config.max_slippage_pct:
            reason = f"SLIPPAGE_TOO_HIGH: {slippage_pct:.3f}% > {self.config.max_slippage_pct:.3f}%"
            self.log.warning(
                "slippage_too_high",
                symbol=proposal.symbol,
                slippage_pct=slippage_pct,
                max_slippage_pct=self.config.max_slippage_pct,
                expected_price=expected_price,
                mark_price=mark_price,
                trade_id=proposal.trade_id,
            )
            return False, reason

        return True, None

    def _limit_price(self, entry_price: float, side: str, tick_size: float) -> float:
        buffer_pct = self.config.limit_order_buffer_pct / 100
        if side == "LONG":
            raw = entry_price * (1 - buffer_pct)
            return self._round_price(raw, tick_size, round_up=False)
        raw = entry_price * (1 + buffer_pct)
        return self._round_price(raw, tick_size, round_up=True)

    @staticmethod
    def _round_price(value: float, tick_size: float, round_up: bool) -> float:
        if tick_size <= 0:
            return value
        quant = Decimal(str(tick_size))
        rounding = ROUND_UP if round_up else ROUND_DOWN
        rounded = (Decimal(str(value)) / quant).to_integral_value(rounding=rounding) * quant
        exponent = quant.as_tuple().exponent
        # Handle special Decimal values (NaN, Inf) which return string exponents
        precision = abs(exponent) if isinstance(exponent, int) else 0
        return float(round(rounded, precision))

    def _serialize_entry(self, entry: PendingEntry) -> dict[str, Any]:
        """Serialize PendingEntry for persistence."""
        return {
            "trade_id": entry.proposal.trade_id,
            "symbol": entry.proposal.symbol,
            "side": entry.proposal.side,
            "stop_price": entry.stop_price,
            "tick_size": entry.tick_size,
            "entry_price": entry.proposal.entry_price,
            "take_profit": entry.proposal.take_profit,
            "atr": entry.proposal.atr,
            "leverage": entry.proposal.leverage,
            "lifecycle_state": entry.lifecycle_state,
            "candle_timestamp": entry.candle_timestamp.isoformat()
            if entry.candle_timestamp
            else None,
            "attempt_count": entry.attempt_count,
            "original_client_order_id": entry.original_client_order_id,
        }

    def _deserialize_entry(self, context: dict[str, Any]) -> PendingEntry | None:
        """Deserialize persisted context back to PendingEntry."""
        try:
            # Import here to avoid circular imports
            from src.models import TradeProposal

            # Handle candle_timestamp deserialization
            candle_ts = None
            if context.get("candle_timestamp"):
                if isinstance(context["candle_timestamp"], str):
                    candle_ts = datetime.fromisoformat(context["candle_timestamp"])
                else:
                    candle_ts = context["candle_timestamp"]

            proposal = TradeProposal(
                symbol=context["symbol"],
                side=context["side"],
                entry_price=context["entry_price"],
                stop_price=context.get("stop_price") or context["entry_price"],
                take_profit=context.get("take_profit"),
                atr=context.get("atr", 0) or 0,
                leverage=context.get("leverage", 1) or 1,
                score=None,  # Score not needed for protective orders
                funding_rate=0,
                news_risk="LOW",
                trade_id=context.get("trade_id") or "",
                created_at=datetime.now(timezone.utc),
                candle_timestamp=candle_ts,
            )
            return PendingEntry(
                proposal=proposal,
                stop_price=context.get("stop_price"),
                tick_size=context.get("tick_size", 0.0001),
                lifecycle_state=context.get("lifecycle_state", "OPEN"),
                candle_timestamp=candle_ts,
                attempt_count=context.get("attempt_count", 1),
                original_client_order_id=context.get("original_client_order_id"),
            )
        except Exception:
            return None

    # === Order Lifecycle Methods ===

    def _find_pending_for_candle(
        self, symbol: str, candle_ts: datetime | None
    ) -> PendingEntry | None:
        """Find pending entry for a specific candle."""
        if candle_ts is None:
            return None
        for entry in self._pending_entries.values():
            if entry.proposal.symbol == symbol and entry.candle_timestamp == candle_ts:
                return entry
        return None

    def has_pending_for_candle(self, symbol: str, candle_ts: datetime | None) -> bool:
        """Check if there's a pending order for this candle."""
        return self._find_pending_for_candle(symbol, candle_ts) is not None

    def get_pending_for_symbol(self, symbol: str) -> list[PendingEntry]:
        """Get all pending entries for a symbol."""
        return [e for e in self._pending_entries.values() if e.proposal.symbol == symbol]

    def _compute_entry_deadline(self) -> float:
        """Compute order deadline based on timeout mode."""
        mode = self.config.entry_timeout_mode

        if mode == "unlimited":
            # No deadline, keep GTC until filled or signal invalidates
            return float("inf")
        elif mode == "timeframe":
            # Deadline = next candle open (e.g., 4 hours from now for 4h TF)
            # Convert timeframe to seconds: 4h = 4*3600 = 14400
            interval_seconds: dict[str, int] = {
                "1m": 60,
                "5m": 300,
                "15m": 900,
                "1h": 3600,
                "4h": 14400,
                "1d": 86400,
            }
            tf_seconds = interval_seconds.get(self.settings.strategy.entry_timeframe, 14400)
            return time.time() + tf_seconds
        else:  # "fixed" mode
            return time.time() + self.config.order_timeout_sec

    async def _handle_order_expired(
        self,
        order: OrderIntent,
        proposal: TradeProposal,
        attempt_count: int,
    ) -> None:
        """Handle order expiration with configurable fallback."""
        action = self.config.entry_expired_action

        if action == "cancel":
            # Just cancel and log - signal may regenerate on next candle
            await self.event_bus.publish(
                EventType.ORDER_EXPIRED,
                {
                    "symbol": order.symbol,
                    "client_order_id": order.client_order_id,
                    "reason": "TIMEOUT",
                    "attempt": attempt_count,
                    "timeout_mode": self.config.entry_timeout_mode,
                },
                {"source": "execution_engine", "trade_id": proposal.trade_id},
            )
            self.log.info(
                "order_expired",
                symbol=order.symbol,
                client_order_id=order.client_order_id,
                attempt=attempt_count,
                timeout_mode=self.config.entry_timeout_mode,
                trade_id=proposal.trade_id,
            )
        elif action == "convert_market":
            # Convert to market order for immediate execution
            self.log.info(
                "order_expired_converting_market",
                symbol=order.symbol,
                client_order_id=order.client_order_id,
                trade_id=proposal.trade_id,
            )
            market_order = OrderIntent(
                symbol=order.symbol,
                side=order.side,
                order_type="MARKET",
                quantity=order.quantity,
                price=None,
                stop_price=None,
                reduce_only=False,
                client_order_id=self._client_order_id(order.symbol, f"MKT-{order.side}"),
            )
            try:
                await self._place_order(market_order, proposal.trade_id)
            except Exception as exc:
                self.log.error(
                    "market_conversion_failed",
                    symbol=order.symbol,
                    error=str(exc),
                    trade_id=proposal.trade_id,
                )
        elif action == "convert_stop":
            # Convert to stop-market entry (good for breakout trades)
            self.log.info(
                "order_expired_converting_stop",
                symbol=order.symbol,
                client_order_id=order.client_order_id,
                trade_id=proposal.trade_id,
            )
            stop_order = OrderIntent(
                symbol=order.symbol,
                side=order.side,
                order_type="STOP_MARKET",
                quantity=order.quantity,
                price=None,
                stop_price=proposal.entry_price,
                reduce_only=False,
                client_order_id=self._client_order_id(order.symbol, f"STP-{order.side}"),
            )
            try:
                await self._place_order(stop_order, proposal.trade_id)
            except Exception as exc:
                self.log.error(
                    "stop_conversion_failed",
                    symbol=order.symbol,
                    error=str(exc),
                    trade_id=proposal.trade_id,
                )
