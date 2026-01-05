"""Binance Futures user data stream (listenKey) consumer."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import structlog

from src.config.settings import Settings
from src.connectors.rest_client import BinanceRestClient
from src.connectors.ws_client import BinanceWebSocketClient
from src.ledger.bus import EventBus
from src.ledger.events import EventType
from src.ledger.state import StateManager


class UserDataStream:
    """
    Consume Binance Futures user data stream and translate updates into ledger events.

    - Starts/keeps alive listenKey
    - Connects to WS and publishes ORDER_* events
    - Emits POSITION_* events for reduce-only fills (e.g., SL/TP) so trades get logged
    """

    def __init__(
        self,
        settings: Settings,
        rest: BinanceRestClient,
        event_bus: EventBus,
        state_manager: StateManager,
    ) -> None:
        self.settings = settings
        self.rest = rest
        self.event_bus = event_bus
        self.state_manager = state_manager
        self.log = structlog.get_logger(__name__)

    async def run(self) -> None:
        if self.settings.run.mode == "paper":
            self.log.info("user_stream_disabled", reason="run_mode_paper")
            return
        if not self.settings.binance.user_stream_enabled:
            self.log.info("user_stream_disabled", reason="config_disabled")
            return
        if not self.settings.active_binance_api_key:
            self.log.info("user_stream_disabled", reason="missing_api_key")
            return

        keepalive_sec = self.settings.binance.user_stream_keepalive_sec
        while True:
            listen_key = ""
            try:
                listen_key = await self.rest.start_listen_key()
            except Exception as exc:
                self.log.warning("listen_key_create_failed", error=str(exc))
                await asyncio.sleep(5)
                continue
            if not listen_key:
                self.log.warning("listen_key_empty")
                await asyncio.sleep(5)
                continue

            ws_client = BinanceWebSocketClient(self.settings)
            expired = asyncio.Event()

            async def handler(payload: dict[str, Any]) -> None:
                try:
                    data = payload.get("data", payload)
                    event_type = data.get("e")
                    if event_type == "listenKeyExpired":
                        self.log.warning("listen_key_expired")
                        expired.set()
                        ws_client.stop()
                        return
                    if event_type == "ORDER_TRADE_UPDATE":
                        await self._handle_order_trade_update(data)
                        return
                    if event_type == "ACCOUNT_UPDATE":
                        await self._handle_account_update(data)
                        return
                except Exception:
                    self.log.exception("user_stream_handler_failed")

            self.log.info(
                "user_stream_starting",
                ws_url=self.settings.binance_ws_url,
                listen_key=self._mask_listen_key(listen_key),
            )
            keepalive_task = asyncio.create_task(self._keepalive_loop(listen_key, keepalive_sec, expired))
            try:
                await ws_client.run([listen_key], handler)
            finally:
                expired.set()
                keepalive_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await keepalive_task

            await asyncio.sleep(1 if expired.is_set() else 5)

    async def _keepalive_loop(self, listen_key: str, keepalive_sec: int, stop: asyncio.Event) -> None:
        while not stop.is_set():
            await asyncio.sleep(keepalive_sec)
            try:
                await self.rest.keepalive_listen_key(listen_key)
                self.log.info("listen_key_keepalive_ok")
            except Exception as exc:
                self.log.warning("listen_key_keepalive_failed", error=str(exc))

    async def _handle_order_trade_update(self, data: dict[str, Any]) -> None:
        order: dict[str, Any] = data.get("o") or {}
        symbol = str(order.get("s") or "")
        client_order_id = str(order.get("c") or "")
        side = str(order.get("S") or "")
        order_type = str(order.get("o") or "")
        status = str(order.get("X") or "")
        exec_type = str(order.get("x") or "")
        order_id = order.get("i")

        reduce_only = bool(order.get("R", False))
        stop_price = self._as_float(order.get("sp"))

        cumulative_qty = self._as_float(order.get("z"))
        last_qty = self._as_float(order.get("l"))
        avg_price = self._as_float(order.get("ap"))
        last_price = self._as_float(order.get("L"))
        realized_pnl = self._as_float(order.get("rp"))

        price = avg_price if avg_price > 0 else last_price
        if status == "PARTIALLY_FILLED" and last_qty > 0:
            await self.event_bus.publish(
                EventType.ORDER_PARTIAL_FILL,
                {
                    "symbol": symbol,
                    "side": side,
                    "quantity": last_qty,
                    "price": last_price if last_price > 0 else price,
                    "executed_qty": cumulative_qty,
                    "client_order_id": client_order_id,
                    "order_id": order_id,
                    "order_type": order_type,
                    "stop_price": stop_price or None,
                    "reduce_only": reduce_only,
                    "status": status,
                    "exec_type": exec_type,
                },
                {"source": "user_stream"},
            )
            return

        if status == "FILLED" and cumulative_qty > 0:
            await self.event_bus.publish(
                EventType.ORDER_FILLED,
                {
                    "symbol": symbol,
                    "side": side,
                    "quantity": cumulative_qty,
                    "price": price,
                    "client_order_id": client_order_id,
                    "order_id": order_id,
                    "order_type": order_type,
                    "stop_price": stop_price or None,
                    "reduce_only": reduce_only,
                    "status": status,
                    "exec_type": exec_type,
                },
                {"source": "user_stream"},
            )
            if reduce_only and not self._is_bot_exit_order(client_order_id):
                await self._handle_reduce_only_fill(
                    symbol=symbol,
                    filled_qty=cumulative_qty,
                    exit_price=price,
                    order_type=order_type,
                    realized_pnl=realized_pnl,
                )
            return

        if status in {"CANCELED", "EXPIRED", "REJECTED"}:
            await self.event_bus.publish(
                EventType.ORDER_CANCELLED,
                {
                    "symbol": symbol,
                    "client_order_id": client_order_id,
                    "order_id": order_id,
                    "order_type": order_type,
                    "reduce_only": reduce_only,
                    "status": status,
                    "exec_type": exec_type,
                    "reason": f"{exec_type}/{status}",
                },
                {"source": "user_stream"},
            )

    async def _handle_reduce_only_fill(
        self,
        symbol: str,
        filled_qty: float,
        exit_price: float,
        order_type: str,
        realized_pnl: float,
    ) -> None:
        position = self.state_manager.state.positions.get(symbol)
        if not position:
            return
        if filled_qty <= 0:
            return

        epsilon = 1e-8
        remaining_qty = max(0.0, position.quantity - filled_qty)
        pnl = realized_pnl
        if abs(pnl) <= epsilon:
            pnl = (exit_price - position.entry_price) * filled_qty
            if position.side == "SHORT":
                pnl = -pnl

        reason = {
            "STOP_MARKET": "STOP_LOSS",
            "TAKE_PROFIT_MARKET": "TAKE_PROFIT",
        }.get(order_type, "REDUCE_ONLY_EXIT")

        metadata: dict[str, Any] = {"source": "user_stream"}
        if position.trade_id:
            metadata["trade_id"] = position.trade_id

        if remaining_qty <= epsilon:
            await self.event_bus.publish(
                EventType.POSITION_CLOSED,
                {
                    "symbol": symbol,
                    "quantity": filled_qty,
                    "exit_price": exit_price,
                    "realized_pnl": pnl,
                    "reason": reason,
                    "trade_id": position.trade_id,
                    "entry_price": position.entry_price,
                    "side": position.side,
                },
                metadata,
            )
            return

        await self.event_bus.publish(
            EventType.POSITION_UPDATED,
            {
                "symbol": symbol,
                "quantity": remaining_qty,
            },
            metadata,
        )

    async def _handle_account_update(self, data: dict[str, Any]) -> None:
        account: dict[str, Any] = data.get("a") or {}
        positions = account.get("P") or []
        for pos in positions:
            try:
                symbol = str(pos.get("s") or "")
                amt = self._as_float(pos.get("pa"))
                if not symbol or abs(amt) <= 0:
                    continue
                existing = self.state_manager.state.positions.get(symbol)
                if not existing:
                    continue
                await self.event_bus.publish(
                    EventType.POSITION_UPDATED,
                    {
                        "symbol": symbol,
                        "quantity": abs(amt),
                        "unrealized_pnl": self._as_float(pos.get("up")),
                    },
                    {"source": "user_stream", "trade_id": existing.trade_id or ""},
                )
            except Exception:
                self.log.exception("account_update_parse_failed")

    @staticmethod
    def _as_float(value: Any) -> float:
        if value is None:
            return 0.0
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _mask_listen_key(listen_key: str) -> str:
        if not listen_key:
            return ""
        if len(listen_key) <= 8:
            return "<redacted>"
        return f"{listen_key[:4]}...{listen_key[-4:]}"

    @staticmethod
    def _is_bot_exit_order(client_order_id: str) -> bool:
        if not client_order_id:
            return False
        return client_order_id.startswith("T_") and "_E_" in client_order_id
