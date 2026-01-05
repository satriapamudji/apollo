"""Shared data models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from src.strategy.scoring import CompositeScore


Side = Literal["LONG", "SHORT"]
NewsRiskLevel = Literal["HIGH", "MEDIUM", "LOW"]


@dataclass(frozen=True)
class TradeProposal:
    symbol: str
    side: Side
    entry_price: float
    stop_price: float
    take_profit: float | None
    atr: float
    leverage: int
    score: CompositeScore | None
    funding_rate: float
    news_risk: NewsRiskLevel
    trade_id: str
    created_at: datetime
    is_entry: bool = True
    reason: str | None = None
