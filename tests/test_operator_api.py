"""Tests for the Operator API."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.api.operator import create_app


@pytest.fixture
def client() -> TestClient:
    """Create a test client for the API."""
    app = create_app()
    return TestClient(app)


def test_root_endpoint(client: TestClient) -> None:
    """Test the root endpoint returns API info."""
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Trading Bot Operator API"
    assert data["version"] == "0.1.0"
    assert "endpoints" in data


def test_health_endpoint(client: TestClient) -> None:
    """Test the health endpoint returns status info."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "uptime_sec" in data
    assert "mode" in data
    assert "trading_enabled" in data


def test_state_endpoint(client: TestClient) -> None:
    """Test the state endpoint returns trading state."""
    response = client.get("/state")
    assert response.status_code == 200
    data = response.json()
    # Check expected fields are present
    assert "equity" in data
    assert "peak_equity" in data
    assert "positions" in data
    assert "open_orders" in data
    assert "circuit_breaker_active" in data
    assert "requires_manual_review" in data


def test_events_endpoint_default(client: TestClient) -> None:
    """Test the events endpoint with default tail."""
    response = client.get("/events")
    assert response.status_code == 200
    data = response.json()
    assert "count" in data
    assert "total" in data
    assert "events" in data
    assert isinstance(data["events"], list)


def test_events_endpoint_with_tail(client: TestClient) -> None:
    """Test the events endpoint with custom tail parameter."""
    response = client.get("/events?tail=10")
    assert response.status_code == 200
    data = response.json()
    assert data["count"] <= 10


def test_events_endpoint_tail_limits(client: TestClient) -> None:
    """Test the events endpoint enforces tail limits."""
    # tail=0 should fail validation
    response = client.get("/events?tail=0")
    assert response.status_code == 422  # Validation error

    # tail > 1000 should fail validation
    response = client.get("/events?tail=1001")
    assert response.status_code == 422  # Validation error


def test_ack_manual_review_endpoint(client: TestClient) -> None:
    """Test the ack-manual-review action endpoint."""
    response = client.post("/actions/ack-manual-review?reason=test_reason")
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "message" in data


def test_kill_switch_endpoint(client: TestClient) -> None:
    """Test the kill-switch action endpoint."""
    response = client.post("/actions/kill-switch?reason=test_kill")
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["reason"] == "test_kill"


def test_pause_endpoint(client: TestClient) -> None:
    """Test the pause action endpoint."""
    response = client.post("/actions/pause?reason=test_pause&duration_hours=2")
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "cooldown_until" in data


def test_pause_endpoint_default_duration(client: TestClient) -> None:
    """Test the pause action with default duration."""
    response = client.post("/actions/pause")
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True


def test_pause_endpoint_duration_limits(client: TestClient) -> None:
    """Test pause endpoint enforces duration limits."""
    # duration=0 should fail
    response = client.post("/actions/pause?duration_hours=0")
    assert response.status_code == 422

    # duration > 48 should fail
    response = client.post("/actions/pause?duration_hours=49")
    assert response.status_code == 422


def test_resume_endpoint(client: TestClient) -> None:
    """Test the resume action endpoint."""
    response = client.post("/actions/resume?reason=test_resume")
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "previously_in_cooldown" in data
