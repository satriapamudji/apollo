"""Append-only event ledger for event sourcing."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

import orjson

from src.ledger.events import Event, EventType, new_event


class EventLedger:
    """Append-only event store with sequence tracking."""

    def __init__(self, ledger_path: str) -> None:
        self.ledger_path = Path(ledger_path)
        self.ledger_path.mkdir(parents=True, exist_ok=True)
        self.events_file = self.ledger_path / "events.jsonl"
        self.sequence_file = self.ledger_path / "sequence.txt"
        self._sequence = self._load_sequence()

    def _load_sequence(self) -> int:
        if self.sequence_file.exists():
            try:
                seq = int(self.sequence_file.read_text().strip())
            except ValueError:
                seq = 0
            # If multiple processes touched the ledger, `sequence.txt` can be stale/backwards.
            # Prefer the highest observed sequence between the file and the event log tail.
            if self.events_file.exists():
                seq = max(seq, self._read_last_sequence())
            return seq
        if not self.events_file.exists():
            return 0
        return self._read_last_sequence()

    def _read_last_sequence(self) -> int:
        try:
            with open(self.events_file, "rb") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                if size == 0:
                    return 0
                offset = min(size, 4096)
                handle.seek(-offset, os.SEEK_END)
                chunk = handle.read(offset)
            lines = chunk.splitlines()
            if not lines:
                return 0
            last = orjson.loads(lines[-1])
            return int(last.get("sequence_num", 0))
        except OSError:
            return 0

    def _persist_sequence(self) -> None:
        self.sequence_file.write_text(str(self._sequence))

    def _next_sequence(self) -> int:
        self._sequence += 1
        self._persist_sequence()
        return self._sequence

    def append(
        self,
        event_type: EventType,
        payload: dict,
        metadata: dict | None = None,
    ) -> Event:
        """Create and append a new event, then return it."""
        event = new_event(event_type, payload, self._next_sequence(), metadata)
        self.append_event(event)
        return event

    def append_event(self, event: Event) -> None:
        """Append an existing event to the ledger."""
        payload = orjson.dumps(event.to_dict())
        with open(self.events_file, "ab") as handle:
            handle.write(payload + b"\n")

    def iter_events(self) -> Iterable[Event]:
        """Iterate all events from the ledger."""
        if not self.events_file.exists():
            return
        with open(self.events_file, "rb") as handle:
            for line in handle:
                if not line.strip():
                    continue
                yield Event.from_dict(orjson.loads(line))

    def load_all(self) -> list[Event]:
        """Load all events into memory."""
        return list(self.iter_events())
