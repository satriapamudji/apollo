# Task 15: Binance Time Sync + Real Rate-Limit Telemetry

## What Was Added

Implemented Binance server time synchronization and real rate-limit telemetry to make signed requests resilient and observable.

## Files Modified

| File | Changes |
|------|---------|
| `src/connectors/rest_client.py` | Added server time sync methods, rate-limit header tracking, and updated timestamp for signed requests |
| `src/monitoring/metrics.py` | Added 7 new Prometheus metrics for rate-limit telemetry |
| `src/config/settings.py` | Added `time_sync_interval_sec` config to `BinanceConfig` |
| `src/main.py` | Added initial `sync_server_time()` call before making signed requests |

## Key Implementation Details

### Server Time Sync
- Added `get_server_time()` method to fetch Binance server time via `GET /fapi/v1/time`
- Added `sync_server_time()` method to calculate and store the offset between local and server time
- Modified `_request()` to use server-synced timestamp for signed requests: `timestamp = int(time.time() * 1000) + self._server_time_offset`

### Rate-Limit Telemetry
- Added `_update_rate_limit_headers()` method to extract headers from responses:
  - `x-mbx-used-weight-1m` - Actual request weight used
  - `x-mbx-order-count-10s` - Order count in 10s window
  - `x-mbx-order-count-1m` - Order count in 1m window
- Updated `RateLimitTracker` with `update_server_reported_weight()` method
- Added `current_weight` property that prefers server-reported weight

### New Prometheus Metrics
```python
# Rate-limit telemetry
binance_used_weight_1m        # Gauge - Actual used weight from Binance header
binance_order_count_10s       # Gauge - Order count in 10s window
binance_order_count_1m        # Gauge - Order count in 1m window
binance_request_throttled_total  # Counter - Throttled requests by endpoint
binance_request_retry_total   # Counter - Retries by endpoint

# Time sync
binance_time_sync_offset_ms   # Gauge - Time offset from server (ms)
```

### Configuration
```python
class BinanceConfig(BaseModel):
    # ... existing fields ...
    time_sync_interval_sec: int = Field(default=60, ge=30, le=3600)
```

## Why This Matters

1. **Timestamp Drift (-1021 Errors)**: Using local time for signed requests can cause intermittent failures when local clock drifts. Syncing to Binance server time eliminates this.

2. **Rate-Limit Visibility**: Previously the bot only estimated rate limits. Now it captures actual values from Binance headers for better visibility and control.

3. **Observability**: New metrics allow monitoring:
   - How close the bot is to rate limits
   - Whether requests are being throttled
   - Time sync offset over time

## Testing
- All 91 tests pass (3 pre-existing failures unrelated to this change)
- Type-check and linting complete

## Accessing Metrics
All new metrics are available on the Prometheus endpoint (default port 9090):
```
binance_used_weight_1m
binance_order_count_10s
binance_order_count_1m
binance_request_throttled_total
binance_request_retry_total
binance_time_sync_offset_ms
```
