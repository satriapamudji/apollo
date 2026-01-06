# Adding Strategies

Guide to developing and integrating trading strategies in the Binance Trend Bot.

## Overview

Strategies in the bot are defined declaratively using YAML specifications in markdown files. The system validates data requirements, parameters, and configuration before running.

**Key Files**:
- `src/strategy/package.py` - Strategy spec loader
- `src/strategy/signals.py` - Signal generation
- `src/strategy/scoring.py` - Scoring engine
- `strategies/<name>/strategy.md` - Strategy specifications

---

## Strategy Specification Format

### File Location

```
strategies/
└── <strategy_name>/
    └── strategy.md
```

### YAML Front Matter

```yaml
---
name: trend_following_v1
version: 1
description: Multi-timeframe trend following with breakout entries

requires:
  bars:
    - series: trade
      interval: 4h
  derived:
    - interval: 1d
      from: 4h
  funding: true

parameters:
  ema_fast:
    type: int
    default: 8
    min: 2
    max: 50
    description: Fast EMA period
  ema_slow:
    type: int
    default: 21
    min: 5
    max: 100
    description: Slow EMA period
  entry_style:
    type: str
    default: breakout
    enum: [breakout, pullback]
    description: Entry signal style
  score_threshold:
    type: float
    default: 0.55
    min: 0.3
    max: 0.95
    description: Minimum score for entry

assumptions:
  - Trend persistence on daily timeframe
  - Volume confirms breakouts
  - ATR-based position sizing
---

# Strategy Description

Detailed description in markdown...
```

### Specification Fields

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Strategy identifier (matches folder name) |
| `version` | Yes | Version number (integer) |
| `description` | No | Brief description |
| `requires` | Yes | Data requirements |
| `parameters` | No | Configurable parameters |
| `assumptions` | No | Strategy assumptions |

### Data Requirements

**Bar Data** (`requires.bars`):
```yaml
bars:
  - series: trade           # Data series type
    interval: 4h            # Timeframe
```

**Derived Data** (`requires.derived`):
```yaml
derived:
  - interval: 1d            # Target interval
    from: 4h                # Source interval to aggregate
```

**Funding Data**:
```yaml
funding: true               # Require funding rate data
```

### Parameter Types

| Type | Example | Constraints |
|------|---------|-------------|
| `int` | `ema_fast: 8` | `min`, `max` |
| `float` | `threshold: 0.55` | `min`, `max` |
| `str` | `style: breakout` | `enum` |
| `bool` | `enabled: true` | - |

---

## Creating a New Strategy

### Step 1: Create Strategy Folder

```bash
mkdir -p strategies/my_strategy
```

### Step 2: Create Specification

`strategies/my_strategy/strategy.md`:

```yaml
---
name: my_strategy
version: 1
description: Custom momentum strategy

requires:
  bars:
    - series: trade
      interval: 4h
  derived:
    - interval: 1d
      from: 4h
  funding: true

parameters:
  momentum_period:
    type: int
    default: 14
    min: 5
    max: 50
  entry_threshold:
    type: float
    default: 0.5
    min: 0.1
    max: 1.0
---

# My Strategy

Custom momentum-based entry strategy...
```

### Step 3: Implement Signal Logic

If your strategy requires custom signal logic, extend `SignalGenerator`:

```python
# src/strategy/signals.py (or new file)

class MySignalGenerator(SignalGenerator):
    def generate(
        self,
        symbol: str,
        daily_df: pd.DataFrame,
        fourh_df: pd.DataFrame,
        **kwargs
    ) -> Signal:
        # Custom signal logic
        momentum = self._compute_momentum(fourh_df)

        if momentum > self.config.entry.threshold:
            return Signal(
                symbol=symbol,
                signal_type=SignalType.LONG,
                # ... other fields
            )

        return Signal(symbol=symbol, signal_type=SignalType.NONE, ...)
```

### Step 4: Configure Strategy

In `config.yaml`:

```yaml
strategy:
  name: my_strategy
  # Override parameters from spec
  indicators:
    momentum_period: 20
  entry:
    threshold: 0.6
```

### Step 5: Validate Strategy

```python
from src.strategy.package import load_strategy_spec, validate_strategy_config
from src.config.settings import load_settings

settings = load_settings()
spec = load_strategy_spec("my_strategy")
errors = validate_strategy_config(spec, settings.strategy)

if errors:
    print("Validation errors:", errors)
else:
    print("Strategy valid!")
```

---

## Signal Generation API

### SignalGenerator Class

```python
class SignalGenerator:
    def __init__(self, config: StrategyConfig, regime_config: RegimeConfig):
        self.config = config
        self.regime_classifier = RegimeClassifier(regime_config)
        self.scoring_engine = ScoringEngine(config.scoring)

    def generate(
        self,
        symbol: str,
        daily_df: pd.DataFrame,
        fourh_df: pd.DataFrame,
        funding_rate: float,
        news_risk: str,
        open_position: Position | None,
        current_time: datetime
    ) -> Signal:
        """Generate signal for symbol."""
```

### Signal Class

```python
@dataclass(frozen=True)
class Signal:
    symbol: str
    signal_type: SignalType      # LONG, SHORT, EXIT, NONE
    score: CompositeScore | None
    price: float                 # Current price
    atr: float                   # Current ATR
    entry_price: float | None    # Proposed entry
    stop_price: float | None     # Proposed stop
    take_profit: float | None    # Proposed TP
    reason: str | None           # Signal reason
    trade_id: str | None         # Unique trade ID
    timestamp: datetime | None
    regime: RegimeClassification | None
    entry_extension: float | None
    volume_ratio: float | None
```

