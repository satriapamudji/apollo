"""Console logger for key ledger events."""

from __future__ import annotations

from collections.abc import Iterable

import structlog

from src.ledger.events import Event, EventType


class EventConsoleLogger:
    """Emit selected ledger events to stdout via structlog."""

    def __init__(self, include: Iterable[EventType] | None = None) -> None:
        self.include = set(
            include
            or {
                EventType.ACCOUNT_SETTING_UPDATED,
                EventType.ACCOUNT_SETTING_FAILED,
                EventType.ORDER_PLACED,
                EventType.ORDER_PARTIAL_FILL,
                EventType.ORDER_FILLED,
                EventType.ORDER_CANCELLED,
                EventType.POSITION_OPENED,
                EventType.POSITION_CLOSED,
                EventType.MANUAL_INTERVENTION,
            }
        )
        self.log = structlog.get_logger("ledger_events")

    def handle_event(self, event: Event) -> None:
        if event.event_type not in self.include:
            return
        self.log.info(
            "ledger_event",
            event_type=event.event_type.value,
            sequence_num=event.sequence_num,
            payload=event.payload,
            metadata=event.metadata,
        )

