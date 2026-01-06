"""Tests for EventMux deterministic event ordering."""

from datetime import datetime, timezone

import pytest

from src.backtester.events import BarEvent, EventPriority, FundingEvent
from src.backtester.event_mux import (
    EventMux,
    HeapEntry,
    get_bars_by_symbol,
    group_events_by_timestamp,
    separate_events_by_type,
)


def make_bar(
    symbol: str,
    ts: datetime,
    close: float = 100.0,
    sequence: int = 0,
) -> BarEvent:
    """Create a test BarEvent."""
    return BarEvent(
        symbol=symbol,
        interval="4h",
        timestamp=ts,
        open=close - 1,
        high=close + 1,
        low=close - 2,
        close=close,
        volume=1000.0,
        sequence=sequence,
    )


def make_funding(
    symbol: str,
    ts: datetime,
    rate: float = 0.0001,
    sequence: int = 0,
) -> FundingEvent:
    """Create a test FundingEvent."""
    return FundingEvent(
        symbol=symbol,
        funding_time=ts,
        rate=rate,
        mark_price=100.0,
        sequence=sequence,
    )


class TestEventPriority:
    """Test EventPriority ordering."""

    def test_funding_before_bar(self) -> None:
        """Funding events should have lower priority (processed first)."""
        assert EventPriority.FUNDING < EventPriority.BAR_CLOSE

    def test_bar_before_spread(self) -> None:
        """Bar events should be processed before spread events."""
        assert EventPriority.BAR_CLOSE < EventPriority.SPREAD

    def test_priority_order(self) -> None:
        """Full priority ordering."""
        assert EventPriority.FUNDING < EventPriority.BAR_CLOSE
        assert EventPriority.BAR_CLOSE < EventPriority.SPREAD
        assert EventPriority.SPREAD < EventPriority.STRATEGY
        assert EventPriority.STRATEGY < EventPriority.RISK
        assert EventPriority.RISK < EventPriority.EXECUTION


class TestHeapEntry:
    """Test HeapEntry comparison for heap operations."""

    def test_sort_by_timestamp(self) -> None:
        """Earlier timestamp should sort first."""
        ts1 = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
        ts2 = datetime(2024, 1, 1, 4, 0, tzinfo=timezone.utc)

        bar1 = make_bar("BTCUSDT", ts1)
        bar2 = make_bar("BTCUSDT", ts2)

        entry1 = HeapEntry(sort_key=bar1.sort_key() + (0,), event=bar1, source_idx=0)
        entry2 = HeapEntry(sort_key=bar2.sort_key() + (0,), event=bar2, source_idx=0)

        assert entry1 < entry2

    def test_same_timestamp_priority_ordering(self) -> None:
        """At same timestamp, funding should sort before bar."""
        ts = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)

        bar = make_bar("BTCUSDT", ts)
        funding = make_funding("BTCUSDT", ts)

        bar_key = bar.sort_key() + (0,)
        funding_key = funding.sort_key() + (0,)

        bar_entry = HeapEntry(sort_key=bar_key, event=bar, source_idx=0)
        funding_entry = HeapEntry(sort_key=funding_key, event=funding, source_idx=0)

        assert funding_entry < bar_entry

    def test_same_timestamp_symbol_ordering(self) -> None:
        """At same timestamp and priority, sort by symbol alphabetically."""
        ts = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)

        bar_btc = make_bar("BTCUSDT", ts)
        bar_eth = make_bar("ETHUSDT", ts)

        btc_key = bar_btc.sort_key() + (0,)
        eth_key = bar_eth.sort_key() + (0,)

        btc_entry = HeapEntry(sort_key=btc_key, event=bar_btc, source_idx=0)
        eth_entry = HeapEntry(sort_key=eth_key, event=bar_eth, source_idx=0)

        assert btc_entry < eth_entry


