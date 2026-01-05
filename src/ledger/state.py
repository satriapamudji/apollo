"""State reconstruction and reconciliation logic."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from src.config.settings import NewsConfig, RiskConfig
from src.ledger.events import Event, EventType, utc_now


NewsRiskLevel = Literal["HIGH", "MEDIUM", "LOW"]
PositionSide = Literal["LONG", "SHORT"]


@dataclass
class NewsRisk:
    """Track news risk with decay."""

    symbol: str
    level: NewsRiskLevel
    reason: str | None
    confidence: float
    last_updated: datetime

    def level_with_decay(
        self,
        high_block_hours: float,
        medium_block_hours: float,
        now: datetime | None = None,
    ) -> NewsRiskLevel:
        now = now or utc_now()
        elapsed = now - self.last_updated
        if self.level == "HIGH":
            if elapsed >= timedelta(hours=high_block_hours):
                return "LOW"
            if elapsed >= timedelta(hours=high_block_hours / 2):
                return "MEDIUM"
            return "HIGH"
        if self.level == "MEDIUM":
            if elapsed >= timedelta(hours=medium_block_hours):
                return "LOW"
            return "MEDIUM"
        return "LOW"

    def blocks_entries(self, high_block_hours: float, now: datetime | None = None) -> bool:
        now = now or utc_now()
        if self.level != "HIGH":
            return False
        return now - self.last_updated <= timedelta(hours=high_block_hours)


@dataclass
class Position:
    symbol: str
    side: PositionSide
    quantity: float
    entry_price: float
    leverage: int
    opened_at: datetime
    stop_price: float | None = None
    take_profit: float | None = None
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    last_update: datetime | None = None
    trade_id: str | None = None


@dataclass
class Order:
    client_order_id: str
    symbol: str
    side: str
    order_type: str
    quantity: float
    price: float | None
    stop_price: float | None
    reduce_only: bool
    status: str
    created_at: datetime
    order_id: str | None = None


@dataclass
class TradingState:
    positions: dict[str, Position] = field(default_factory=dict)
    open_orders: dict[str, Order] = field(default_factory=dict)
    equity: float = 100.0
    realized_pnl_today: float = 0.0
    daily_loss: float = 0.0
    daily_loss_date: datetime | None = None
    peak_equity: float = 100.0
    consecutive_losses: int = 0
    loss_timestamps: list[datetime] = field(default_factory=list)
    last_loss_time: datetime | None = None
    cooldown_until: datetime | None = None
    max_daily_loss_time: datetime | None = None
    news_risk_flags: dict[str, NewsRisk] = field(default_factory=dict)
    circuit_breaker_active: bool = False
    requires_manual_review: bool = False
    last_reconciliation: datetime | None = None
    universe: list[str] = field(default_factory=list)
    last_event_sequence: int = 0


class StateManager:
    """Rebuilds and updates state using events."""

    def __init__(
        self,
        initial_equity: float = 100.0,
        risk_config: RiskConfig | None = None,
        news_config: NewsConfig | None = None,
    ) -> None:
        self.state = TradingState(equity=initial_equity, peak_equity=initial_equity)
        self.risk_config = risk_config or RiskConfig()
        self.news_config = news_config or NewsConfig()

    def rebuild(self, events: list[Event]) -> TradingState:
        self.state = TradingState(
            equity=self.state.equity,
            peak_equity=self.state.peak_equity,
        )
        for event in events:
            self.apply_event(event)
        return self.state

    def apply_event(self, event: Event) -> None:
        self.state.last_event_sequence = max(self.state.last_event_sequence, event.sequence_num)
        handler = {
            EventType.NEWS_CLASSIFIED: self._handle_news_classified,
            EventType.UNIVERSE_UPDATED: self._handle_universe_updated,
            EventType.ORDER_PLACED: self._handle_order_placed,
            EventType.ORDER_CANCELLED: self._handle_order_cancelled,
            EventType.ORDER_FILLED: self._handle_order_filled,
            EventType.ORDER_PARTIAL_FILL: self._handle_order_partial_fill,
            EventType.POSITION_OPENED: self._handle_position_opened,
            EventType.POSITION_UPDATED: self._handle_position_updated,
            EventType.POSITION_CLOSED: self._handle_position_closed,
            EventType.CIRCUIT_BREAKER_TRIGGERED: self._handle_circuit_breaker,
            EventType.MANUAL_INTERVENTION: self._handle_manual_intervention,
            EventType.MANUAL_REVIEW_ACKNOWLEDGED: self._handle_manual_review_acknowledged,
            EventType.RECONCILIATION_COMPLETED: self._handle_reconciliation_completed,
        }.get(event.event_type)
        if handler:
            handler(event.payload, event.timestamp)

    def get_news_risk(self, symbol: str, now: datetime | None = None) -> NewsRiskLevel:
        direct, base = self._normalize_symbol(symbol)
        for key in (direct, base):
            if not key:
                continue
            flag = self.state.news_risk_flags.get(key)
            if flag:
                return flag.level_with_decay(
                    self.news_config.high_risk_block_hours,
                    self.news_config.medium_risk_block_hours,
                    now,
                )
        return "LOW"

    def blocks_entries(self, symbol: str, now: datetime | None = None) -> bool:
        direct, base = self._normalize_symbol(symbol)
        for key in (direct, base):
            if not key:
                continue
            flag = self.state.news_risk_flags.get(key)
            if flag and flag.blocks_entries(self.news_config.high_risk_block_hours, now):
                return True
        return False

    @staticmethod
    def _normalize_symbol(raw_symbol: str | None) -> tuple[str | None, str | None]:
        if not raw_symbol:
            return None, None
        symbol = raw_symbol.strip().upper()
        if not symbol:
            return None, None
        if symbol.endswith("USDT") and len(symbol) > 4:
            return symbol, symbol[:-4]
        return symbol, None

    def record_equity_snapshot(self, equity: float) -> None:
        self.state.equity = equity
        if equity > self.state.peak_equity:
            self.state.peak_equity = equity

    def reconcile(
        self,
        account_info: dict[str, Any],
        positions: list[dict[str, Any]],
        open_orders: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Return list of manual intervention events required to reconcile."""
        discrepancies: list[dict[str, Any]] = []
        equity = float(account_info.get("totalWalletBalance", account_info.get("totalMarginBalance", 0)))
        if equity > 0:
            self.state.equity = equity
            if equity > self.state.peak_equity:
                self.state.peak_equity = equity
        exchange_positions = {
            p["symbol"]: p
            for p in positions
            if abs(float(p.get("positionAmt", 0))) > 0
        }
        state_positions = {symbol: pos for symbol, pos in self.state.positions.items()}

        for symbol, pos_data in exchange_positions.items():
            size = float(pos_data.get("positionAmt", 0))
            entry_price = float(pos_data.get("entryPrice", 0) or 0)
            leverage = int(float(pos_data.get("leverage", 0) or 0))
            unrealized_pnl = float(
                pos_data.get("unRealizedProfit", pos_data.get("unrealizedProfit", 0) or 0)
            )
            if symbol not in state_positions:
                discrepancies.append(
                    {
                        "action": "POSITION_OPENED_EXTERNALLY",
                        "symbol": symbol,
                        "exchange_size": size,
                        "entry_price": entry_price,
                        "leverage": leverage,
                        "unrealized_pnl": unrealized_pnl,
                        "position_amt": size,
                    }
                )
            else:
                state_size = state_positions[symbol].quantity
                if abs(state_size - abs(size)) > 1e-8:
                    discrepancies.append(
                        {
                            "action": "POSITION_SIZE_CHANGED",
                            "symbol": symbol,
                            "exchange_size": size,
                            "state_size": state_size,
                            "entry_price": entry_price,
                            "leverage": leverage,
                            "unrealized_pnl": unrealized_pnl,
                            "position_amt": size,
                        }
                    )

        for symbol in state_positions:
            if symbol not in exchange_positions:
                discrepancies.append(
                    {
                        "action": "POSITION_CLOSED_EXTERNALLY",
                        "symbol": symbol,
                    }
                )

        exchange_orders = {
            o.get("clientOrderId") or str(o.get("orderId")): o for o in open_orders
        }
        state_orders = dict(self.state.open_orders)
        for client_id, order in exchange_orders.items():
            if client_id not in state_orders:
                discrepancies.append(
                    {
                        "action": "ORDER_PLACED_EXTERNALLY",
                        "symbol": order.get("symbol"),
                        "client_order_id": client_id,
                        "side": order.get("side"),
                        "order_type": order.get("type"),
                        "quantity": float(order.get("origQty", 0) or 0),
                        "price": float(order.get("price", 0) or 0),
                        "stop_price": float(order.get("stopPrice", 0) or 0),
                        "reduce_only": bool(order.get("reduceOnly", False)),
                        "order_id": order.get("orderId"),
                    }
                )
        for client_id in state_orders:
            if client_id not in exchange_orders:
                discrepancies.append(
                    {
                        "action": "ORDER_MISSING_ON_EXCHANGE",
                        "client_order_id": client_id,
                        "symbol": state_orders[client_id].symbol,
                    }
                )

        if discrepancies:
            self.state.requires_manual_review = True
        return discrepancies

    def _handle_news_classified(self, payload: dict[str, Any], timestamp: datetime) -> None:
        symbols = payload.get("symbols_mentioned", [])
        for raw_symbol in symbols:
            symbol, base = self._normalize_symbol(raw_symbol)
            for key in {symbol, base}:
                if not key:
                    continue
                self.state.news_risk_flags[key] = NewsRisk(
                    symbol=key,
                    level=payload.get("risk_level", "LOW"),
                    reason=payload.get("risk_reason"),
                    confidence=float(payload.get("confidence", 0)),
                    last_updated=timestamp,
                )

    def _handle_universe_updated(self, payload: dict[str, Any], timestamp: datetime) -> None:
        self.state.universe = payload.get("symbols", [])

    def _handle_order_placed(self, payload: dict[str, Any], timestamp: datetime) -> None:
        order = Order(
            client_order_id=payload["client_order_id"],
            symbol=payload["symbol"],
            side=payload["side"],
            order_type=payload["order_type"],
            quantity=float(payload["quantity"]),
            price=payload.get("price"),
            stop_price=payload.get("stop_price"),
            reduce_only=bool(payload.get("reduce_only", False)),
            status="NEW",
            created_at=timestamp,
            order_id=payload.get("order_id"),
        )
        self.state.open_orders[order.client_order_id] = order

    def _handle_order_cancelled(self, payload: dict[str, Any], timestamp: datetime) -> None:
        client_id = payload.get("client_order_id")
        if client_id and client_id in self.state.open_orders:
            self.state.open_orders.pop(client_id, None)

    def _handle_order_filled(self, payload: dict[str, Any], timestamp: datetime) -> None:
        client_id = payload.get("client_order_id")
        if client_id and client_id in self.state.open_orders:
            self.state.open_orders.pop(client_id, None)

    def _handle_order_partial_fill(self, payload: dict[str, Any], timestamp: datetime) -> None:
        client_id = payload.get("client_order_id")
        order = self.state.open_orders.get(client_id)
        if order:
            order.status = "PARTIALLY_FILLED"

    def _handle_position_opened(self, payload: dict[str, Any], timestamp: datetime) -> None:
        position = Position(
            symbol=payload["symbol"],
            side=payload["side"],
            quantity=float(payload["quantity"]),
            entry_price=float(payload["entry_price"]),
            leverage=int(payload.get("leverage", 1)),
            opened_at=timestamp,
            stop_price=payload.get("stop_price"),
            take_profit=payload.get("take_profit"),
            trade_id=payload.get("trade_id"),
        )
        self.state.positions[position.symbol] = position

    def _handle_position_updated(self, payload: dict[str, Any], timestamp: datetime) -> None:
        symbol = payload.get("symbol")
        position = self.state.positions.get(symbol)
        if not position:
            return
        if "quantity" in payload:
            position.quantity = float(payload["quantity"])
        if "unrealized_pnl" in payload:
            position.unrealized_pnl = float(payload["unrealized_pnl"])
        if "stop_price" in payload:
            position.stop_price = payload["stop_price"]
        if "take_profit" in payload:
            position.take_profit = payload["take_profit"]
        position.last_update = timestamp

    def _handle_position_closed(self, payload: dict[str, Any], timestamp: datetime) -> None:
        symbol = payload.get("symbol")
        position = self.state.positions.pop(symbol, None)
        if not position:
            return
        pnl = float(payload.get("realized_pnl", 0.0))
        self._update_daily_metrics(pnl, timestamp)
        self.state.equity += pnl
        if self.state.equity > self.state.peak_equity:
            self.state.peak_equity = self.state.equity

    def _handle_circuit_breaker(self, payload: dict[str, Any], timestamp: datetime) -> None:
        self.state.circuit_breaker_active = True

    def _handle_manual_intervention(self, payload: dict[str, Any], timestamp: datetime) -> None:
        self.state.requires_manual_review = True
        action = payload.get("action")
        if action == "POSITION_OPENED_EXTERNALLY":
            symbol = payload.get("symbol")
            size = float(payload.get("exchange_size", 0))
            if symbol and abs(size) > 0:
                side = "LONG" if size > 0 else "SHORT"
                leverage = int(float(payload.get("leverage", 1) or 1))
                self.state.positions[symbol] = Position(
                    symbol=symbol,
                    side=side,
                    quantity=abs(size),
                    entry_price=float(payload.get("entry_price", 0) or 0),
                    leverage=leverage if leverage > 0 else 1,
                    opened_at=timestamp,
                    unrealized_pnl=float(payload.get("unrealized_pnl", 0) or 0),
                )
        elif action == "POSITION_CLOSED_EXTERNALLY":
            symbol = payload.get("symbol")
            if symbol:
                self.state.positions.pop(symbol, None)
        elif action == "POSITION_SIZE_CHANGED":
            symbol = payload.get("symbol")
            size = float(payload.get("exchange_size", 0))
            if symbol and symbol in self.state.positions:
                position = self.state.positions[symbol]
                position.quantity = abs(size)
                if payload.get("entry_price") is not None:
                    position.entry_price = float(payload.get("entry_price", position.entry_price))
                if payload.get("leverage") is not None:
                    leverage = int(float(payload.get("leverage", position.leverage) or position.leverage))
                    position.leverage = leverage if leverage > 0 else position.leverage
                if payload.get("unrealized_pnl") is not None:
                    position.unrealized_pnl = float(
                        payload.get("unrealized_pnl", position.unrealized_pnl)
                    )
        elif action == "ORDER_PLACED_EXTERNALLY":
            client_id = payload.get("client_order_id")
            symbol = payload.get("symbol")
            if client_id and symbol:
                self.state.open_orders[client_id] = Order(
                    client_order_id=client_id,
                    symbol=symbol,
                    side=payload.get("side", ""),
                    order_type=payload.get("order_type", ""),
                    quantity=float(payload.get("quantity", 0) or 0),
                    price=float(payload.get("price", 0) or 0) or None,
                    stop_price=float(payload.get("stop_price", 0) or 0) or None,
                    reduce_only=bool(payload.get("reduce_only", False)),
                    status="NEW",
                    created_at=timestamp,
                    order_id=payload.get("order_id"),
                )
        elif action == "ORDER_MISSING_ON_EXCHANGE":
            client_id = payload.get("client_order_id")
            if client_id:
                self.state.open_orders.pop(client_id, None)

    def _handle_manual_review_acknowledged(
        self, payload: dict[str, Any], timestamp: datetime
    ) -> None:
        self.state.requires_manual_review = False

    def _handle_reconciliation_completed(self, payload: dict[str, Any], timestamp: datetime) -> None:
        self.state.last_reconciliation = timestamp

    def _update_daily_metrics(self, pnl: float, timestamp: datetime) -> None:
        date = datetime(timestamp.year, timestamp.month, timestamp.day, tzinfo=timezone.utc)
        if self.state.daily_loss_date != date:
            self.state.daily_loss_date = date
            self.state.realized_pnl_today = 0.0
            self.state.daily_loss = 0.0
            self.state.loss_timestamps = []
        self.state.realized_pnl_today += pnl
        self.state.daily_loss = min(self.state.realized_pnl_today, 0.0)
        if pnl < 0:
            self.state.consecutive_losses += 1
            self.state.last_loss_time = timestamp
            self.state.loss_timestamps.append(timestamp)
            cutoff = timestamp - timedelta(hours=self.risk_config.loss_streak_window_hours)
            self.state.loss_timestamps = [
                t for t in self.state.loss_timestamps if t >= cutoff
            ]
            if self.state.consecutive_losses >= self.risk_config.max_consecutive_losses:
                self.state.cooldown_until = timestamp + timedelta(
                    hours=self.risk_config.cooldown_after_loss_hours
                )
            if len(self.state.loss_timestamps) >= self.risk_config.max_loss_streak_24h:
                self.state.cooldown_until = timestamp + timedelta(
                    hours=self.risk_config.cooldown_after_max_daily_hours
                )
        else:
            self.state.consecutive_losses = 0
            self.state.cooldown_until = None

        if self.state.equity > 0:
            daily_loss_limit = -self.state.equity * (self.risk_config.max_daily_loss_pct / 100)
            if self.state.realized_pnl_today <= daily_loss_limit:
                self.state.cooldown_until = timestamp + timedelta(
                    hours=self.risk_config.cooldown_after_max_daily_hours
                )
                self.state.max_daily_loss_time = timestamp
