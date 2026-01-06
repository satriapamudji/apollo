# Task 16: Use Production Market Data While Trading Testnet

## What Was Changed

### Before
`BinanceRestClient` used a single `base_url` for all API requests, meaning:
- When running in `testnet` mode, both market data AND trading requests went to testnet
- Testnet market data can be incomplete or unrepresentative
- Strategy development suffered from unrealistic liquidity/volume/funding inputs

### After

1. **New Configuration** (`src/config/settings.py`):
   - Added `use_production_market_data: bool = False` - Enable production market data in testnet
   - Added `market_data_base_url: str = "https://fapi.binance.com"` - Override URL
   - Added `market_data_ws_url: str = "wss://fstream.binance.com"` - Override WS URL
   - Added `binance_market_data_base_url` property to `Settings` class that returns:
     - Production URL if `use_production_market_data=True`
     - Trading URL (testnet/live) otherwise

2. **Dual HTTP Clients** (`src/connectors/rest_client.py`):
   - Added `market_http` client for market data requests
   - Market data methods now use `_market_request()` with market data URL
   - Account/order methods continue using `_request()` with trading URL

3. **Market Data Methods** (use production when enabled):
   - `get_exchange_info()` - Exchange info from production
   - `get_24h_tickers()` - 24hr tickers from production
   - `get_klines()` - Klines/candles from production
   - `get_mark_price()` - Mark prices from production
   - `get_book_ticker()` - Book tickers from production
   - `get_funding_rate()` - Funding rates from production

4. **Account/Order Methods** (always use trading URL - safety):
   - `get_account_info()` - Account info from trading environment
   - `get_position_risk()` - Position risk from trading environment
   - `get_open_orders()` - Open orders from trading environment
   - `place_order()` - Orders go to trading environment
   - `cancel_order()` - Orders go to trading environment
   - `set_leverage()` - Leverage settings to trading environment
   - `set_margin_type()` - Margin type to trading environment
   - `start_listen_key()` - User stream from trading environment

## Why

Binance testnet market data has limitations:
- Incomplete or stale data
- Lower liquidity than production
- Funding rates may not reflect real market conditions
- Volume data may not be realistic

This feature allows:
- **Realistic strategy development** - Use production data for signals while testing orders on testnet
- **Safe testing** - Orders still go to testnet (can't lose real money)
- **Accurate backtesting alignment** - Backtest with production data, forward-test with same data

## Files Modified

- `src/config/settings.py` - Added market data config fields and property
- `src/connectors/rest_client.py` - Added dual HTTP clients and `_market_request()` method

## Configuration Example

```yaml
binance:
  use_production_market_data: true  # Use production market data even in testnet mode
  # market_data_base_url: https://fapi.binance.com  # Optional override
```

## Behavior Matrix

| Mode | `use_production_market_data` | Market Data URL | Trading URL |
|------|------------------------------|-----------------|-------------|
| paper | false | N/A (no trading) | N/A |
| paper | true | Production | N/A |
| testnet | false | Testnet | Testnet |
| testnet | true | Production | Testnet |
| live | false | Production | Production |
| live | true | Production | Production |

## Safety Considerations

- **Account/order endpoints always use trading URL** - Orders never accidentally go to production
- **Market data is read-only** - Even if market data URL is wrong, no funds at risk
- **Clear separation** - Two HTTP clients make it explicit which URL is being used