class TestEventMux:
    """Test EventMux heap-merge ordering."""

    def test_empty_mux(self) -> None:
        """Empty mux should iterate without error."""
        mux = EventMux()
        events = list(mux)
        assert events == []
        assert mux.is_empty()

    def test_single_symbol_ordering(self) -> None:
        """Single symbol events should be in chronological order."""

        class MockIterator:
            def __init__(self, events):
                self._events = iter(events)

            def __iter__(self):
                return self

            def __next__(self):
                return next(self._events)

        ts1 = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
        ts2 = datetime(2024, 1, 1, 4, 0, tzinfo=timezone.utc)
        ts3 = datetime(2024, 1, 1, 8, 0, tzinfo=timezone.utc)

        bars = [make_bar("BTCUSDT", ts1), make_bar("BTCUSDT", ts2), make_bar("BTCUSDT", ts3)]
        mock_iter = MockIterator(bars)

        mux = EventMux(bar_iterators={"BTCUSDT": mock_iter})
        events = list(mux)

        assert len(events) == 3
        assert events[0].timestamp == ts1
        assert events[1].timestamp == ts2
        assert events[2].timestamp == ts3

    def test_multi_symbol_interleaving(self) -> None:
        """Events from multiple symbols should interleave by timestamp."""

        class MockIterator:
            def __init__(self, events):
                self._events = iter(events)

            def __iter__(self):
                return self

            def __next__(self):
                return next(self._events)

        ts1 = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
        ts2 = datetime(2024, 1, 1, 4, 0, tzinfo=timezone.utc)

        btc_bars = [make_bar("BTCUSDT", ts1), make_bar("BTCUSDT", ts2)]
        eth_bars = [make_bar("ETHUSDT", ts1), make_bar("ETHUSDT", ts2)]

        mux = EventMux(
            bar_iterators={
                "BTCUSDT": MockIterator(btc_bars),
                "ETHUSDT": MockIterator(eth_bars),
            }
        )
        events = list(mux)

        assert len(events) == 4
        # At ts1: BTCUSDT before ETHUSDT (alphabetical)
        assert events[0].symbol == "BTCUSDT" and events[0].timestamp == ts1
        assert events[1].symbol == "ETHUSDT" and events[1].timestamp == ts1
        # At ts2: same ordering
        assert events[2].symbol == "BTCUSDT" and events[2].timestamp == ts2
        assert events[3].symbol == "ETHUSDT" and events[3].timestamp == ts2

    def test_funding_before_bars_same_timestamp(self) -> None:
        """Funding events should be processed before bar events at same timestamp."""

        class MockBarIterator:
            def __init__(self, events):
                self._events = iter(events)

            def __iter__(self):
                return self

            def __next__(self):
                return next(self._events)

        class MockFundingIterator:
            def __init__(self, events):
                self._events = iter(events)

            def __iter__(self):
                return self

            def __next__(self):
                return next(self._events)

        ts = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)

        bars = [make_bar("BTCUSDT", ts)]
        funding = [make_funding("BTCUSDT", ts)]

        mux = EventMux(
            bar_iterators={"BTCUSDT": MockBarIterator(bars)},
            funding_iterators={"BTCUSDT": MockFundingIterator(funding)},
        )
        events = list(mux)

        assert len(events) == 2
        assert isinstance(events[0], FundingEvent)  # Funding first
        assert isinstance(events[1], BarEvent)  # Bar second

    def test_peek_without_consuming(self) -> None:
        """Peek should return next event without consuming it."""

        class MockIterator:
            def __init__(self, events):
                self._events = iter(events)

            def __iter__(self):
                return self

            def __next__(self):
                return next(self._events)

        ts1 = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
        bars = [make_bar("BTCUSDT", ts1)]

        mux = EventMux(bar_iterators={"BTCUSDT": MockIterator(bars)})

        # Peek multiple times
        peeked1 = mux.peek()
        peeked2 = mux.peek()
        assert peeked1 is peeked2

        # Consume
        consumed = next(mux)
        assert consumed.timestamp == ts1

        # Should be empty now
        assert mux.peek() is None
        assert mux.is_empty()


