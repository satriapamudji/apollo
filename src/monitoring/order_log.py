"""Order CSV logger."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from src.ledger.events import Event, EventType


class OrderLogger:
    """Append order lifecycle events to a CSV file."""

    _SUPPORTED = {
        EventType.ORDER_PLACED,
        EventType.ORDER_PARTIAL_FILL,
        EventType.ORDER_FILLED,
        EventType.ORDER_CANCELLED,
    }

    def __init__(self, log_path: str) -> None:
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_header()

    def handle_event(self, event: Event) -> None:
        if event.event_type not in self._SUPPORTED:
            return
        payload = event.payload
        metadata = event.metadata or {}
        self._append_row(
            {
                "timestamp": event.timestamp.isoformat(),
                "event_type": event.event_type.value,
                "trade_id": metadata.get("trade_id", ""),
                "symbol": payload.get("symbol", ""),
                "side": payload.get("side", ""),
                "order_type": payload.get("order_type", payload.get("type", "")),
                "client_order_id": payload.get("client_order_id", payload.get("clientOrderId", "")),
                "order_id": payload.get("order_id", payload.get("orderId", "")),
                "quantity": payload.get("quantity", ""),
                "price": payload.get("price", ""),
                "stop_price": payload.get("stop_price", payload.get("stopPrice", "")),
                "reduce_only": payload.get("reduce_only", payload.get("reduceOnly", "")),
                "reason": payload.get("reason", ""),
                "cancel_error": payload.get("cancel_error", ""),
            }
        )

    def _ensure_header(self) -> None:
        if self.log_path.exists():
            return
        with open(self.log_path, "w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=self._fieldnames())
            writer.writeheader()

    def _append_row(self, row: dict[str, Any]) -> None:
        with open(self.log_path, "a", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=self._fieldnames())
            writer.writerow(row)

    @staticmethod
    def _fieldnames() -> list[str]:
        return [
            "timestamp",
            "event_type",
            "trade_id",
            "symbol",
            "side",
            "order_type",
            "client_order_id",
            "order_id",
            "quantity",
            "price",
            "stop_price",
            "reduce_only",
            "reason",
            "cancel_error",
        ]

