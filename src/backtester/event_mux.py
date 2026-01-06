"""Event multiplexer for deterministic multi-symbol backtesting.

Provides heap-based merge of events from multiple symbol iterators,
ensuring deterministic ordering by (timestamp, priority, symbol).
"""

from __future__ import annotations

import heapq
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from src.backtester.events import BacktestEvent, BarEvent, FundingEvent

if TYPE_CHECKING:
    from src.backtester.data_reader import FundingDataIterator, SymbolDataIterator


@dataclass(order=True)
class HeapEntry:
    """Wrapper for heap-based event ordering.

    Uses a tuple key for deterministic comparison:
    (timestamp, priority, symbol, interval, sequence, counter)

    The counter ensures FIFO ordering for events with identical keys.
    """

    sort_key: tuple = field(compare=True)
    event: BacktestEvent = field(compare=False)
    source_idx: int = field(compare=False)  # Index into sources list for refilling


class EventMux:
    """Multiplexer for deterministic event ordering across symbols.

    Merges events from multiple SymbolDataIterator and FundingDataIterator
    sources using a min-heap. Events are ordered by:
    1. Timestamp (chronological)
    2. Priority (funding before bars before spreads)
    3. Symbol (alphabetical for consistency)
    4. Interval (for bars)
    5. Sequence (tie-breaker within same source)

    Example:
        bar_iters = reader.bar_iterators()
        funding_iters = reader.funding_iterators()
        mux = EventMux(bar_iters, funding_iters)
        for event in mux:
            process_event(event)
    """

    def __init__(
        self,
        bar_iterators: dict[str, SymbolDataIterator] | None = None,
        funding_iterators: dict[str, FundingDataIterator] | None = None,
    ) -> None:
        """Initialize the event multiplexer.

        Args:
            bar_iterators: Dict of symbol -> SymbolDataIterator
            funding_iterators: Dict of symbol -> FundingDataIterator
        """
        self._sources: list[Iterator[BarEvent] | Iterator[FundingEvent]] = []
        self._heap: list[HeapEntry] = []
        self._counter = 0  # Tie-breaker for identical sort keys

        # Add bar iterators
        if bar_iterators:
            for iterator in bar_iterators.values():
                self._sources.append(iterator)

        # Add funding iterators
        if funding_iterators:
            for iterator in funding_iterators.values():
                self._sources.append(iterator)

        # Initialize heap with first event from each source
        self._initialize_heap()

    def _initialize_heap(self) -> None:
        """Initialize heap with first event from each source."""
        for idx, source in enumerate(self._sources):
            self._try_add_from_source(idx)

    def _try_add_from_source(self, source_idx: int) -> bool:
        """Try to add next event from source to heap.

        Returns True if an event was added, False if source is exhausted.
        """
        try:
            event = next(self._sources[source_idx])
            sort_key = self._make_sort_key(event)
            entry = HeapEntry(sort_key=sort_key, event=event, source_idx=source_idx)
            heapq.heappush(self._heap, entry)
            return True
        except StopIteration:
            return False

    def _make_sort_key(self, event: BacktestEvent) -> tuple:
        """Create sort key for an event.

        Returns tuple: (timestamp, priority, symbol, interval, sequence, counter)
        """
        self._counter += 1

        if isinstance(event, BarEvent):
            return (
                event.timestamp,
                event.priority,
                event.symbol,
                event.interval,
                event.sequence,
                self._counter,
            )
        elif isinstance(event, FundingEvent):
            return (
                event.funding_time,
                event.priority,
                event.symbol,
                "",  # No interval for funding
                event.sequence,
                self._counter,
            )
        else:
            # SpreadEvent, UniverseEvent, etc.
            return (
                event.timestamp,
                event.priority,
                getattr(event, "symbol", ""),
                "",
                event.sequence,
                self._counter,
            )

    def __iter__(self) -> Iterator[BacktestEvent]:
        return self

    def __next__(self) -> BacktestEvent:
        if not self._heap:
            raise StopIteration

        entry = heapq.heappop(self._heap)

        # Refill from the same source
        self._try_add_from_source(entry.source_idx)

        return entry.event

    def peek(self) -> BacktestEvent | None:
        """Peek at the next event without consuming it."""
        if not self._heap:
            return None
        return self._heap[0].event

    def peek_timestamp(self) -> datetime | None:
        """Peek at the timestamp of the next event."""
        if not self._heap:
            return None
        event = self._heap[0].event
        if isinstance(event, FundingEvent):
            return event.funding_time
        return event.timestamp

    def is_empty(self) -> bool:
        """Check if there are no more events."""
        return len(self._heap) == 0


def group_events_by_timestamp(
    mux: EventMux,
) -> Iterator[tuple[datetime, list[BacktestEvent]]]:
    """Group events by timestamp for cross-sectional processing.

    Yields groups of events that share the same timestamp, allowing
    cross-sectional analysis (e.g., ranking signals across symbols).

    Events within each group are already sorted by priority.

    Example:
        for timestamp, events in group_events_by_timestamp(mux):
            funding_events = [e for e in events if isinstance(e, FundingEvent)]
            bar_events = [e for e in events if isinstance(e, BarEvent)]
            process_funding(funding_events)
            process_bars(bar_events)

    Yields:
        Tuples of (timestamp, list of events at that timestamp)
    """
    current_ts: datetime | None = None
    current_group: list[BacktestEvent] = []

    for event in mux:
        # Get timestamp
        if isinstance(event, FundingEvent):
            event_ts = event.funding_time
        else:
            event_ts = event.timestamp

        if current_ts is None:
            current_ts = event_ts
            current_group = [event]
        elif event_ts == current_ts:
            current_group.append(event)
        else:
            # Yield previous group and start new one
            yield current_ts, current_group
            current_ts = event_ts
            current_group = [event]

    # Yield final group
    if current_ts is not None and current_group:
        yield current_ts, current_group


def separate_events_by_type(
    events: list[BacktestEvent],
) -> tuple[list[FundingEvent], list[BarEvent], list[BacktestEvent]]:
    """Separate events by type for ordered processing.

    Returns:
        Tuple of (funding_events, bar_events, other_events)
    """
    funding: list[FundingEvent] = []
    bars: list[BarEvent] = []
    other: list[BacktestEvent] = []

    for event in events:
        if isinstance(event, FundingEvent):
            funding.append(event)
        elif isinstance(event, BarEvent):
            bars.append(event)
        else:
            other.append(event)

    return funding, bars, other


def get_bars_by_symbol(bar_events: list[BarEvent]) -> dict[str, BarEvent]:
    """Get latest bar per symbol from a list of bar events.

    Useful for cross-sectional analysis where you want one bar per symbol.
    If multiple bars exist for a symbol (different intervals), returns the last one.

    Returns:
        Dict mapping symbol to its BarEvent
    """
    result: dict[str, BarEvent] = {}
    for bar in bar_events:
        result[bar.symbol] = bar
    return result
