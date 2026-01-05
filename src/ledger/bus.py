"""Event bus that appends to the ledger before dispatching."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any, Awaitable, Callable

import structlog

from src.ledger.events import Event, EventType
from src.ledger.store import EventLedger

EventHandler = Callable[[Event], Awaitable[None] | None]


class EventBus:
    """Publish events to the ledger and notify subscribers."""

    def __init__(self, ledger: EventLedger) -> None:
        self._ledger = ledger
        self._handlers: dict[EventType, list[EventHandler]] = defaultdict(list)
        self._log = structlog.get_logger(__name__)

    def register(self, event_type: EventType, handler: EventHandler) -> None:
        """Register a handler for a specific event type."""
        self._handlers[event_type].append(handler)

    async def publish(
        self,
        event_type: EventType,
        payload: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> Event:
        """Append the event and dispatch to handlers."""
        event = self._ledger.append(event_type, payload, metadata)
        await self._dispatch(event)
        return event

    async def _dispatch(self, event: Event) -> None:
        handlers = self._handlers.get(event.event_type, [])
        for handler in handlers:
            try:
                result = handler(event)
                if asyncio.iscoroutine(result):
                    await result
            except asyncio.CancelledError:
                raise
            except Exception:
                handler_name = getattr(handler, "__name__", repr(handler))
                self._log.exception(
                    "event_handler_failed",
                    event_id=event.event_id,
                    event_type=event.event_type.value,
                    handler=handler_name,
                )
                if event.event_type != EventType.MANUAL_INTERVENTION:
                    try:
                        await self.publish(
                            EventType.MANUAL_INTERVENTION,
                            {
                                "action": "HANDLER_EXCEPTION",
                                "event_id": event.event_id,
                                "event_type": event.event_type.value,
                                "handler": handler_name,
                            },
                            {"source": "event_bus"},
                        )
                    except Exception:
                        self._log.exception(
                            "manual_intervention_publish_failed",
                            event_id=event.event_id,
                            event_type=event.event_type.value,
                        )
