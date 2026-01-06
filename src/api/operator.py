"""Operator API for inspecting state and taking safe actions."""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Query

from src.config.settings import Settings, load_settings
from src.ledger import EventLedger, EventType, StateManager
from src.ledger.events import Event
from src.ledger.state import TradingState

# Global instances (reused across requests)
_settings: Settings | None = None
_ledger: EventLedger | None = None
_state_manager: StateManager | None = None
_start_time: float = 0.0
_ws_last_msg_time: float = 0.0


def _get_instances() -> tuple[Settings, EventLedger, StateManager]:
    """Get or create global instances."""
    global _settings, _ledger, _state_manager
    if _settings is None:
        _settings = load_settings()
    if _ledger is None:
        _ledger = EventLedger(_settings.storage.ledger_path)
    if _state_manager is None:
        _state_manager = StateManager(
            initial_equity=100.0,
            risk_config=_settings.risk,
            news_config=_settings.news,
        )
        _state_manager.rebuild(_ledger.load_all())
    return _settings, _ledger, _state_manager


def _serialize_state(state: TradingState) -> dict[str, Any]:
    """Serialize TradingState to JSON-compatible dict."""
    return {
        "equity": state.equity,
        "peak_equity": state.peak_equity,
        "realized_pnl_today": state.realized_pnl_today,
        "daily_loss": state.daily_loss,
        "consecutive_losses": state.consecutive_losses,
        "cooldown_until": (
            state.cooldown_until.isoformat() if state.cooldown_until else None
        ),
        "circuit_breaker_active": state.circuit_breaker_active,
        "requires_manual_review": state.requires_manual_review,
        "last_reconciliation": (
            state.last_reconciliation.isoformat() if state.last_reconciliation else None
        ),
        "last_event_sequence": state.last_event_sequence,
        "universe": state.universe,
        "positions": {
            symbol: {
                "symbol": pos.symbol,
                "side": pos.side,
                "quantity": pos.quantity,
                "entry_price": pos.entry_price,
                "leverage": pos.leverage,
                "opened_at": pos.opened_at.isoformat(),
                "stop_price": pos.stop_price,
                "take_profit": pos.take_profit,
                "unrealized_pnl": pos.unrealized_pnl,
                "realized_pnl": pos.realized_pnl,
            }
            for symbol, pos in state.positions.items()
        },
        "open_orders": {
            oid: {
                "client_order_id": order.client_order_id,
                "symbol": order.symbol,
                "side": order.side,
                "order_type": order.order_type,
                "quantity": order.quantity,
                "price": order.price,
                "stop_price": order.stop_price,
                "reduce_only": order.reduce_only,
                "status": order.status,
                "created_at": order.created_at.isoformat(),
                "order_id": order.order_id,
            }
            for oid, order in state.open_orders.items()
        },
        "news_risk_flags": {
            symbol: {
                "symbol": nr.symbol,
                "level": nr.level,
                "reason": nr.reason,
                "confidence": nr.confidence,
                "last_updated": nr.last_updated.isoformat(),
            }
            for symbol, nr in state.news_risk_flags.items()
        },
    }


def _serialize_event(event: Event) -> dict[str, Any]:
    """Serialize Event to JSON-compatible dict."""
    return {
        "event_id": event.event_id,
        "event_type": event.event_type.value,
        "timestamp": event.timestamp.isoformat(),
        "sequence_num": event.sequence_num,
        "payload": event.payload,
        "metadata": event.metadata,
    }