class TestGroupEventsByTimestamp:
    """Test grouping events by timestamp."""

    def test_group_single_timestamp(self) -> None:
        """Events at single timestamp should form one group."""

        class MockIterator:
            def __init__(self, events):
                self._events = iter(events)

            def __iter__(self):
                return self

            def __next__(self):
                return next(self._events)

        ts = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
        bars = [make_bar("BTCUSDT", ts), make_bar("ETHUSDT", ts)]

        mux = EventMux(
            bar_iterators={
                "BTCUSDT": MockIterator([bars[0]]),
                "ETHUSDT": MockIterator([bars[1]]),
            }
        )

        groups = list(group_events_by_timestamp(mux))
        assert len(groups) == 1
        group_ts, group_events = groups[0]
        assert group_ts == ts
        assert len(group_events) == 2

    def test_group_multiple_timestamps(self) -> None:
        """Events at different timestamps should form separate groups."""

        class MockIterator:
            def __init__(self, events):
                self._events = iter(events)

            def __iter__(self):
                return self

            def __next__(self):
                return next(self._events)

        ts1 = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
        ts2 = datetime(2024, 1, 1, 4, 0, tzinfo=timezone.utc)

        bars = [make_bar("BTCUSDT", ts1), make_bar("BTCUSDT", ts2)]
        mux = EventMux(bar_iterators={"BTCUSDT": MockIterator(bars)})

        groups = list(group_events_by_timestamp(mux))
        assert len(groups) == 2
        assert groups[0][0] == ts1
        assert groups[1][0] == ts2


class TestSeparateEventsByType:
    """Test event type separation helper."""

    def test_separate_mixed_events(self) -> None:
        """Should correctly separate funding and bar events."""
        ts = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)

        events = [
            make_funding("BTCUSDT", ts),
            make_bar("BTCUSDT", ts),
            make_funding("ETHUSDT", ts),
            make_bar("ETHUSDT", ts),
        ]

        funding, bars, other = separate_events_by_type(events)

        assert len(funding) == 2
        assert len(bars) == 2
        assert len(other) == 0
        assert all(isinstance(e, FundingEvent) for e in funding)
        assert all(isinstance(e, BarEvent) for e in bars)


class TestGetBarsBySymbol:
    """Test bars-by-symbol helper."""

    def test_get_bars_by_symbol(self) -> None:
        """Should return dict mapping symbol to bar."""
        ts = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)

        bars = [make_bar("BTCUSDT", ts, close=100), make_bar("ETHUSDT", ts, close=50)]

        result = get_bars_by_symbol(bars)

        assert len(result) == 2
        assert result["BTCUSDT"].close == 100
        assert result["ETHUSDT"].close == 50


class TestDeterminism:
    """Test that EventMux produces deterministic output."""

    def test_deterministic_ordering(self) -> None:
        """Same input should always produce same output."""

        class MockIterator:
            def __init__(self, events):
                self._events = iter(events)

            def __iter__(self):
                return self

            def __next__(self):
                return next(self._events)

        ts1 = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
        ts2 = datetime(2024, 1, 1, 4, 0, tzinfo=timezone.utc)

        def create_events():
            return {
                "BTCUSDT": MockIterator([make_bar("BTCUSDT", ts1), make_bar("BTCUSDT", ts2)]),
                "ETHUSDT": MockIterator([make_bar("ETHUSDT", ts1), make_bar("ETHUSDT", ts2)]),
            }

        # Run twice
        mux1 = EventMux(bar_iterators=create_events())
        result1 = [(e.symbol, e.timestamp) for e in mux1]

        mux2 = EventMux(bar_iterators=create_events())
        result2 = [(e.symbol, e.timestamp) for e in mux2]

        # Should be identical
        assert result1 == result2