### SignalType Enum

```python
class SignalType(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    EXIT = "EXIT"
    NONE = "NONE"
```

---

## Scoring System

### CompositeScore

```python
@dataclass(frozen=True)
class CompositeScore:
    trend_score: float           # EMA alignment + momentum
    volatility_score: float      # ATR regime
    entry_quality: float         # Distance from breakout
    funding_penalty: float       # Funding rate impact
    news_modifier: float         # News risk impact
    liquidity_score: float       # Spread-based
    crowding_score: float        # Market crowding
    funding_volatility_score: float
    oi_expansion_score: float
    taker_imbalance_score: float
    volume_score: float
    composite: float             # Final [0.0, 1.0]
```

### ScoringEngine

```python
class ScoringEngine:
    def compute(
        self,
        direction: str,          # "LONG" or "SHORT"
        price: float,
        ema_fast: float,
        ema_slow: float,
        ema_fast_3bars_ago: float,
        atr: float,
        entry_distance_atr: float,
        funding_rate: float,
        news_risk: str,
        volume_ratio: float,
        crowding_data: dict | None = None
    ) -> CompositeScore:
        """Compute composite score for entry."""
```

### Customizing Weights

```yaml
# config.yaml
strategy:
  scoring:
    enabled: true
    threshold: 0.55
    factors:
      trend: 0.35
      volatility: 0.15
      entry_quality: 0.25
      funding: 0.10
      news: 0.15
      liquidity: 0.0
      volume: 0.0
```

---

## Regime Detection

### RegimeClassifier

```python
class RegimeClassifier:
    def classify(
        self,
        adx: float,
        choppiness: float,
        atr_pct: float | None = None,
        atr_sma: float | None = None
    ) -> RegimeClassification:
        """Classify market regime."""
```

### RegimeClassification

```python
@dataclass(frozen=True)
class RegimeClassification:
    regime: RegimeType           # TRENDING, CHOPPY, TRANSITIONAL
    adx: float
    chop: float
    blocks_entry: bool
    size_multiplier: float
    volatility_regime: VolatilityRegimeType | None
    volatility_contracts: bool
    volatility_expands: bool
```

### Using Regime in Strategy

```python
def generate(self, symbol, daily_df, fourh_df, **kwargs) -> Signal:
    # Classify regime
    regime = self.regime_classifier.classify(
        adx=daily_df["adx"].iloc[-1],
        choppiness=daily_df["chop"].iloc[-1]
    )

    # Block entry in choppy regime
    if regime.blocks_entry:
        return Signal(
            symbol=symbol,
            signal_type=SignalType.NONE,
            reason="Choppy regime - entry blocked"
        )

    # Adjust size in transitional regime
    size_multiplier = regime.size_multiplier
```

---

## Entry Styles

### Breakout Entry

Entry when price breaks above/below N-bar high/low:

```python
def _check_breakout(self, df: pd.DataFrame, period: int = 20) -> tuple[bool, str]:
    high_breakout = df["close"].iloc[-1] > df["high"].iloc[-period:-1].max()
    low_breakout = df["close"].iloc[-1] < df["low"].iloc[-period:-1].min()

    if high_breakout:
        return True, "LONG"
    elif low_breakout:
        return True, "SHORT"
    return False, None
```

### Pullback Entry

Entry on pullback to EMA:

```python
def _check_pullback(self, df: pd.DataFrame) -> tuple[bool, str]:
    price = df["close"].iloc[-1]
    ema_fast = df["ema_fast"].iloc[-1]
    ema_slow = df["ema_slow"].iloc[-1]

    # Uptrend pullback
    if ema_fast > ema_slow and price <= ema_fast * 1.01:
        return True, "LONG"

    # Downtrend pullback
    if ema_fast < ema_slow and price >= ema_fast * 0.99:
        return True, "SHORT"

    return False, None
```

---

## Testing Strategies

### Unit Tests

```python
# tests/test_my_strategy.py
import pytest
import pandas as pd
from src.strategy.signals import MySignalGenerator
from src.config.settings import StrategyConfig

def test_long_signal():
    config = StrategyConfig()
    generator = MySignalGenerator(config)

    df = create_trending_data()  # Helper to create test data
    signal = generator.generate(symbol="TEST", daily_df=df, fourh_df=df)

    assert signal.signal_type == SignalType.LONG

def test_no_signal_choppy():
    config = StrategyConfig()
    generator = MySignalGenerator(config)

    df = create_choppy_data()
    signal = generator.generate(symbol="TEST", daily_df=df, fourh_df=df)

    assert signal.signal_type == SignalType.NONE
```

### Backtesting

```bash
# Run backtest with your strategy
backtest --symbol BTCUSDT --interval 4h --data-path ./data/market

# Check results
cat ./data/backtests/summary.json | jq
```

---

## Strategy Best Practices

1. **Start simple**: Begin with basic rules, add complexity gradually
2. **Validate data requirements**: Ensure all required data is available
3. **Use regime detection**: Avoid trading in unsuitable market conditions
4. **Set realistic parameters**: Use defaults that work across markets
5. **Document assumptions**: List what the strategy assumes about markets
6. **Test thoroughly**: Use backtesting and paper trading before live
7. **Monitor performance**: Track metrics and adjust as needed

---

## Related Documentation

- [System Overview](../00_architecture/01_SystemOverview.md) - Architecture
- [Module Reference](../00_architecture/02_ModuleReference.md) - Strategy module
- [Configuration Reference](../04_reference/01_Configuration.md) - Strategy config
- [Backtester Overview](../05_backtester/01_Overview.md) - Testing strategies
