"""Tests for alert webhook functionality."""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from src.ledger.events import Event, EventType
from src.monitoring.alert_webhooks import (
    AlertWebhookHandler,
    _compute_reason_hash,
    _format_timestamp,
)


def _create_event(
    event_type: EventType,
    payload: dict | None = None,
    metadata: dict | None = None,
    sequence_num: int = 1,
) -> Event:
    """Helper to create a test event."""
    return Event(
        event_id="test-event-id",
        event_type=event_type,
        timestamp=datetime(2026, 1, 6, 12, 0, 0, tzinfo=timezone.utc),
        sequence_num=sequence_num,
        payload=payload or {},
        metadata=metadata or {},
    )


class TestAlertWebhookHandler:
    """Tests for AlertWebhookHandler."""

    def test_init_with_defaults(self) -> None:
        """Test handler initialization with default values."""
        handler = AlertWebhookHandler(webhook_urls=["https://example.com/webhook"])
        assert handler.webhook_urls == ["https://example.com/webhook"]
        assert handler.dedup_window_sec == 300
        assert handler.run_mode == "unknown"
        assert handler._seen_alerts == set()

    def test_init_with_custom_values(self) -> None:
        """Test handler initialization with custom values."""
        handler = AlertWebhookHandler(
            webhook_urls=["https://hooks.slack.com/xxx", "https://discord.com/xxx"],
            dedup_window_sec=600,
            run_mode="live",
        )
        assert len(handler.webhook_urls) == 2
        assert handler.dedup_window_sec == 600
        assert handler.run_mode == "live"

    @pytest.mark.asyncio
    async def test_handle_event_with_no_webhooks(self) -> None:
        """Test that no errors occur when no webhooks are configured."""
        handler = AlertWebhookHandler(webhook_urls=[])
        event = _create_event(EventType.MANUAL_INTERVENTION, {"action": "TEST"})
        # Should not raise
        await handler.handle_event(event)

    @pytest.mark.asyncio
    async def test_create_payload_structure(self) -> None:
        """Test that payload has correct structure."""
        handler = AlertWebhookHandler(webhook_urls=["https://example.com"], run_mode="paper")
        event = _create_event(
            EventType.MANUAL_INTERVENTION,
            payload={
                "action": "HANDLER_EXCEPTION",
                "symbol": "BTCUSDT",
                "trade_id": "t-123",
                "event_id": "abc",
            },
            metadata={"source": "test"},
        )
        payload = handler._create_payload(event)

        assert payload["alert_type"] == "ManualInterventionDetected"
        assert payload["run_mode"] == "paper"
        assert payload["symbol"] == "BTCUSDT"
        assert payload["trade_id"] == "t-123"
        assert payload["reason"] == "HANDLER_EXCEPTION"
        assert payload["event_id"] == "test-event-id"
        assert "pointers" in payload
        assert payload["pointers"]["events"] == "data/ledger/events.jsonl"
        assert payload["pointers"]["orders"] == "logs/orders.csv"

    @pytest.mark.asyncio
    async def test_deduplicates_manual_intervention(self) -> None:
        """Test that duplicate manual intervention alerts are suppressed."""
        handler = AlertWebhookHandler(
            webhook_urls=["https://example.com"],
            dedup_window_sec=300,
        )
        event = _create_event(
            EventType.MANUAL_INTERVENTION,
            payload={"action": "RECONCILIATION_DISCREPANCY", "symbol": "BTCUSDT"},
        )

        # First event should be sent
        with patch.object(handler, "_dispatch", new_callable=AsyncMock) as mock_dispatch:
            await handler.handle_event(event)
            mock_dispatch.assert_called_once()

        # Same event should be deduplicated
        with patch.object(handler, "_dispatch", new_callable=AsyncMock) as mock_dispatch:
            await handler.handle_event(event)
            mock_dispatch.assert_not_called()

    @pytest.mark.asyncio
    async def test_circuit_breaker_not_deduplicated(self) -> None:
        """Test that circuit breaker alerts are not deduplicated."""
        handler = AlertWebhookHandler(
            webhook_urls=["https://example.com"],
            dedup_window_sec=300,
        )
        event = _create_event(
            EventType.CIRCUIT_BREAKER_TRIGGERED,
            payload={"reason": "MAX_DRAWDOWN"},
        )

        # Both events should be sent (no deduplication for circuit breaker)
        with patch.object(handler, "_dispatch", new_callable=AsyncMock) as mock_dispatch:
            await handler.handle_event(event)
            await handler.handle_event(event)
            assert mock_dispatch.call_count == 2

    @pytest.mark.asyncio
    async def test_sends_alert_to_webhook(self) -> None:
        """Test that alerts are sent to configured webhooks."""
        handler = AlertWebhookHandler(
            webhook_urls=["https://example.com/webhook"],
            run_mode="live",
        )
        event = _create_event(
            EventType.CIRCUIT_BREAKER_TRIGGERED,
            payload={"reason": "MAX_DRAWDOWN", "drawdown_pct": 15.0},
        )

        with patch("aiohttp.ClientSession") as mock_session:
            mock_response = AsyncMock()
            mock_response.status = 200
            # Set up the mock chain
            mock_post_ctx = mock_session.return_value.__aenter__.return_value.post
            mock_post_ctx.return_value.__aenter__.return_value = mock_response

            await handler.handle_event(event)

            mock_session.assert_called_once()
            call_args = mock_session.return_value.__aenter__.return_value.post.call_args
            assert call_args is not None

    @pytest.mark.asyncio
    async def test_format_slack_message(self) -> None:
        """Test Slack message formatting."""
        handler = AlertWebhookHandler(webhook_urls=["https://hooks.slack.com/xxx"])
        event = _create_event(
            EventType.MANUAL_INTERVENTION,
            payload={"action": "DISCREPANCY", "symbol": "ETHUSDT"},
        )
        payload = handler._create_payload(event)
        slack_message = handler._format_slack(payload)

        assert "attachments" in slack_message
        assert len(slack_message["attachments"]) == 1
        assert "blocks" in slack_message["attachments"][0]
        assert slack_message["attachments"][0]["color"] == "warning"

    @pytest.mark.asyncio
    async def test_format_discord_message(self) -> None:
        """Test Discord message formatting."""
        handler = AlertWebhookHandler(webhook_urls=["https://discord.com/api/webhooks/xxx"])
        event = _create_event(
            EventType.CIRCUIT_BREAKER_TRIGGERED,
            payload={"reason": "MAX_DRAWDOWN", "symbol": "BTCUSDT"},
        )
        payload = handler._create_payload(event)
        discord_message = handler._format_discord(payload)

        assert "embeds" in discord_message
        assert len(discord_message["embeds"]) == 1
        assert discord_message["embeds"][0]["color"] == 0xFF0000  # Red for circuit breaker

    @pytest.mark.asyncio
    async def test_handles_webhook_failure_gracefully(self) -> None:
        """Test that webhook failures don't crash the handler."""
        handler = AlertWebhookHandler(
            webhook_urls=["https://example.com"],
            run_mode="paper",
        )
        event = _create_event(
            EventType.MANUAL_INTERVENTION,
            payload={"action": "TEST"},
        )

        with patch("aiohttp.ClientSession") as mock_session:
            mock_response = AsyncMock()
            mock_response.status = 500
            mock_response.text = AsyncMock(return_value="Internal Server Error")
            # Set up the mock chain
            mock_post_ctx = mock_session.return_value.__aenter__.return_value.post
            mock_post_ctx.return_value.__aenter__.return_value = mock_response

            # Should not raise
            await handler.handle_event(event)

    @pytest.mark.asyncio
    async def test_multiple_webhooks(self) -> None:
        """Test that alerts are sent to all configured webhooks."""
        handler = AlertWebhookHandler(
            webhook_urls=["https://webhook1.com", "https://webhook2.com"],
            run_mode="testnet",
        )
        event = _create_event(
            EventType.MANUAL_INTERVENTION,
            payload={"action": "RECONCILIATION"},
        )

        with patch("aiohttp.ClientSession") as mock_session:
            mock_response = AsyncMock()
            mock_response.status = 200
            # Set up the mock chain
            mock_post_ctx = mock_session.return_value.__aenter__.return_value.post
            mock_post_ctx.return_value.__aenter__.return_value = mock_response

            await handler.handle_event(event)

            # Should have been called twice (once per webhook)
            assert mock_session.return_value.__aenter__.return_value.post.call_count == 2


