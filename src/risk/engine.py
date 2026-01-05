"""Deterministic risk engine with hard limits."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

from src.config.settings import RiskConfig
from src.ledger.state import TradingState
from src.models import TradeProposal
from src.risk.sizing import PositionSizer, SymbolFilters


NewsRiskLevel = Literal["HIGH", "MEDIUM", "LOW"]


@dataclass(frozen=True)
class RiskCheckResult:
    approved: bool
    reasons: list[str] = field(default_factory=list)
    size_multiplier: float = 1.0
    adjusted_entry_threshold: float | None = None
    adjusted_stop_multiplier: float | None = None
    circuit_breaker: bool = False


class RiskEngine:
    """Evaluate trade proposals against hard risk rules."""

    HARD_MAX_RISK_PCT = 1.0
    HARD_MAX_LEVERAGE = 5
    HARD_MAX_DAILY_LOSS_PCT = 3.0
    HARD_MAX_DRAWDOWN_PCT = 10.0
    HARD_MAX_POSITIONS = 1
    HARD_MAX_CONSECUTIVE_LOSSES = 3

    def __init__(self, config: RiskConfig) -> None:
        self.config = config
        self.risk_per_trade_pct = min(config.risk_per_trade_pct, self.HARD_MAX_RISK_PCT)
        self.max_leverage = min(config.max_leverage, self.HARD_MAX_LEVERAGE)
        self.max_daily_loss_pct = min(config.max_daily_loss_pct, self.HARD_MAX_DAILY_LOSS_PCT)
        self.max_drawdown_pct = min(config.max_drawdown_pct, self.HARD_MAX_DRAWDOWN_PCT)
        self.max_positions = min(config.max_positions, self.HARD_MAX_POSITIONS)
        self.max_consecutive_losses = min(config.max_consecutive_losses, self.HARD_MAX_CONSECUTIVE_LOSSES)
        self.sizer = PositionSizer(self.risk_per_trade_pct, self.max_leverage)

    def evaluate(
        self,
        state: TradingState,
        proposal: TradeProposal,
        symbol_filters: SymbolFilters,
        now: datetime,
    ) -> RiskCheckResult:
        reasons: list[str] = []
        size_multiplier = 1.0
        adjusted_entry_threshold = None
        adjusted_stop_multiplier = None

        if proposal.is_entry and state.circuit_breaker_active:
            return RiskCheckResult(approved=False, reasons=["CIRCUIT_BREAKER_ACTIVE"])

        if state.equity < 10:
            reasons.append("EQUITY_BELOW_MINIMUM")

        daily_loss_limit = -state.equity * (self.max_daily_loss_pct / 100)
        if state.realized_pnl_today <= daily_loss_limit:
            reasons.append("DAILY_LOSS_LIMIT")

        drawdown = state.peak_equity - state.equity
        if state.peak_equity > 0:
            drawdown_pct = (drawdown / state.peak_equity) * 100
            if drawdown_pct >= self.max_drawdown_pct:
                return RiskCheckResult(
                    approved=False,
                    reasons=["MAX_DRAWDOWN"],
                    circuit_breaker=True,
                )

        if proposal.is_entry and len(state.positions) >= self.max_positions:
            reasons.append("MAX_POSITIONS_REACHED")

        if proposal.leverage > self.max_leverage:
            reasons.append("LEVERAGE_EXCEEDS_LIMIT")

        if proposal.is_entry and proposal.symbol in state.positions:
            reasons.append("SYMBOL_ALREADY_OPEN")

        if proposal.is_entry:
            has_open_order = any(
                order.symbol == proposal.symbol and not order.reduce_only
                for order in state.open_orders.values()
            )
            if has_open_order:
                reasons.append("OPEN_ORDER_EXISTS")

        if proposal.stop_price is None or proposal.atr <= 0:
            reasons.append("STOP_LOSS_MISSING")
        else:
            stop_distance_atr = abs(proposal.entry_price - proposal.stop_price) / proposal.atr
            if stop_distance_atr > 3.0:
                reasons.append("STOP_TOO_WIDE")

        funding_pct = self._funding_percent(proposal.funding_rate)
        if abs(funding_pct) > 0.2:
            reasons.append("FUNDING_TOO_HIGH")
        elif abs(funding_pct) > 0.1:
            size_multiplier *= 0.75

        if proposal.news_risk == "HIGH":
            reasons.append("NEWS_HIGH_RISK")
        elif proposal.news_risk == "MEDIUM":
            size_multiplier *= 0.5
            adjusted_entry_threshold = 0.75
            adjusted_stop_multiplier = 1.5

        if state.last_loss_time:
            if state.consecutive_losses >= self.max_consecutive_losses:
                if now - state.last_loss_time < timedelta(hours=self.config.cooldown_after_loss_hours):
                    reasons.append("COOLDOWN_AFTER_LOSS")

        loss_24h = [t for t in state.loss_timestamps if now - t < timedelta(hours=24)]
        if len(loss_24h) >= 5:
            reasons.append("COOLDOWN_AFTER_LOSS_STREAK")

        if state.cooldown_until and now < state.cooldown_until:
            reasons.append("COOLDOWN_ACTIVE")

        if proposal.is_entry:
            sizing = self.sizer.calculate_size(
                equity=state.equity,
                entry_price=proposal.entry_price,
                stop_price=proposal.stop_price or proposal.entry_price,
                symbol_filters=symbol_filters,
                leverage=proposal.leverage,
            )
            if not sizing:
                reasons.append("SIZE_BELOW_MIN_NOTIONAL")
            else:
                projected_leverage = sizing.notional / state.equity if state.equity > 0 else 0
                if projected_leverage > self.max_leverage * 0.8:
                    reasons.append("MARGIN_RATIO_HIGH")

        approved = len(reasons) == 0
        return RiskCheckResult(
            approved=approved,
            reasons=reasons,
            size_multiplier=size_multiplier,
            adjusted_entry_threshold=adjusted_entry_threshold,
            adjusted_stop_multiplier=adjusted_stop_multiplier,
        )

    @staticmethod
    def _funding_percent(funding_rate: float) -> float:
        if abs(funding_rate) <= 1:
            return funding_rate * 100
        return funding_rate
