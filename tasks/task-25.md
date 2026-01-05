# Task 25 — Add Free Binance Auxiliary Data Endpoints

## Goal
Integrate free Binance Futures public data endpoints that provide crowding, sentiment, and order flow signals to improve regime detection and entry quality.

## Why
Pure OHLCV trend systems are fragile in crypto. Binance provides **free** public time series that help detect:
- **Crowding**: When everyone is positioned the same way (fade signal)
- **Regime shifts**: When momentum is building or exhausting
- **Smart money positioning**: What large traders are doing

This is alpha that most retail bots ignore because they don't know these endpoints exist.

## Available Free Endpoints (No API Key Required)

### 1. Open Interest
```
GET /fapi/v1/openInterest?symbol=BTCUSDT
GET /futures/data/openInterestHist?symbol=BTCUSDT&period=5m&limit=500
```
**Use**: Detect position crowding. OI rising + price rising = strong trend. OI rising + price flat = squeeze incoming.

### 2. Long/Short Account Ratio
```
GET /futures/data/globalLongShortAccountRatio?symbol=BTCUSDT&period=5m&limit=500
```
**Use**: Sentiment. Extreme readings (>2.0 or <0.5) suggest crowded trade, potential reversal.

### 3. Top Trader Long/Short Ratio
```
GET /futures/data/topLongShortPositionRatio?symbol=BTCUSDT&period=5m&limit=500
GET /futures/data/topLongShortAccountRatio?symbol=BTCUSDT&period=5m&limit=500
```
**Use**: Smart money positioning. Divergence from retail ratio = edge.

### 4. Taker Buy/Sell Volume
```
GET /futures/data/takerlongshortRatio?symbol=BTCUSDT&period=5m&limit=500
```
**Use**: Order flow. Taker buy volume > sell = aggressive buying. Leading indicator.

## Deliverables

### 1. REST Client Methods
Add to `src/connectors/rest_client.py`:
```python
async def get_open_interest(self, symbol: str) -> dict:
    return await self._request("GET", "/fapi/v1/openInterest", 
                               params={"symbol": symbol}, weight=1)

async def get_open_interest_hist(self, symbol: str, period: str = "4h", 
                                  limit: int = 100) -> list:
    return await self._request("GET", "/futures/data/openInterestHist",
                               params={"symbol": symbol, "period": period, 
                                       "limit": limit}, weight=1)

async def get_long_short_ratio(self, symbol: str, period: str = "4h",
                                limit: int = 100) -> list:
    return await self._request("GET", "/futures/data/globalLongShortAccountRatio",
                               params={"symbol": symbol, "period": period,
                                       "limit": limit}, weight=1)

async def get_top_trader_ratio(self, symbol: str, period: str = "4h",
                                limit: int = 100) -> list:
    return await self._request("GET", "/futures/data/topLongShortPositionRatio",
                               params={"symbol": symbol, "period": period,
                                       "limit": limit}, weight=1)

async def get_taker_volume_ratio(self, symbol: str, period: str = "4h",
                                  limit: int = 100) -> list:
    return await self._request("GET", "/futures/data/takerlongshortRatio",
                               params={"symbol": symbol, "period": period,
                                       "limit": limit}, weight=1)
```

### 2. Auxiliary Data Module
Create `src/features/auxiliary.py`:
```python
@dataclass
class AuxiliaryData:
    open_interest: float
    open_interest_change_pct: float
    long_short_ratio: float
    top_trader_ratio: float
    taker_buy_ratio: float
    
    @property
    def crowding_score(self) -> float:
        """0 = uncrowded, 1 = extremely crowded (fade signal)"""
        
    @property  
    def order_flow_score(self) -> float:
        """-1 = aggressive selling, +1 = aggressive buying"""
```

### 3. Signal Integration
In `SignalGenerator.generate()`:
- Fetch auxiliary data for the symbol
- Add crowding penalty to scoring
- Use order flow as confirmation filter

### 4. Scoring Factor
Add to `ScoringEngine`:
- `crowding_score`: Penalize entries when L/S ratio > 2.0 or < 0.5
- `order_flow_score`: Boost when taker flow confirms direction

### 5. Config
```yaml
auxiliary_data:
  enabled: true
  cache_ttl_seconds: 300
  crowding_threshold: 2.0  # L/S ratio extreme
  order_flow_weight: 0.1
```

## Acceptance Criteria
- All 5 Binance data endpoints integrated and callable
- Auxiliary data fetched once per strategy cycle (cached)
- Crowding penalty visible in composite score
- Entries blocked when crowding is extreme AND trend is extended
- Logging shows auxiliary data for each signal evaluation

## Files to Modify
- `src/connectors/rest_client.py`
- `src/features/auxiliary.py` (new)
- `src/strategy/signals.py`
- `src/strategy/scoring.py`
- `src/config/settings.py`
- `config.yaml`

## Notes
- These endpoints use production data URLs even for testnet trading
- Rate limits are separate from trading endpoints (generous)
- Data granularity: 5m, 15m, 30m, 1h, 2h, 4h, 6h, 12h, 1d available
- Historical data available for backtesting too

