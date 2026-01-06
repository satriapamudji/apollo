# Task 15 — Binance Time Sync + Real Rate-Limit Telemetry

## Goal
Make signed requests resilient and observable by syncing to Binance server time and tracking actual request weight usage.

## Why
- Timestamp drift causes intermittent `-1021` (timestamp outside recvWindow) failures.
- “Rotating API keys” does not fix most Binance rate limits because public endpoints are primarily IP-weighted; you need telemetry + backoff.

## Deliverables
- Add `GET /fapi/v1/time` support and maintain a server-time offset used for all signed endpoints.
- Capture and export Binance rate-limit headers (when present), e.g.:
  - `x-mbx-used-weight-1m`
  - `x-mbx-order-count-10s` / `1m` (if provided)
- Add Prometheus metrics:
  - used weight, throttles, and retry counts by endpoint.

## Acceptance Criteria
- Bot can run for days without timestamp-related auth errors under normal conditions.
- Rate-limit behavior is visible in metrics and logs.

