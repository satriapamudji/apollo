"""Order execution engine for Binance Futures."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from typing import Any, Literal
from uuid import uuid4

import httpx
import structlog

from src.config.settings import ExecutionConfig, Settings
from src.connectors.rest_client import BinanceRestClient
from src.ledger.bus import EventBus
from src.ledger.events import Event, EventType
from src.ledger.state import Position, TradingState
from src.models import TradeProposal
from src.risk.engine import RiskCheckResult, RiskEngine
from src.risk.sizing import PositionSizer, SymbolFilters


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


class ExecutionEngine:
    """Submit and monitor orders after risk approval."""

    def __init__(self, settings: Settings, rest: BinanceRestClient, event_bus: EventBus) -> None:
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

    async def execute_entry(
        self,
        proposal: TradeProposal,
        risk_result: RiskCheckResult,
        symbol_filters: SymbolFilters,
        state: TradingState,
    ) -> None:
        if not risk_result.approved or not proposal.is_entry:
            return
        if risk_result.adjusted_entry_threshold and proposal.score:
            if proposal.score.composite < risk_result.adjusted_entry_threshold:
                return

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
            return

        quantity = sizing.quantity * risk_result.size_multiplier
        if symbol_filters.step_size > 0:
            step = Decimal(str(symbol_filters.step_size))
            quantity = float(
                (Decimal(str(quantity)) / step).to_integral_value(rounding=ROUND_DOWN) * step
            )
        if quantity < symbol_filters.min_qty:
            return
        if quantity * proposal.entry_price < symbol_filters.min_notional:
            return
        if quantity <= 0:
            return
        if any(
            existing.symbol == proposal.symbol and not existing.reduce_only
            for existing in state.open_orders.values()
        ):
            return

        await self._ensure_account_settings(proposal.symbol, proposal.leverage)

        client_order_id = self._client_order_id(proposal.symbol, proposal.side)
        if client_order_id in state.open_orders:
            return

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
            return
        except httpx.RequestError as exc:
            error_text = str(exc)
            await self.event_bus.publish(
                EventType.ORDER_CANCELLED,
                {"symbol": order.symbol, "client_order_id": order.client_order_id, "reason": error_text},
                {"source": "execution_engine", "trade_id": proposal.trade_id},
            )
            self.log.warning("order_submit_failed", symbol=order.symbol, error=error_text)
            return

        self._pending_entries[order.client_order_id] = PendingEntry(
            proposal=proposal,
            stop_price=adjusted_stop,
            tick_size=symbol_filters.tick_size,
        )

        order_status = None
        full_fill = True
        if not self.simulate and order_type != "MARKET":
            order_status = await self._await_order_fill(order)
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
                    return
                else:
                    await self._cancel_order_safe(order, proposal.trade_id, status or "TIMEOUT")
                    return
            else:
                fill_qty = executed_qty or order.quantity
                fill_price = avg_price or proposal.entry_price
        else:
            fill_qty = order.quantity
            fill_price = proposal.entry_price

        if full_fill:
            await self.event_bus.publish(
                EventType.ORDER_FILLED,
                {
                    "symbol": order.symbol,
                    "side": order.side,
                    "quantity": fill_qty,
                    "client_order_id": order.client_order_id,
                    "price": fill_price,
                },
                {"source": "execution_engine", "trade_id": proposal.trade_id},
            )
        return

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
        pnl = (fill_price - position.entry_price) * fill_qty
        if position.side == "SHORT":
            pnl = -pnl

        await self.event_bus.publish(
            EventType.POSITION_CLOSED,
            {
                "symbol": position.symbol,
                "quantity": fill_qty,
                "exit_price": fill_price,
                "realized_pnl": pnl,
                "reason": reason,
                "trade_id": position.trade_id,
                "entry_price": position.entry_price,
                "side": position.side,
                "order_id": order_id,
            },
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
        await self._finalize_entry(str(client_order_id), qty, price)

    async def handle_order_cancelled(self, event: Event) -> None:
        payload = event.payload or {}
        client_order_id = payload.get("client_order_id") or payload.get("clientOrderId")
        if not client_order_id:
            return
        self._pending_entries.pop(str(client_order_id), None)

    async def _finalize_entry(self, client_order_id: str, fill_qty: float, fill_price: float) -> None:
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
        await self.event_bus.publish(EventType.ORDER_CANCELLED, payload, {"source": "execution_engine"})

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
                        {"setting": "margin_type", "symbol": symbol, "value": self.config.margin_type, "error": error_text},
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
                    {"setting": "margin_type", "symbol": symbol, "value": self.config.margin_type, "error": error_text},
                    {"source": "execution_engine"},
                )
            self._margin_type_set.add(symbol)
        if self._leverage_set.get(symbol) != leverage:
            try:
                response = await self.rest.set_leverage(symbol, leverage)
                self._leverage_set[symbol] = leverage
                await self.event_bus.publish(
                    EventType.ACCOUNT_SETTING_UPDATED,
                    {"setting": "leverage", "symbol": symbol, "value": leverage, "response": response},
                    {"source": "execution_engine"},
                )
            except httpx.HTTPStatusError as exc:
                error_text = exc.response.text if exc.response is not None else str(exc)
                self.log.warning("leverage_set_failed", symbol=symbol, error=error_text)
                await self.event_bus.publish(
                    EventType.ACCOUNT_SETTING_FAILED,
                    {"setting": "leverage", "symbol": symbol, "value": leverage, "error": error_text},
                    {"source": "execution_engine"},
                )
            except httpx.RequestError as exc:
                error_text = str(exc)
                self.log.warning("leverage_set_failed", symbol=symbol, error=error_text)
                await self.event_bus.publish(
                    EventType.ACCOUNT_SETTING_FAILED,
                    {"setting": "leverage", "symbol": symbol, "value": leverage, "error": error_text},
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
            expected_stop = self._round_price(
                stop_price, tick_size, round_up=(side == "SHORT")
            )
        if take_profit is not None:
            expected_tp = self._round_price(
                take_profit, tick_size, round_up=(side == "LONG")
            )
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

    async def _await_order_fill(self, order: OrderIntent) -> dict[str, Any] | None:
        deadline = time.time() + self.config.order_timeout_sec
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
        return last_status

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

        if self.simulate:
            await self.event_bus.publish(
                EventType.ORDER_PLACED,
                {
                    "symbol": order.symbol,
                    "side": order.side,
                    "quantity": order.quantity,
                    "price": order.price,
                    "order_type": order.order_type,
                    "client_order_id": order.client_order_id,
                    "stop_price": order.stop_price,
                    "reduce_only": order.reduce_only,
                    "order_id": f"SIM-{uuid4()}",
                },
                {"source": "execution_engine", "trade_id": trade_id},
            )
            return

        response = await self.rest.place_order(payload)
        await self.event_bus.publish(
            EventType.ORDER_PLACED,
            {
                "symbol": order.symbol,
                "side": order.side,
                "quantity": order.quantity,
                "price": order.price,
                "order_type": order.order_type,
                "client_order_id": order.client_order_id,
                "stop_price": order.stop_price,
                "reduce_only": order.reduce_only,
                "order_id": response.get("orderId"),
            },
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

        if proposal.take_profit:
            rounded_tp = self._round_price(
                proposal.take_profit,
                tick_size,
                round_up=(proposal.side == "LONG"),
            )
            tp_order = OrderIntent(
                symbol=proposal.symbol,
                side=side,
                order_type="TAKE_PROFIT_MARKET",
                quantity=quantity,
                price=None,
                stop_price=rounded_tp,
                reduce_only=True,
                client_order_id=self._client_order_id(proposal.symbol, f"TP-{proposal.side}"),
            )
            await self._place_order(tp_order, proposal.trade_id)

    def _client_order_id(self, symbol: str, side: str) -> str:
        side_tag = side[0].upper() if side else "X"
        timestamp = str(int(time.time() * 1000))[-10:]
        nonce = uuid4().hex[:4]
        client_id = f"T_{symbol}_{side_tag}_{timestamp}_{nonce}"
        return client_id[:36]

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
        precision = abs(quant.as_tuple().exponent)
        return float(round(rounded, precision))