@asynccontextmanager
async def lifespan(app: FastAPI) -> Any:
    """Lifespan context manager for startup/shutdown."""
    global _start_time
    _start_time = time.time()
    yield


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="Trading Bot Operator API",
        description="Inspect state and take safe actions on the trading bot",
        version="0.1.0",
        lifespan=lifespan,
    )

    @app.get("/health")
    async def health() -> dict[str, Any]:
        """Get system health status."""
        settings, _, _ = _get_instances()
        uptime_sec = time.time() - _start_time
        ws_msg_age_sec = time.time() - _ws_last_msg_time if _ws_last_msg_time > 0 else None

        # Determine trading enabled status
        trading_enabled = (
            settings.run.enable_trading and
            settings.run.mode in ("testnet", "live") and
            not _state_manager.state.circuit_breaker_active if _state_manager else False
        )

        return {
            "status": "healthy",
            "uptime_sec": uptime_sec,
            "mode": settings.run.mode,
            "trading_enabled": trading_enabled,
            "environment": settings.environment,
            "ws_last_message_age_sec": ws_msg_age_sec,
            "api_port": settings.monitoring.api_port,
            "metrics_port": settings.monitoring.metrics_port,
        }

    @app.get("/state")
    async def get_state() -> dict[str, Any]:
        """Get current trading state."""
        _, _, state_manager = _get_instances()
        return _serialize_state(state_manager.state)

    @app.get("/events")
    async def get_events(
        tail: int = Query(default=100, ge=1, le=1000, description="Number of recent events"),
    ) -> dict[str, Any]:
        """Get recent events from the ledger."""
        _, ledger, _ = _get_instances()
        all_events = list(ledger.iter_events())
        recent_events = all_events[-tail:] if tail < len(all_events) else all_events
        return {
            "count": len(recent_events),
            "total": len(all_events),
            "events": [_serialize_event(e) for e in recent_events],
        }

    @app.post("/actions/ack-manual-review")
    async def ack_manual_review(reason: str = Query(default="operator_api")) -> dict[str, Any]:
        """Acknowledge manual review and clear the flag."""
        settings, ledger, state_manager = _get_instances()

        was_flagged = state_manager.state.requires_manual_review

        ledger.append(
            EventType.MANUAL_REVIEW_ACKNOWLEDGED,
            {"reason": reason, "previously_flagged": was_flagged},
            {"source": "operator_api"},
        )

        # Rebuild state to reflect the change
        state_manager.rebuild(ledger.load_all())

        if was_flagged:
            msg = "Manual review acknowledged. Restart bot or wait for next reconciliation."
        else:
            msg = "Manual review already clear."

        return {
            "success": True,
            "previously_flagged": was_flagged,
            "message": msg,
        }

    @app.post("/actions/kill-switch")
    async def kill_switch(reason: str = Query(default="operator_kill_switch")) -> dict[str, Any]:
        """Trigger system shutdown event (kill switch)."""
        settings, ledger, state_manager = _get_instances()

        ledger.append(
            EventType.SYSTEM_STOPPED,
            {"reason": reason, "initiated_by": "operator_api"},
            {"source": "operator_api", "kill_switch": True},
        )

        return {
            "success": True,
            "message": "Kill switch triggered. SYSTEM_STOPPED event recorded.",
            "reason": reason,
        }

    @app.post("/actions/pause")
    async def pause(
        reason: str = Query(default="operator_pause"),
        duration_hours: int = Query(default=4, ge=1, le=48),
    ) -> dict[str, Any]:
        """Pause trading by setting cooldown period."""
        settings, ledger, state_manager = _get_instances()

        now = datetime.now(timezone.utc)
        from datetime import timedelta

        cooldown_until = now + timedelta(hours=duration_hours)

        # Record the cooldown by appending a dummy event that triggers cooldown logic
        # We use MANUAL_INTERVENTION to trigger the cooldown handler if needed
        # Or we can just set cooldown_until directly and record it
        ledger.append(
            EventType.MANUAL_INTERVENTION,
            {
                "action": "OPERATOR_PAUSE",
                "reason": reason,
                "cooldown_until": cooldown_until.isoformat(),
                "duration_hours": duration_hours,
            },
            {"source": "operator_api"},
        )

        # Manually set the cooldown in state
        state_manager.state.cooldown_until = cooldown_until

        return {
            "success": True,
            "message": f"Trading paused until {cooldown_until.isoformat()}",
            "cooldown_until": cooldown_until.isoformat(),
            "reason": reason,
        }

    @app.post("/actions/resume")
    async def resume(reason: str = Query(default="operator_resume")) -> dict[str, Any]:
        """Resume trading by clearing cooldown and manual review."""
        settings, ledger, state_manager = _get_instances()

        was_in_cooldown = state_manager.state.cooldown_until is not None
        was_manual_review = state_manager.state.requires_manual_review

        # Clear the cooldown
        state_manager.state.cooldown_until = None

        # Clear manual review flag
        if state_manager.state.requires_manual_review:
            ledger.append(
                EventType.MANUAL_REVIEW_ACKNOWLEDGED,
                {"reason": reason, "previously_flagged": True},
                {"source": "operator_api"},
            )
            state_manager.state.requires_manual_review = False

        return {
            "success": True,
            "message": "Trading resumed.",
            "previously_in_cooldown": was_in_cooldown,
            "previously_in_manual_review": was_manual_review,
            "reason": reason,
        }

    @app.get("/")
    async def root() -> dict[str, Any]:
        """Root endpoint with API info."""
        return {
            "name": "Trading Bot Operator API",
            "version": "0.1.0",
            "endpoints": {
                "health": "GET /health",
                "state": "GET /state",
                "events": "GET /events?tail=N",
                "ack_manual_review": "POST /actions/ack-manual-review?reason=<text>",
                "kill_switch": "POST /actions/kill-switch?reason=<text>",
                "pause": "POST /actions/pause?reason=<text>&duration_hours=<int>",
                "resume": "POST /actions/resume?reason=<text>",
            },
        }

    return app


def update_ws_last_message_time() -> None:
    """Update the last WS message timestamp. Call this from the main loop."""
    global _ws_last_msg_time
    _ws_last_msg_time = time.time()
