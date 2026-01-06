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
    candle_timestamp: datetime | None = None  # The candle this signal belongs to (for lifecycle tracking)


@dataclass(frozen=True)
class OrderLifecycle:
    """Tracks the full lifecycle of an entry order."""

    client_order_id: str
    trade_id: str
    symbol: str
    side: Side
    state: Literal["PLACED", "OPEN", "FILLED", "CANCELLED", "EXPIRED"]
    created_at: datetime
    last_updated: datetime
    candle_timestamp: datetime | None  # The candle this order belongs to
    fill_price: float | None = None
    cancel_reason: str | None = None
    attempt_count: int = 1
