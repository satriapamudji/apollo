"""Backtest ledger for optional event logging during backtests.

Provides an append-only event log that writes to a separate location
from the live ledger, using simulated timestamps instead of wall-clock time.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import orjson

from src.backtester.events import BacktestEvent, to_ledger_event

if TYPE_CHECKING:
    from src.ledger.events import Event


class BacktestLedger:
    """Append-only event ledger for backtest event logging.

    Similar to EventLedger but:
    - Uses simulated timestamps (from backtest events) instead of wall-clock
    - Writes to a separate location (out_dir/events.jsonl)
    - Lighter weight (no sequence file persistence during run)

    Example:
        ledger = BacktestLedger(out_dir="./data/backtests/run_001")
        ledger.append_backtest_event(bar_event)
        ledger.append_ledger_event(trade_filled_event)
        ledger.flush()
    """

    def __init__(
        self,
        out_dir: str | Path,
        buffer_size: int = 100,
    ) -> None:
        """Initialize the backtest ledger.

        Args:
            out_dir: Directory for backtest output
            buffer_size: Number of events to buffer before auto-flush
        """
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.events_file = self.out_dir / "events.jsonl"
        self.buffer_size = buffer_size

        self._sequence = 0
        self._buffer: list[bytes] = []
        self._event_count = 0

    def _next_sequence(self) -> int:
        """Get next sequence number."""
        self._sequence += 1
        return self._sequence

    def append_backtest_event(self, bt_event: BacktestEvent) -> Event:
        """Convert and append a backtest event.

        Args:
            bt_event: Backtest event (BarEvent, FundingEvent, etc.)

        Returns:
            The converted ledger Event
        """
        event = to_ledger_event(bt_event, self._next_sequence())
        self._write_event(event)
        return event

    def append_ledger_event(self, event: Event) -> None:
        """Append a pre-formed ledger event.

        Used for trade/order events that are already in ledger format.
        """
        self._write_event(event)

    def _write_event(self, event: Event) -> None:
        """Write event to buffer, flush if needed."""
        # Serialize event to JSON bytes
        event_dict = {
            "event_id": event.event_id,
            "event_type": event.event_type.value,
            "timestamp": event.timestamp.isoformat(),
            "sequence_num": event.sequence_num,
            "payload": event.payload,
        }
        if event.metadata:
            event_dict["metadata"] = event.metadata

        line = orjson.dumps(event_dict)
        self._buffer.append(line)
        self._event_count += 1

        if len(self._buffer) >= self.buffer_size:
            self.flush()

    def flush(self) -> None:
        """Flush buffered events to disk."""
        if not self._buffer:
            return

        with open(self.events_file, "ab") as f:
            for line in self._buffer:
                f.write(line)
                f.write(b"\n")

        self._buffer.clear()

    def close(self) -> None:
        """Flush and close the ledger."""
        self.flush()

        # Write final sequence to file for reference
        seq_file = self.out_dir / "sequence.txt"
        seq_file.write_text(str(self._sequence))

    @property
    def event_count(self) -> int:
        """Total number of events written."""
        return self._event_count

    def __enter__(self) -> BacktestLedger:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


class NullLedger:
    """No-op ledger for when event logging is disabled.

    Provides the same interface as BacktestLedger but discards all events.
    """

    def append_backtest_event(self, bt_event: BacktestEvent) -> None:
        """Discard event."""
        pass

    def append_ledger_event(self, event: Event) -> None:
        """Discard event."""
        pass

    def flush(self) -> None:
        """No-op."""
        pass

    def close(self) -> None:
        """No-op."""
        pass

    @property
    def event_count(self) -> int:
        return 0

    def __enter__(self) -> NullLedger:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass
