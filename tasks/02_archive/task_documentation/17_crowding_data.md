# Task 17 — Add Free Binance "Crowding/Regime" Data

## Summary

Implemented free Binance Futures public data integration for improved signal quality using open interest, long/short ratios, and taker activity data to detect crowding and regime shifts.

## Before

The system relied solely on OHLCV trend data without awareness of market positioning, open interest trends, or funding rate volatility. This made the strategy vulnerable to crowded trades and choppy regimes.

## After

Created a comprehensive data module and scoring factors to incorporate:

1. **Open Interest Data** - Tracks positions buildup/contraction
2. **Global Long/Short Account Ratio** - Measures retail positioning
3. **Top Trader Long/Short Ratio** - Measures professional positioning
4. **Taker Buy/Sell Ratio** - Measures immediate buying/selling pressure
5. **Historical Funding Rate** - Tracks cost of carry and funding volatility

## Changes

### Files Created

1. **`src/data/crowding.py`** - Main data module with:
   - `CrowdingData` dataclass for storing crowding indicators
   - `CrowdingCache` class for caching data to `data/crowding/{symbol}.jsonl`
   - Computes OI expansion/decay rates
   - Computes funding rate volatility
   - Calculates overall crowding score

2. **`tests/test_crowding.py`** - 28 comprehensive tests covering:
   - CrowdingData serialization
   - Cache save/load
   - OI expansion computation
   - Funding volatility computation
   - Crowding factor scoring
   - Taker imbalance scoring
   - Configuration validation

### Files Modified

1. **`src/connectors/rest_client.py`** - Added new REST methods:
   - `get_open_interest(symbol, period, limit)` - Open interest history
   - `get_open_interest_stats(symbol)` - Current OI statistics
   - `get_long_short_account_ratio(symbol, period)` - Global L/S ratio
   - `get_top_trader_long_short_ratio(symbol, period)` - Top trader L/S ratio
   - `get_taker_long_short_ratio(symbol, period)` - Taker activity ratio
   - `get_funding_rate_history(symbol, limit)` - Historical funding rates

2. **`src/strategy/scoring.py`** - Added new factor classes:
   - `CrowdingFactor` - Penalizes extreme long/short positioning
   - `FundingVolatilityFactor` - Penalizes unstable funding rates
   - `OIExpansionFactor` - Detects OI expansion/contraction patterns
   - `TakerImbalanceFactor` - Uses taker activity for sentiment

   Updated `CompositeScore` to include:
   - `crowding_score`
   - `funding_volatility_score`
   - `oi_expansion_score`
   - `taker_imbalance_score`

3. **`src/config/settings.py`** - Added `CrowdingConfig`:
   ```python
   class CrowdingConfig(BaseModel):
       enabled: bool = True
       long_short_extreme: float = 0.8
       oi_expansion_threshold: float = 0.1
       taker_imbalance_threshold: float = 0.6
       funding_volatility_window: int = 8
       oi_window: int = 3
       cache_dir: str = "./data/crowding"
       cache_max_entries: int = 1000
   ```

## Key Features

### Crowding Detection
- Penalizes when >80% of accounts are on one side (long or short)
- Compares top trader positioning vs global to detect smart money divergence
- Scores from 0.0 (crowded) to 1.0 (not crowded)

### OI Expansion/Contraction
- Tracks open interest trends over configurable window (default 3 periods)
- Expansion (OI increasing) may indicate new money entering
- Contraction (OI decreasing) may indicate exits or forced liquidations

### Funding Volatility
- Computes standard deviation of funding rates over configurable window (default 8)
- High volatility can signal regime change
- Scores from 0.0 (stable) to 1.0 (volatile)

### Taker Imbalance
- Measures immediate buying vs selling pressure
- Can detect short-term sentiment shifts
- Used for directional bias adjustment

## Data Flow

```
Binance REST API → get_open_interest(), get_long_short_account_ratio(), etc.
                      ↓
                CrowdingCache.cache()
                      ↓
           data/crowding/{symbol}.jsonl
                      ↓
                CrowdingCache.compute_trends()
                      ↓
               CrowdingData (with computed metrics)
                      ↓
          SignalGenerator.generate(crowding_data=...)
                      ↓
           ScoringEngine.compute(crowding_data=...)
                      ↓
          CompositeScore (includes crowding factors)
                      ↓
          ThinkingLogger.log_signal() → logs/thinking.jsonl
```

## Configuration

Add to `config.yaml`:
```yaml
crowding:
  enabled: true
  long_short_extreme: 0.8
  oi_expansion_threshold: 0.1
  taker_imbalance_threshold: 0.6
  funding_volatility_window: 8
  oi_window: 3
```

## Acceptance Criteria Met

- [x] Open interest, long/short ratios, taker data fetched via REST
- [x] Data cached to `data/crowding/{symbol}.jsonl`
- [x] New factors: crowding penalty, funding volatility, OI expansion, taker imbalance
- [x] Factors are deterministic and visible in `logs/thinking.jsonl` (via CompositeScore)
- [x] Strategy can run entirely on free Binance + local computation
- [x] All new code passes type-check and linting
- [x] All 28 new tests pass
