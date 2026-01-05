"""Trade CSV logger."""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Any

from src.ledger.events import Event, EventType


class TradeLogger:
    """
    Log trades to a CSV file.

    - Writes a row on `PositionOpened` (open trade with blank exit fields)
    - Updates that row on `PositionClosed`
    """

    def __init__(self, log_path: str) -> None:
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_header()

    def handle_event(self, event: Event) -> None:
        if event.event_type == EventType.POSITION_OPENED:
            self._handle_open(event)
        elif event.event_type == EventType.POSITION_CLOSED:
            self._handle_close(event)

    def _handle_open(self, event: Event) -> None:
        payload = event.payload
        trade_id = payload.get("trade_id") or f"{payload.get('symbol')}-{event.sequence_num}"
        if self._has_open_trade(trade_id):
            return
        self._append_row(
            {
                "trade_id": trade_id,
                "symbol": payload.get("symbol", ""),
                "side": payload.get("side", ""),
                "quantity": float(payload.get("quantity", 0)),
                "entry_price": float(payload.get("entry_price", 0)),
                "exit_price": "",
                "entry_time": event.timestamp.isoformat(),
                "exit_time": "",
                "holding_hours": "",
                "realized_pnl": "",
                "reason": "",
            }
        )

    def _handle_close(self, event: Event) -> None:
        payload = event.payload
        trade_id = payload.get("trade_id") or payload.get("tradeId")
        symbol = payload.get("symbol", "")
        side = payload.get("side", "")
        quantity = float(payload.get("quantity", 0))
        entry_price = float(payload.get("entry_price", 0))
        exit_price = float(payload.get("exit_price", 0))
        realized_pnl = float(payload.get("realized_pnl", 0))
        reason = payload.get("reason", "")
        updated = False
        if trade_id:
            updated = self._update_open_trade(
                trade_id=trade_id,
                exit_time=event.timestamp,
                exit_price=exit_price,
                realized_pnl=realized_pnl,
                reason=reason,
                fallback_entry_price=entry_price,
                fallback_symbol=symbol,
                fallback_side=side,
                fallback_quantity=quantity,
            )
        if not updated:
            self._append_row(
                {
                    "trade_id": trade_id or "",
                    "symbol": symbol,
                    "side": side,
                    "quantity": quantity,
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "entry_time": "",
                    "exit_time": event.timestamp.isoformat(),
                    "holding_hours": "",
                    "realized_pnl": realized_pnl,
                    "reason": reason,
                }
            )

    def _has_open_trade(self, trade_id: str) -> bool:
        if not self.log_path.exists():
            return False
        with open(self.log_path, newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                if row.get("trade_id") != trade_id:
                    continue
                if not (row.get("exit_time") or "").strip():
                    return True
        return False

    def _update_open_trade(
        self,
        trade_id: str,
        exit_time: datetime,
        exit_price: float,
        realized_pnl: float,
        reason: str,
        fallback_entry_price: float,
        fallback_symbol: str,
        fallback_side: str,
        fallback_quantity: float,
    ) -> bool:
        if not self.log_path.exists():
            return False
        with open(self.log_path, newline="") as handle:
            reader = csv.DictReader(handle)
            rows = list(reader)
        updated = False
        for row in rows:
            if row.get("trade_id") != trade_id:
                continue
            if (row.get("exit_time") or "").strip():
                continue
            entry_time_str = (row.get("entry_time") or "").strip()
            holding_hours = ""
            if entry_time_str:
                try:
                    entry_time = datetime.fromisoformat(entry_time_str.replace("Z", "+00:00"))
                    holding_hours = str(round((exit_time - entry_time).total_seconds() / 3600, 4))
                except ValueError:
                    holding_hours = ""
            row["symbol"] = (row.get("symbol") or "").strip() or fallback_symbol
            row["side"] = (row.get("side") or "").strip() or fallback_side
            row["quantity"] = (row.get("quantity") or "").strip() or str(fallback_quantity)
            row["entry_price"] = (row.get("entry_price") or "").strip() or str(fallback_entry_price)
            row["exit_price"] = str(exit_price)
            row["exit_time"] = exit_time.isoformat()
            row["holding_hours"] = holding_hours
            row["realized_pnl"] = str(realized_pnl)
            row["reason"] = reason
            updated = True
            break
        if not updated:
            return False
        with open(self.log_path, "w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=self._fieldnames())
            writer.writeheader()
            writer.writerows(rows)
        return True

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
            "trade_id",
            "symbol",
            "side",
            "quantity",
            "entry_price",
            "exit_price",
            "entry_time",
            "exit_time",
            "holding_hours",
            "realized_pnl",
            "reason",
        ]
