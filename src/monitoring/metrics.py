"""Prometheus metrics definitions."""

from __future__ import annotations

import time
from datetime import datetime, timezone

from prometheus_client import Gauge, Histogram, Counter, start_http_server

from src.ledger.state import TradingState


class Metrics:
    """Expose core metrics for monitoring."""

    def __init__(self) -> None:
        self.ws_connected = Gauge("ws_connected", "WebSocket connection status")
        self.ws_last_message_age_sec = Gauge("ws_last_message_age_sec", "Age of last WS msg")
        self.rest_request_latency_ms = Histogram("rest_request_latency_ms", "REST latency (ms)")
        self.rest_error_rate = Counter("rest_error_rate", "REST error rate")

        self.open_positions = Gauge("open_positions", "Number of open positions")
        self.daily_pnl_percent = Gauge("daily_pnl_percent", "Daily PnL percent")
        self.drawdown_percent = Gauge("drawdown_percent", "Drawdown percent")

        self.order_fill_latency_ms = Histogram("order_fill_latency_ms", "Order fill latency (ms)")
        self.order_reject_count = Counter("order_reject_count", "Order rejects")
        self.slippage_bps = Histogram("slippage_bps", "Slippage in bps")

        self.margin_ratio = Gauge("margin_ratio", "Margin ratio estimate")
        self.leverage_used = Gauge("leverage_used", "Leverage used")
        self.circuit_breaker_active = Gauge("circuit_breaker_active", "Circuit breaker active")
        self.requires_manual_review = Gauge(
            "requires_manual_review", "Manual review required"
        )
        self.cooldown_active = Gauge("cooldown_active", "Cooldown active")

        self.event_ledger_size = Gauge("event_ledger_size", "Event ledger size")
        self.last_reconciliation_age_hr = Gauge(
            "last_reconciliation_age_hr", "Hours since last reconciliation"
        )
        self.last_event_sequence = Gauge(
            "last_event_sequence", "Last applied event sequence number"
        )
        self.memory_usage_mb = Gauge("memory_usage_mb", "Memory usage (MB)")

    def start(self, port: int) -> None:
        start_http_server(port)

    def update_state(self, state: TradingState) -> None:
        self.open_positions.set(len(state.positions))
        now = datetime.now(timezone.utc)
        if state.equity > 0:
            pnl_pct = (state.realized_pnl_today / state.equity) * 100
            self.daily_pnl_percent.set(pnl_pct)
            drawdown = (state.peak_equity - state.equity) / state.peak_equity * 100
            self.drawdown_percent.set(drawdown)
        self.circuit_breaker_active.set(1 if state.circuit_breaker_active else 0)
        self.requires_manual_review.set(1 if state.requires_manual_review else 0)
        cooldown = 1 if state.cooldown_until and state.cooldown_until > now else 0
        self.cooldown_active.set(cooldown)
        self.last_event_sequence.set(state.last_event_sequence)
        if state.last_reconciliation:
            age = (time.time() - state.last_reconciliation.timestamp()) / 3600
            self.last_reconciliation_age_hr.set(age)
