# Task 25 — Binance Auxiliary Data Endpoints

## Summary

Integrated free Binance Futures public data endpoints that provide crowding, sentiment, and order flow signals to improve regime detection and entry quality.

## Implementation Details

### 1. REST Client Methods (`src/connectors/rest_client.py`)

Five new async methods added to `BinanceRestClient`:

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `get_open_interest(symbol)` | `/fapi/v1/openInterest` | Current open interest for symbol |
| `get_open_interest_stats(symbol, period, limit)` | `/futures/data/openInterestHist` | Historical OI with period granularity |
| `get_long_short_account_ratio(symbol, period, limit)` | `/futures/data/globalLongShortAccountRatio` | Retail sentiment L/S ratio |
| `get_top_trader_long_short_ratio(symbol, period, limit)` | `/futures/data/topLongShortPositionRatio` | Smart money positioning |
| `get_taker_long_short_ratio(symbol, period, limit)` | `/futures/data/takerlongshortRatio` | Order flow (taker buy/sell ratio) |

### 2. Crowding Data Module (`src/data/crowding.py`)

**CrowdingData Dataclass:**
```python
@dataclass
class CrowdingData:
    long_short_ratio: float      # Global L/S account ratio
    top_trader_ratio: float      # Top trader L/S position ratio
    oi_change_pct: float         # Open interest % change
    taker_buy_ratio: float       # Taker buy/sell ratio
    funding_rate: float          # Current funding rate
    timestamp: datetime
```

**CrowdingCache:**
- TTL-based caching (default 5 minutes)
- Async `get_crowding_data(symbol)` method
- Handles API failures gracefully (returns None)

### 3. Scoring Factors (`src/strategy/scoring.py`)

Four new scoring factors integrated into `CompositeScore`:

| Factor | Weight Config Key | Logic |
|--------|-------------------|-------|
| `CrowdingFactor` | `crowding` | Penalizes extreme L/S ratios (>2.0 or <0.5) |
| `FundingVolatilityFactor` | `funding_volatility` | Penalizes extreme funding rates |
| `OIExpansionFactor` | `oi_expansion` | Rewards OI expansion with trend, penalizes divergence |
| `TakerImbalanceFactor` | `taker_imbalance` | Boosts score when taker flow confirms direction |

**Integration in ScoringEngine:**
```python
def compute(self, ..., crowding_data: dict | None = None) -> CompositeScore:
    # crowding_data dict passed to factors that need it
```

### 4. Configuration (`src/config/settings.py`)

**CrowdingConfig:**
```python
class CrowdingConfig(BaseModel):
    enabled: bool = True
    cache_ttl_seconds: int = 300
    crowding_threshold: float = 2.0
    oi_change_threshold: float = 0.05
    funding_extreme_threshold: float = 0.001
    taker_imbalance_threshold: float = 0.55
```

**FactorWeightsConfig additions:**
```python
crowding: float = 0.10
funding_volatility: float = 0.05
oi_expansion: float = 0.10
taker_imbalance: float = 0.10
```

### 5. Test Coverage (`tests/test_crowding.py`)

28 comprehensive tests covering:
- CrowdingData dataclass creation and edge cases
- CrowdingCache TTL behavior and API mocking
- All four scoring factors with various input scenarios
- Integration with ScoringEngine.compute()
- Edge cases (missing data, None values, extreme values)

## Usage Pattern

```python
# In strategy loop
crowding_cache = CrowdingCache(rest_client, settings.crowding)
crowding_data = await crowding_cache.get_crowding_data(symbol)

# Pass to scoring engine
score = scoring_engine.compute(
    signal=signal,
    features=features,
    crowding_data=crowding_data.__dict__ if crowding_data else None
)
```

## Files Modified

- `src/connectors/rest_client.py` — 5 new endpoint methods
- `src/data/crowding.py` — New module (CrowdingData, CrowdingCache)
- `src/strategy/scoring.py` — 4 new factors, CompositeScore integration
- `src/config/settings.py` — CrowdingConfig, FactorWeightsConfig updates
- `tests/test_crowding.py` — 28 tests

## Notes

- These endpoints use production Binance data URLs even when trading on testnet
- Rate limits are separate from trading endpoints (generous)
- Data granularity options: 5m, 15m, 30m, 1h, 2h, 4h, 6h, 12h, 1d
- Historical data available for backtesting integration

## Verification

All 136 tests pass including the 28 new crowding tests:
```bash
pytest  # 136 passed
```
