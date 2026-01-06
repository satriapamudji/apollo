"""Backtest event dataclasses for the event-driven replay engine.

Provides lightweight, type-safe event types for backtesting:
- BarEvent (CANDLE_CLOSE): bar close data
- FundingEvent (FUNDING_UPDATE): funding rate settlement
- SpreadEvent (SPREAD_SNAPSHOT): bid/ask spread (stub for Task 33)
- UniverseEvent (UNIVERSE_UPDATED): universe changes

Events use EventPriority for deterministic ordering within same timestamp.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum
from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from src.ledger.events import Event


class EventPriority(IntEnum):
    """Priority for deterministic event ordering within same timestamp.

    Lower values are processed first. Order:
    1. Funding settlements (affect equity before decisions)
    2. Bar closes (trigger signals)
    3. Spread snapshots (inform execution)
    4. Strategy decisions (generate proposals)
    5. Risk gating (evaluate proposals)
    6. Execution (fill trades)
    """

    FUNDING = 1
    BAR_CLOSE = 2
    SPREAD = 3
    STRATEGY = 4
    RISK = 5
    EXECUTION = 6


@dataclass(frozen=True, slots=True)
class BarEvent:
    """CANDLE_CLOSE event for bar-close replay.

    Represents the close of a single candle with OHLCV data.
    Indexed by close_time (bar-close convention).
    """

    symbol: str
    interval: str
    timestamp: datetime  # Bar close time (index time)
    open: float
    high: float
    low: float
    close: float
    volume: float
    open_time: datetime | None = None
    sequence: int = 0  # For tie-breaking within same timestamp

    @property
    def priority(self) -> EventPriority:
        return EventPriority.BAR_CLOSE

    def sort_key(self) -> tuple:
        """Deterministic sort key: (timestamp, priority, symbol, interval, sequence)."""
        return (self.timestamp, self.priority, self.symbol, self.interval, self.sequence)


@dataclass(frozen=True, slots=True)
class FundingEvent:
    """FUNDING_UPDATE event for funding rate settlement.

    Represents a funding settlement at a specific time.
    Funding is applied as discrete cashflow at settlement timestamps only.
    """

    symbol: str
    funding_time: datetime  # Settlement timestamp
    rate: float  # Funding rate as decimal (e.g., 0.0001 = 0.01%)
    mark_price: float | None = None
    sequence: int = 0

    @property
    def priority(self) -> EventPriority:
        return EventPriority.FUNDING

    @property
    def timestamp(self) -> datetime:
        return self.funding_time

    def sort_key(self) -> tuple:
        return (self.funding_time, self.priority, self.symbol, "", self.sequence)


@dataclass(frozen=True, slots=True)
class SpreadEvent:
    """SPREAD_SNAPSHOT event for spread-aware execution.

    Stub for Task 33 - provides interface for spread data.
    When spread data is present, execution models can reject
    trades if spread exceeds threshold.
    """

    symbol: str
    timestamp: datetime
    bid: float
    ask: float
    spread_bps: float
    sequence: int = 0

    @property
    def priority(self) -> EventPriority:
        return EventPriority.SPREAD

    def sort_key(self) -> tuple:
        return (self.timestamp, self.priority, self.symbol, "", self.sequence)


@dataclass(frozen=True, slots=True)
class UniverseEvent:
    """UNIVERSE_UPDATED event for dynamic universe changes.

    Represents changes to the tradable symbol universe.
    Processed early (with funding) to update universe before signals.
    """

    symbols: tuple[str, ...]
    timestamp: datetime
    sequence: int = 0

    @property
    def priority(self) -> EventPriority:
        return EventPriority.FUNDING  # Process early with funding

    def sort_key(self) -> tuple:
        return (self.timestamp, self.priority, "", "", self.sequence)


# Union type for all backtest events
BacktestEvent = BarEvent | FundingEvent | SpreadEvent | UniverseEvent


def to_ledger_event(bt_event: BacktestEvent, sequence_num: int) -> Event:
    """Convert backtest event to ledger Event for logging.

    Creates a ledger-compatible Event with the simulated timestamp
    (not wall-clock time). Use this when writing to BacktestLedger.

    Args:
        bt_event: Backtest event to convert
        sequence_num: Sequence number for the ledger event

    Returns:
        Event compatible with EventLedger.append_event()
    """
    from src.ledger.events import Event, EventType

    if isinstance(bt_event, BarEvent):
        return Event(
            event_id=str(uuid4()),
            event_type=EventType.CANDLE_CLOSE,
            timestamp=bt_event.timestamp,
            sequence_num=sequence_num,
            payload={
                "symbol": bt_event.symbol,
                "interval": bt_event.interval,
                "open": bt_event.open,
                "high": bt_event.high,
                "low": bt_event.low,
                "close": bt_event.close,
                "volume": bt_event.volume,
            },
        )
    elif isinstance(bt_event, FundingEvent):
        return Event(
            event_id=str(uuid4()),
            event_type=EventType.FUNDING_UPDATE,
            timestamp=bt_event.funding_time,
            sequence_num=sequence_num,
            payload={
                "symbol": bt_event.symbol,
                "funding_rate": bt_event.rate,
                "mark_price": bt_event.mark_price,
            },
        )
    elif isinstance(bt_event, SpreadEvent):
        return Event(
            event_id=str(uuid4()),
            event_type=EventType.MARKET_TICK,  # Closest match
            timestamp=bt_event.timestamp,
            sequence_num=sequence_num,
            payload={
                "symbol": bt_event.symbol,
                "bid": bt_event.bid,
                "ask": bt_event.ask,
                "spread_bps": bt_event.spread_bps,
            },
        )
    elif isinstance(bt_event, UniverseEvent):
        return Event(
            event_id=str(uuid4()),
            event_type=EventType.UNIVERSE_UPDATED,
            timestamp=bt_event.timestamp,
            sequence_num=sequence_num,
            payload={"symbols": list(bt_event.symbols)},
        )
    else:
        raise ValueError(f"Unknown backtest event type: {type(bt_event)}")
