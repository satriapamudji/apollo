"""Thinking log writer (JSONL) for signals and risk decisions."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.models import TradeProposal
from src.risk.engine import RiskCheckResult
from src.strategy.signals import Signal


class ThinkingLogger:
    """Write per-signal and per-risk entries to a JSONL file."""

    def __init__(self, log_path: str) -> None:
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def log_signal(
        self,
        signal: Signal,
        news_risk: str,
        funding_rate: float,
        entry_style: str,
        score_threshold: float,
        trend_timeframe: str,
        entry_timeframe: str,
        has_open_position: bool,
        news_blocked: bool,
        leverage: int = 1,
    ) -> None:
        # Calculate estimated funding carry based on leverage and position size
        # Carry per 8 hours = position_value * leverage * funding_rate
        funding_pct = funding_rate * 100
        is_long_payer = funding_rate > 0  # Long pays short when rate is positive

        record: dict[str, Any] = {
            "event": "signal",
            "timestamp": (signal.timestamp or datetime.now(timezone.utc)).isoformat(),
            "symbol": signal.symbol,
            "signal_type": signal.signal_type.value,
            "price": signal.price,
            "atr": signal.atr,
            "entry_price": signal.entry_price,
            "stop_price": signal.stop_price,
            "take_profit": signal.take_profit,
            "reason": signal.reason,
            "trade_id": signal.trade_id,
            "news_risk": news_risk,
            "news_blocked": news_blocked,
            "funding_rate": funding_rate,
            "funding_rate_pct": funding_pct,
            "funding_currency": "long_pays_short"
            if is_long_payer
            else "short_pays_long"
            if funding_rate < 0
            else "neutral",
            "entry_style": entry_style,
            "score_threshold": score_threshold,
            "trend_timeframe": trend_timeframe,
            "entry_timeframe": entry_timeframe,
            "has_open_position": has_open_position,
            "entry_extension": signal.entry_extension,
            "volume_ratio": signal.volume_ratio,
            "estimated_funding_carry": {
                "per_8h_1k": 1000 * leverage * funding_rate,
                "per_8h_10k": 10000 * leverage * funding_rate,
                "per_8h_100k": 100000 * leverage * funding_rate,
                "daily_1k": 3000 * leverage * funding_rate,
                "daily_10k": 30000 * leverage * funding_rate,
                "daily_100k": 300000 * leverage * funding_rate,
            },
        }
        if signal.regime:
            record["regime"] = {
                "type": signal.regime.regime.value,
                "adx": signal.regime.adx,
                "chop": signal.regime.chop,
                "blocks_entry": signal.regime.blocks_entry,
                "size_multiplier": signal.regime.size_multiplier,
            }
            if signal.regime.volatility_regime is not None:
                record["regime"]["volatility_regime"] = signal.regime.volatility_regime.value
                record["regime"]["volatility_contracts"] = signal.regime.volatility_contracts
                record["regime"]["volatility_expands"] = signal.regime.volatility_expands
        if signal.score:
            record["score"] = {
                "trend": signal.score.trend_score,
                "volatility": signal.score.volatility_score,
                "entry_quality": signal.score.entry_quality,
                "funding_penalty": signal.score.funding_penalty,
                "news_modifier": signal.score.news_modifier,
                "liquidity": signal.score.liquidity_score,
                "volume": signal.score.volume_score,
                "composite": signal.score.composite,
            }
        self._append(record)

    def log_risk(self, proposal: TradeProposal, risk_result: RiskCheckResult) -> None:
        funding_rate = proposal.funding_rate
        funding_pct = funding_rate * 100
        is_long_payer = funding_rate > 0

        record: dict[str, Any] = {
            "event": "risk",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "symbol": proposal.symbol,
            "trade_id": proposal.trade_id,
            "side": proposal.side,
            "entry_price": proposal.entry_price,
            "stop_price": proposal.stop_price,
            "take_profit": proposal.take_profit,
            "atr": proposal.atr,
            "leverage": proposal.leverage,
            "news_risk": proposal.news_risk,
            "funding_rate": funding_rate,
            "funding_rate_pct": funding_pct,
            "funding_currency": "long_pays_short"
            if is_long_payer
            else "short_pays_long"
            if funding_rate < 0
            else "neutral",
            "approved": risk_result.approved,
            "reasons": risk_result.reasons,
            "size_multiplier": risk_result.size_multiplier,
            "adjusted_entry_threshold": risk_result.adjusted_entry_threshold,
            "adjusted_stop_multiplier": risk_result.adjusted_stop_multiplier,
            "circuit_breaker": risk_result.circuit_breaker,
            "estimated_funding_carry": {
                "per_8h_1k": 1000 * proposal.leverage * funding_rate,
                "per_8h_10k": 10000 * proposal.leverage * funding_rate,
                "per_8h_100k": 100000 * proposal.leverage * funding_rate,
                "daily_1k": 3000 * proposal.leverage * funding_rate,
                "daily_10k": 30000 * proposal.leverage * funding_rate,
                "daily_100k": 300000 * proposal.leverage * funding_rate,
            },
        }
        if proposal.score:
            record["score"] = {
                "trend": proposal.score.trend_score,
                "volatility": proposal.score.volatility_score,
                "entry_quality": proposal.score.entry_quality,
                "funding_penalty": proposal.score.funding_penalty,
                "news_modifier": proposal.score.news_modifier,
                "liquidity": proposal.score.liquidity_score,
                "volume": proposal.score.volume_score,
                "composite": proposal.score.composite,
            }
        self._append(record)

    def log_pretrade_rejection(
        self,
        proposal: TradeProposal,
        rejection_type: str,
        reason: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Log a pre-trade rejection due to microstructure constraints."""
        record: dict[str, Any] = {
            "event": "pretrade_rejection",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "symbol": proposal.symbol,
            "trade_id": proposal.trade_id,
            "side": proposal.side,
            "entry_price": proposal.entry_price,
            "rejection_type": rejection_type,
            "reason": reason,
        }
        if details:
            record["details"] = details
        self._append(record)

    def _append(self, record: dict[str, Any]) -> None:
        with open(self.log_path, "a", newline="") as handle:
            handle.write(json.dumps(record, ensure_ascii=True) + "\n")
