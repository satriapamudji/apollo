"""Event definitions and serialization helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4


class EventType(str, Enum):
    """All supported event types."""

    MARKET_TICK = "MarketTick"
    CANDLE_CLOSE = "CandleClose"
    FUNDING_UPDATE = "FundingUpdate"
    NEWS_INGESTED = "NewsIngested"
    NEWS_CLASSIFIED = "NewsClassified"
    UNIVERSE_UPDATED = "UniverseUpdated"
    SYMBOL_FILTERED = "SymbolFiltered"
    SIGNAL_COMPUTED = "SignalComputed"
    TRADE_PROPOSED = "TradeProposed"
    RISK_APPROVED = "RiskApproved"
    RISK_REJECTED = "RiskRejected"
    ORDER_PLACED = "OrderPlaced"
    ORDER_CANCELLED = "OrderCancelled"
    ORDER_FILLED = "OrderFilled"
    ORDER_PARTIAL_FILL = "OrderPartialFill"
    POSITION_OPENED = "PositionOpened"
    POSITION_UPDATED = "PositionUpdated"
    POSITION_CLOSED = "PositionClosed"
    STOP_TRIGGERED = "StopTriggered"
    CIRCUIT_BREAKER_TRIGGERED = "CircuitBreakerTriggered"
    MANUAL_INTERVENTION = "ManualInterventionDetected"
    MANUAL_REVIEW_ACKNOWLEDGED = "ManualReviewAcknowledged"
    ACCOUNT_SETTING_UPDATED = "AccountSettingUpdated"
    ACCOUNT_SETTING_FAILED = "AccountSettingFailed"
    SYSTEM_STARTED = "SystemStarted"
    SYSTEM_STOPPED = "SystemStopped"
    RECONCILIATION_COMPLETED = "ReconciliationCompleted"


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc)


def format_timestamp(ts: datetime) -> str:
    """Format timestamp as ISO-8601 with Z suffix."""
    return ts.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class Event:
    """Immutable event payload for event sourcing."""

    event_id: str
    event_type: EventType
    timestamp: datetime
    sequence_num: int
    payload: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize event to a JSON-compatible dict."""
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "timestamp": format_timestamp(self.timestamp),
            "sequence_num": self.sequence_num,
            "payload": self.payload,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Event":
        """Deserialize event from a dict."""
        ts = datetime.fromisoformat(data["timestamp"].replace("Z", "+00:00"))
        return cls(
            event_id=data["event_id"],
            event_type=EventType(data["event_type"]),
            timestamp=ts,
            sequence_num=int(data["sequence_num"]),
            payload=data.get("payload", {}),
            metadata=data.get("metadata", {}),
        )


def new_event(
    event_type: EventType,
    payload: dict[str, Any],
    sequence_num: int,
    metadata: dict[str, Any] | None = None,
) -> Event:
    """Create a new event with a fresh UUID."""
    return Event(
        event_id=str(uuid4()),
        event_type=event_type,
        timestamp=utc_now(),
        sequence_num=sequence_num,
        payload=payload,
        metadata=metadata or {},
    )
