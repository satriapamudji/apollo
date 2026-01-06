"""Persist pending entry context to survive process restarts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import orjson


class PendingEntryStore:
    """Persist pending entry context to ensure protective orders are placed even after restart."""

    def __init__(self, state_path: str | Path) -> None:
        self.state_path = Path(state_path)
        self.state_path.mkdir(parents=True, exist_ok=True)
        self._file = self.state_path / "pending_entries.jsonl"

    def save(self, client_order_id: str, context: dict[str, Any]) -> None:
        """Persist a pending entry context."""
        entries = self._load_all()
        entries[client_order_id] = context
        self._save_all(entries)

    def remove(self, client_order_id: str) -> None:
        """Remove a pending entry (when filled/cancelled)."""
        entries = self._load_all()
        entries.pop(client_order_id, None)
        self._save_all(entries)

    def load_all(self) -> dict[str, dict[str, Any]]:
        """Load all persisted pending entries on startup."""
        return self._load_all()

    def clear(self) -> None:
        """Clear all persisted entries. Used after successful recovery validation."""
        if self._file.exists():
            self._file.unlink()

    def _load_all(self) -> dict[str, dict[str, Any]]:
        if not self._file.exists():
            return {}
        try:
            with open(self._file, "rb") as f:
                data = orjson.loads(f.read())
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save_all(self, entries: dict[str, dict[str, Any]]) -> None:
        with open(self._file, "wb") as f:
            f.write(orjson.dumps(entries))
