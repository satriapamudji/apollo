# Task 12: Operator API (State + Actions) - Documentation

## Overview

This document describes the changes made to implement a local FastAPI service that exposes endpoints for operators to inspect state and take safe actions on the trading bot.

## What Was Changed

### New Files Created

1. **`src/api/__init__.py`** - Package initialization file for the new API module
2. **`src/api/operator.py`** - Main FastAPI application with all operator endpoints
3. **`tests/test_operator_api.py`** - Comprehensive test suite for the API (12 tests)

### Modified Files

1. **`pyproject.toml`** - Added FastAPI and Uvicorn dependencies
2. **`src/config/settings.py`** - Added `api_port` configuration to `MonitoringConfig`
3. **`src/main.py`** - Added API server startup alongside main trading loops

## Endpoints Implemented

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | API root with endpoint reference |
| GET | `/health` | System health status (uptime, mode, trading enabled, WS msg age) |
| GET | `/state` | Full trading state (positions, orders, equity, risk flags) |
| GET | `/events?tail=N` | Recent events from the ledger |
| POST | `/actions/ack-manual-review` | Clear manual review flag |
| POST | `/actions/kill-switch` | Trigger system shutdown event |
| POST | `/actions/pause` | Pause trading (cooldown mode) |
| POST | `/actions/resume` | Resume trading |

## Design Decisions

1. **Local-only by default**: The API binds to `127.0.0.1` for security
2. **Port 8000**: Configurable via `settings.monitoring.api_port` (default: 8000)
3. **Shared state**: API reads from the same `StateManager` and `EventLedger` as the main bot
4. **Event persistence**: Actions append events to the ledger (same pattern as `ack_manual_review.py`)
5. **Synchronous endpoints**: State reads are cheap; no async complexity needed

## Configuration

The API port is configured in `config.yaml`:

```yaml
monitoring:
  metrics_port: 9090  # Prometheus metrics (unchanged)
  api_port: 8000      # Operator API (new)
```

## Usage Examples

```bash
# Check health status
curl http://localhost:8000/health

# Get current state
curl http://localhost:8000/state

# Get last 50 events
curl http://localhost:8000/events?tail=50

# Acknowledge manual review
curl -X POST "http://localhost:8000/actions/ack-manual-review?reason=checked_ok"

# Trigger kill switch
curl -X POST "http://localhost:8000/actions/kill-switch?reason=emergency"

# Pause trading for 4 hours
curl -X POST "http://localhost:8000/actions/pause?reason=maintenance&duration_hours=4"

# Resume trading
curl -X POST "http://localhost:8000/actions/resume?reason=ready_to_continue"
```

## Testing

The implementation includes 12 tests covering:
- All endpoints return correct status codes
- Response schemas match expectations
- Query parameter validation (tail limits, duration limits)
- Action endpoints persist events correctly

Run tests with:
```bash
pytest tests/test_operator_api.py -v
```

## Acceptance Criteria Met

- ✅ Operator can identify "why trading is paused" in <60 seconds using `/state` endpoint
- ✅ All endpoints wire to existing `StateManager` + `EventLedger` (no new source of truth)
- ✅ Local-only binding by default for security
