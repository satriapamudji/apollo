"""Alert webhook notifications for critical system events."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime, timezone
from typing import Any

import aiohttp
import structlog

from src.ledger.events import Event, EventType

_log = structlog.get_logger(__name__)

# Default paths for log pointers
DEFAULT_EVENTS_PATH = "data/ledger/events.jsonl"
DEFAULT_ORDERS_PATH = "logs/orders.csv"
DEFAULT_TRADES_PATH = "logs/trades.csv"


def _format_timestamp(ts: datetime) -> str:
    """Format timestamp as ISO-8601 with Z suffix."""
    return ts.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _compute_reason_hash(reason: str) -> str:
    """Compute a short hash for deduplication."""
    return hashlib.md5(reason.encode()).hexdigest()[:8]


class AlertWebhookHandler:
    """Send operator alerts for critical events via webhooks.

    Handles:
    - ManualInterventionDetected
    - CircuitBreakerTriggered
    - Repeated REST failures / WS disconnect storms
    - Protective order missing

    Features:
    - Deduplication to prevent alert storms
    - JSON payloads with run mode, symbol, trade_id, reason
    - Optional Slack/Discord formatting
    - Graceful failure handling
    """

    def __init__(
        self,
        webhook_urls: list[str],
        dedup_window_sec: int = 300,
        run_mode: str = "unknown",
    ) -> None:
        """Initialize the alert webhook handler.

        Args:
            webhook_urls: List of webhook URLs to send alerts to
            dedup_window_sec: Deduplication window in seconds (default 5 min)
            run_mode: Current run mode (paper/testnet/live)
        """
        self.webhook_urls = webhook_urls
        self.dedup_window_sec = dedup_window_sec
        self.run_mode = run_mode
        self._seen_alerts: set[tuple[str, str, str]] = set()
        self._seen_timestamps: dict[tuple[str, str, str], datetime] = {}
        self._log = structlog.get_logger(__name__)

    def _get_dedup_key(self, event: Event) -> tuple[str, str, str] | None:
        """Generate a deduplication key for the event.

        Returns None if the event type should not be deduplicated.
        """
        event_type = event.event_type.value

        # Only deduplicate MANUAL_INTERVENTION events
        if event_type != EventType.MANUAL_INTERVENTION.value:
            return None

        # Extract key identifiers from payload
        symbol = event.payload.get("symbol", "unknown")
        reason = event.payload.get("action", event.payload.get("reason", "unknown"))
        reason_hash = _compute_reason_hash(reason)

        return (event_type, symbol, reason_hash)

    def _is_duplicate(self, dedup_key: tuple[str, str, str]) -> bool:
        """Check if an alert was already sent within the deduplication window."""
        if dedup_key is None:
            return False

        now = datetime.now(timezone.utc)
        if dedup_key in self._seen_alerts:
            return True

        # Check timestamp
        if dedup_key in self._seen_timestamps:
            elapsed = (now - self._seen_timestamps[dedup_key]).total_seconds()
            if elapsed < self.dedup_window_sec:
                return True

        return False

    def _record_alert(self, dedup_key: tuple[str, str, str]) -> None:
        """Record that an alert was sent."""
        if dedup_key is None:
            return
        self._seen_alerts.add(dedup_key)
        self._seen_timestamps[dedup_key] = datetime.now(timezone.utc)

    def _cleanup_expired(self) -> None:
        """Remove expired entries from deduplication tracking."""
        now = datetime.now(timezone.utc)
        expired = [
            key
            for key, ts in self._seen_timestamps.items()
            if (now - ts).total_seconds() >= self.dedup_window_sec
        ]
        for key in expired:
            self._seen_alerts.discard(key)
            self._seen_timestamps.pop(key, None)

    def _create_payload(self, event: Event) -> dict[str, Any]:
        """Create a JSON payload for the alert.

        Includes: run mode, symbol, trade_id, reason, and pointers to logs.
        """
        return {
            "alert_type": event.event_type.value,
            "run_mode": self.run_mode,
            "symbol": event.payload.get("symbol", "N/A"),
            "trade_id": event.payload.get("trade_id", event.metadata.get("trade_id", "")),
            "reason": event.payload.get("action", event.payload.get("reason", "N/A")),
            "timestamp": _format_timestamp(event.timestamp),
            "event_id": event.event_id,
            "sequence_num": event.sequence_num,
            "pointers": {
                "events": DEFAULT_EVENTS_PATH,
                "orders": DEFAULT_ORDERS_PATH,
                "trades": DEFAULT_TRADES_PATH,
            },
            "payload": event.payload,
            "metadata": event.metadata,
        }

    def _format_slack(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Format payload as a Slack message."""
        alert_type = payload["alert_type"]
        reason = payload["reason"]
        symbol = payload["symbol"]
        event_id = payload["event_id"][:8]  # Shorten event ID

        # Determine emoji and color based on alert type
        if alert_type == EventType.CIRCUIT_BREAKER_TRIGGERED.value:
            emoji = ":rotating_light:"
            color = "danger"
        else:
            emoji = ":warning:"
            color = "warning"

        return {
            "attachments": [
                {
                    "color": color,
                    "blocks": [
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": f"{emoji} *{alert_type}*\n*Reason:* {reason}",
                            },
                        },
                        {
                            "type": "section",
                            "fields": [
                                {"type": "mrkdwn", "text": f"*Symbol:*\n{symbol}"},
                                {"type": "mrkdwn", "text": f"*Run Mode:*\n{payload['run_mode']}"},
                                {"type": "mrkdwn", "text": f"*Event ID:*\n`{event_id}`"},
                                {"type": "mrkdwn", "text": f"*Time:*\n{payload['timestamp']}"},
                            ],
                        },
                        {
                            "type": "context",
                            "elements": [
                                {
                                    "type": "mrkdwn",
                                    "text": (
                                        f"Pointers: `{DEFAULT_EVENTS_PATH}` | "
                                        f"`{DEFAULT_ORDERS_PATH}`"
                                    ),
                                }
                            ],
                        },
                    ],
                }
            ],
        }

    def _format_discord(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Format payload as a Discord webhook message."""
        alert_type = payload["alert_type"]
        reason = payload["reason"]
        symbol = payload["symbol"]
        event_id = payload["event_id"][:8]

        # Determine color and emoji based on alert type
        if alert_type == EventType.CIRCUIT_BREAKER_TRIGGERED.value:
            color = 0xFF0000  # Red
            emoji = "🔴"
        else:
            color = 0xFFA500  # Orange
            emoji = "⚠️"

        return {
            "embeds": [
                {
                    "title": f"{emoji} {alert_type}",
                    "description": reason,
                    "color": color,
                    "fields": [
                        {"name": "Symbol", "value": symbol, "inline": True},
                        {"name": "Run Mode", "value": payload["run_mode"], "inline": True},
                        {"name": "Event ID", "value": f"`{event_id}`", "inline": True},
                        {"name": "Time", "value": payload["timestamp"], "inline": False},
                    ],
                    "footer": {"text": f"Event: {payload['event_id']}"},
                    "timestamp": payload["timestamp"],
                }
            ],
        }

    async def _send_webhook(self, url: str, payload: dict[str, Any]) -> None:
        """Send a webhook request."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as response:
                    if response.status >= 400:
                        text = await response.text()
                        self._log.error(
                            "webhook_failed",
                            url=url,
                            status=response.status,
                            response_body=text,
                        )
                    else:
                        self._log.debug("webhook_sent", url=url)
        except asyncio.TimeoutError:
            self._log.error("webhook_timeout", url=url)
        except Exception as exc:
            self._log.exception("webhook_error", url=url, error=str(exc))

    async def _dispatch(self, event: Event) -> None:
        """Dispatch alert to all configured webhooks."""
        if not self.webhook_urls:
            return

        base_payload = self._create_payload(event)

        # Send to each webhook URL
        tasks = []
        for url in self.webhook_urls:
            # Try to detect webhook type from URL
            if "slack" in url.lower():
                payload = self._format_slack(base_payload)
            elif "discord" in url.lower():
                payload = self._format_discord(base_payload)
            else:
                payload = base_payload

            tasks.append(self._send_webhook(url, payload))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def handle_event(self, event: Event) -> None:
        """Handle an event and send alert if applicable.

        This method is registered as an event handler on the EventBus.
        """
        # Clean up expired entries periodically
        if len(self._seen_timestamps) > 1000:
            self._cleanup_expired()

        # Get deduplication key
        dedup_key = self._get_dedup_key(event)

        # Check for duplicate (only for events that support deduplication)
        if dedup_key is not None and self._is_duplicate(dedup_key):
            self._log.debug("alert_suppressed_duplicate", event_type=event.event_type.value)
            return

        # Send alert
        await self._dispatch(event)

        # Record that we sent this alert (only for events that support deduplication)
        if dedup_key is not None:
            self._record_alert(dedup_key)

        self._log.info(
            "alert_sent",
            event_type=event.event_type.value,
            webhook_count=len(self.webhook_urls),
            deduplicated=(dedup_key is not None),
        )


async def send_test_alert(
    webhook_url: str,
    alert_type: str = "TEST",
    run_mode: str = "paper",
) -> bool:
    """Send a test alert to verify webhook connectivity.

    Args:
        webhook_url: The webhook URL to test
        alert_type: Type of alert (for display purposes)
        run_mode: Current run mode

    Returns:
        True if the alert was sent successfully, False otherwise
    """
    payload = {
        "alert_type": alert_type,
        "run_mode": run_mode,
        "symbol": "BTCUSDT",
        "trade_id": "test-123",
        "reason": "Test alert - connectivity check",
        "timestamp": _format_timestamp(datetime.now(timezone.utc)),
        "event_id": "test-connection",
        "sequence_num": 0,
        "pointers": {
            "events": DEFAULT_EVENTS_PATH,
            "orders": DEFAULT_ORDERS_PATH,
            "trades": DEFAULT_TRADES_PATH,
        },
        "payload": {},
        "metadata": {},
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                return response.status < 400
    except Exception:
        return False