class TestHelperFunctions:
    """Tests for helper functions."""

    def test_format_timestamp(self) -> None:
        """Test timestamp formatting."""
        ts = datetime(2026, 1, 6, 12, 0, 0, tzinfo=timezone.utc)
        formatted = _format_timestamp(ts)
        assert formatted == "2026-01-06T12:00:00.000Z"

    def test_format_timestamp_with_microseconds(self) -> None:
        """Test timestamp formatting with microseconds."""
        ts = datetime(2026, 1, 6, 12, 0, 0, 123456, tzinfo=timezone.utc)
        formatted = _format_timestamp(ts)
        assert formatted == "2026-01-06T12:00:00.123Z"

    def test_compute_reason_hash(self) -> None:
        """Test reason hash computation."""
        hash1 = _compute_reason_hash("HANDLER_EXCEPTION")
        hash2 = _compute_reason_hash("HANDLER_EXCEPTION")
        hash3 = _compute_reason_hash("RECONCILIATION")

        # Same input should produce same hash
        assert hash1 == hash2
        # Different input should produce different hash
        assert hash1 != hash3
        # Hash should be 8 characters
        assert len(hash1) == 8


class TestDedupWindow:
    """Tests for deduplication window behavior."""

    @pytest.mark.asyncio
    async def test_different_actions_not_deduplicated(self) -> None:
        """Test that different actions create different dedup keys."""
        handler = AlertWebhookHandler(
            webhook_urls=["https://example.com"],
            dedup_window_sec=300,
        )
        event1 = _create_event(
            EventType.MANUAL_INTERVENTION,
            payload={"action": "TEST_ACTION_1", "symbol": "BTCUSDT"},
            sequence_num=1,
        )
        event2 = _create_event(
            EventType.MANUAL_INTERVENTION,
            payload={"action": "TEST_ACTION_2", "symbol": "BTCUSDT"},
            sequence_num=2,
        )

        with patch.object(handler, "_dispatch", new_callable=AsyncMock) as mock_dispatch:
            await handler.handle_event(event1)
            assert mock_dispatch.call_count == 1

            # Different action should create different dedup key
            await handler.handle_event(event2)
            assert mock_dispatch.call_count == 2

    @pytest.mark.asyncio
    async def test_cleanup_expired_entries(self) -> None:
        """Test that expired entries are cleaned up."""
        handler = AlertWebhookHandler(
            webhook_urls=["https://example.com"],
            dedup_window_sec=1,
        )

        # Add an entry manually with an expired timestamp (from 2020)
        key = ("ManualInterventionDetected", "ETHUSDT", _compute_reason_hash("CLEANUP_TEST"))
        handler._seen_timestamps[key] = datetime(2020, 1, 1, tzinfo=timezone.utc)
        handler._seen_alerts.add(key)

        # Entry should be in both before cleanup
        assert key in handler._seen_alerts
        assert key in handler._seen_timestamps

        # Trigger cleanup
        handler._cleanup_expired()

        # Entry should now be removed from both
        assert key not in handler._seen_alerts
        assert key not in handler._seen_timestamps
