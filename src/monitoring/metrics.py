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
        self.loop_last_tick_age_sec = Gauge(
            "loop_last_tick_age_sec",
            "Seconds since the loop last ticked",
            ["loop"],
        )

        self.open_positions = Gauge("open_positions", "Number of open positions")
        self.daily_pnl_percent = Gauge("daily_pnl_percent", "Daily PnL percent")
        self.drawdown_percent = Gauge("drawdown_percent", "Drawdown percent")

        self.order_fill_latency_ms = Histogram("order_fill_latency_ms", "Order fill latency (ms)")
        self.order_reject_count = Counter("order_reject_count", "Order rejects")
        self.slippage_bps = Histogram("slippage_bps", "Slippage in bps")

        self.margin_ratio = Gauge("margin_ratio", "Margin ratio estimate")
        self.leverage_used = Gauge("leverage_used", "Leverage used")
        self.circuit_breaker_active = Gauge("circuit_breaker_active", "Circuit breaker active")
        self.requires_manual_review = Gauge("requires_manual_review", "Manual review required")
        self.cooldown_active = Gauge("cooldown_active", "Cooldown active")

        self.event_ledger_size = Gauge("event_ledger_size", "Event ledger size")
        self.last_reconciliation_age_hr = Gauge(
            "last_reconciliation_age_hr", "Hours since last reconciliation"
        )
        self.last_event_sequence = Gauge(
            "last_event_sequence", "Last applied event sequence number"
        )
        self.memory_usage_mb = Gauge("memory_usage_mb", "Memory usage (MB)")

        # Reconciliation metrics
        self.reconciliation_discrepancy_total = Counter(
            "reconciliation_discrepancy_total",
            "Total reconciliation discrepancies by type",
            ["type"],
        )
        self.reconciliation_failure_total = Counter(
            "reconciliation_failure_total", "Total reconciliation failures"
        )
        self.reconciliation_success_total = Counter(
            "reconciliation_success_total", "Total successful reconciliations"
        )
        self.reconciliation_consecutive_failures = Gauge(
            "reconciliation_consecutive_failures",
            "Consecutive reconciliation failures",
        )

        # Binance rate-limit telemetry metrics
        self.binance_used_weight_1m = Gauge(
            "binance_used_weight_1m",
            "Actual used weight from Binance x-mbx-used-weight-1m header",
        )
        self.binance_order_count_10s = Gauge(
            "binance_order_count_10s",
            "Order count from Binance x-mbx-order-count-10s header",
        )
        self.binance_order_count_1m = Gauge(
            "binance_order_count_1m",
            "Order count from Binance x-mbx-order-count-1m header",
        )
        self.binance_request_throttled_total = Counter(
            "binance_request_throttled_total",
            "Total number of throttled requests by endpoint",
            ["endpoint"],
        )
        self.binance_request_retry_total = Counter(
            "binance_request_retry_total",
            "Total number of retries by endpoint",
            ["endpoint"],
        )
        self.binance_time_sync_offset_ms = Gauge(
            "binance_time_sync_offset_ms",
            "Time offset between local and Binance server time (ms)",
        )

        # === Performance Telemetry Metrics ===

        # Fill rate metrics
        self.orders_placed_total = Counter(
            "orders_placed_total",
            "Total entry orders placed (non-reduce-only)",
        )
        self.orders_filled_total = Counter(
            "orders_filled_total",
            "Total entry orders filled",
        )
        self.fill_rate_pct = Gauge(
            "fill_rate_pct",
            "Fill rate percentage (filled/placed * 100)",
        )

        # Slippage metrics (derived from existing slippage_bps histogram)
        self.avg_entry_slippage_bps = Gauge(
            "avg_entry_slippage_bps",
            "Average entry slippage in basis points",
        )
        self.avg_exit_slippage_bps = Gauge(
            "avg_exit_slippage_bps",
            "Average exit slippage in basis points",
        )

        # Cost metrics
        self.fees_paid_total = Counter(
            "fees_paid_total",
            "Total trading fees paid (USD)",
        )
        self.funding_received_total = Counter(
            "funding_received_total",
            "Total funding fees received (USD)",
        )
        self.funding_paid_total = Counter(
            "funding_paid_total",
            "Total funding fees paid (USD)",
        )
        self.net_funding = Gauge(
            "net_funding",
            "Net funding (received - paid, USD)",
        )

        # Performance metrics
        self.expectancy_per_trade = Gauge(
            "expectancy_per_trade",
            "Average expectancy per trade (USD)",
        )
        self.profit_factor_session = Gauge(
            "profit_factor_session",
            "Profit factor since session start (gross_profit / gross_loss)",
        )
        self.profit_factor_7d = Gauge(
            "profit_factor_7d",
            "Profit factor rolling 7 days",
        )
        self.profit_factor_30d = Gauge(
            "profit_factor_30d",
            "Profit factor rolling 30 days",
        )
        self.win_rate_pct = Gauge(
            "win_rate_pct",
            "Win rate percentage (winning trades / total trades * 100)",
        )

        # Time metrics
        self.time_in_market_pct = Gauge(
            "time_in_market_pct",
            "Percentage of time with open positions",
        )
        self.avg_holding_time_hours = Gauge(
            "avg_holding_time_hours",
            "Average trade holding time in hours",
        )
        self.trades_closed_total = Counter(
            "trades_closed_total",
            "Total number of trades closed",
        )

        # Spread metrics
        self.trade_spread_pct = Histogram(
            "trade_spread_pct",
            "Spread at time of entry in percent",
            buckets=[0.01, 0.02, 0.05, 0.1, 0.15, 0.2, 0.3, 0.5, 1.0],
        )
        self.spread_rejections_total = Counter(
            "spread_rejections_total",
            "Entries blocked due to spread being too wide",
        )

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
