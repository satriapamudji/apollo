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
    ) -> None:
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
            "entry_style": entry_style,
            "score_threshold": score_threshold,
            "trend_timeframe": trend_timeframe,
            "entry_timeframe": entry_timeframe,
            "has_open_position": has_open_position,
        }
        if signal.score:
            record["score"] = {
                "trend": signal.score.trend_score,
                "volatility": signal.score.volatility_score,
                "entry_quality": signal.score.entry_quality,
                "funding_penalty": signal.score.funding_penalty,
                "news_modifier": signal.score.news_modifier,
                "composite": signal.score.composite,
            }
        self._append(record)

    def log_risk(self, proposal: TradeProposal, risk_result: RiskCheckResult) -> None:
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
            "funding_rate": proposal.funding_rate,
            "approved": risk_result.approved,
            "reasons": risk_result.reasons,
            "size_multiplier": risk_result.size_multiplier,
            "adjusted_entry_threshold": risk_result.adjusted_entry_threshold,
            "adjusted_stop_multiplier": risk_result.adjusted_stop_multiplier,
            "circuit_breaker": risk_result.circuit_breaker,
        }
        if proposal.score:
            record["score"] = {
                "trend": proposal.score.trend_score,
                "volatility": proposal.score.volatility_score,
                "entry_quality": proposal.score.entry_quality,
                "funding_penalty": proposal.score.funding_penalty,
                "news_modifier": proposal.score.news_modifier,
                "composite": proposal.score.composite,
            }
        self._append(record)

    def _append(self, record: dict[str, Any]) -> None:
        with open(self.log_path, "a", newline="") as handle:
            handle.write(json.dumps(record, ensure_ascii=True) + "\n")
