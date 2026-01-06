# Task 29 - exchangeInfo Snapshot + Symbol Rules Versioning + Environment Routing

## Overview

Added infrastructure to download Binance Futures exchangeInfo snapshots, extract normalized symbol rules (tick_size, step_size, min_qty, min_notional), and wire them into the backtester. Also aligned testnet/mainnet URL configuration for clarity.

## What Was Before

- Backtester used hardcoded `SymbolFilters(...)` values in `src/backtester/engine.py`
- Symbol constraints could drift from reality, causing invalid sizing and unrealistic backtests
- `config.yaml` had ambiguous environment routing (testnet flag pointing at production URLs)
- No way to pin symbol rules to a specific point in time for reproducible backtests

## What Changed

### 1. New CLI Tool: `src/tools/download_exchange_info.py`

Downloads and stores exchangeInfo snapshots from Binance Futures:

```bash
python -m src.tools.download_exchange_info
python -m src.tools.download_exchange_info --output-dir ./data/exchange_info
```

Outputs:
- `data/exchange_info/raw/exchange_info_{timestamp}.json` - Raw API response
- `data/exchange_info/symbol_rules_{timestamp}.json` - Normalized rules
- `data/exchange_info/symbol_rules_latest.json` - Convenience latest pointer

Features:
- Retry logic for rate limits (429), IP bans (418), and server errors (5xx)
- Robust filter parsing with fallback defaults
- Tracks which defaults were applied per symbol

### 2. New Module: `src/backtester/symbol_rules.py`

Core components:

```python
@dataclass
class SymbolRuleEntry:
    symbol: str
    tick_size: float      # Price increment from PRICE_FILTER
    step_size: float      # Qty increment from LOT_SIZE
    min_qty: float        # Minimum quantity from LOT_SIZE
    min_notional: float   # Minimum notional from MIN_NOTIONAL/NOTIONAL
    contract_type: str    # PERPETUAL, CURRENT_QUARTER, etc.
    status: str           # TRADING, BREAK, etc.
    quote_asset: str      # USDT, BUSD
    base_asset: str
    margin_asset: str
    price_precision: int
    quantity_precision: int
    defaults_applied: tuple[str, ...]  # Which fields used fallback defaults

    def to_symbol_filters(self) -> SymbolFilters:
        """Convert to backtester SymbolFilters format."""

class SymbolRulesSnapshot:
    effective_date: datetime
    source_file: str
    rules: dict[str, SymbolRuleEntry]
    
    def get_filters(self, symbol: str) -> SymbolFilters:
        """Get SymbolFilters for a symbol, raises KeyError if not found."""
```

Utility functions:
- `load_symbol_rules(path)` - Load from JSON file
- `find_latest_rules(data_dir)` - Find most recent rules file
- `find_rules_for_date(data_dir, target_date)` - Find rules valid for a date
- `auto_load_rules(rules_path, data_dir)` - Load with fallback logic

### 3. Backtester Integration: `src/backtester/engine.py`

Modified `Backtester.__init__` to accept optional `symbol_rules_path`:

```python
def __init__(
    self,
    ...,
    symbol_rules_path: Path | str | None = None,
):
```

Added `get_symbol_filters(symbol)` method:
- If rules snapshot loaded, returns filters from snapshot (raises KeyError if symbol missing)
- Falls back to hardcoded defaults if no rules file provided (backwards compatible)

Replaced hardcoded `self.symbol_filters` with dynamic `self.get_symbol_filters(symbol)` calls.

### 4. CLI Integration: `src/backtester/runner.py`

Added `--rules-file` CLI argument:

```bash
backtest --symbol BTCUSDT --interval 4h --data-path ./data/market --rules-file ./data/exchange_info/symbol_rules_latest.json
```

### 5. Config Alignment: `config.yaml`

Added explicit testnet URLs under `binance:` section:

```yaml
binance:
  base_url: https://fapi.binance.com
  ws_url: wss://fstream.binance.com
  testnet_base_url: https://testnet.binancefuture.com
  testnet_ws_url: wss://stream.binancefuture.com
  use_production_market_data: false
  market_data_base_url: https://fapi.binance.com
  market_data_ws_url: wss://fstream.binance.com
```

This establishes a clear two-plane model:
- **Trading plane**: Controlled by `run.mode` (paper/testnet/live)
- **Market data plane**: Can optionally use production via `use_production_market_data`

### 6. Tests: `tests/test_symbol_rules.py`

10 comprehensive tests covering:
- `test_parse_exchange_info_btcusdt` - Realistic BTCUSDT filter parsing
- `test_parse_exchange_info_missing_filters` - Defaults tracking
- `test_load_symbol_rules_from_file` - JSON loading
- `test_get_symbol_filters_conversion` - SymbolRuleEntry to SymbolFilters
- `test_snapshot_get_filters_not_found` - KeyError on missing symbol
- `test_load_symbol_rules_file_not_found` - Error handling
- `test_find_latest_rules` - Latest file discovery
- `test_find_rules_for_date` - Date-based rule selection
- `test_auto_load_rules_with_explicit_path` - Explicit path loading
- `test_auto_load_rules_no_files` - Graceful None return

## Reasoning

### Why version symbol rules?

1. **Reproducibility**: Binance updates exchange rules periodically. Pinning rules to a snapshot date ensures identical backtest results over time.

2. **Fail-fast validation**: Better to error loudly if a symbol is missing from rules than silently use wrong constraints and get invalid backtests.

3. **Auditability**: The `defaults_applied` field tracks when fallbacks were used, making it clear when rules may be imprecise.

### Why the two-plane model?

Market data endpoints are public and free. Trading endpoints require API keys and have different rate limits. Separating these concerns:
- Allows testnet trading with production market data
- Makes intent explicit in configuration
- Prevents "works locally, fails in production" surprises

## Usage Examples

### Download latest exchange info
```bash
python -m src.tools.download_exchange_info
```

### Run backtest with symbol rules
```bash
backtest --symbol BTCUSDT --interval 4h \
  --data-path ./data/market \
  --rules-file ./data/exchange_info/symbol_rules_latest.json
```

### Programmatic usage
```python
from src.backtester.symbol_rules import load_symbol_rules

snapshot = load_symbol_rules("./data/exchange_info/symbol_rules_20240115_120000.json")
filters = snapshot.get_filters("BTCUSDT")
print(f"BTCUSDT tick_size: {filters.tick_size}, step_size: {filters.step_size}")
```

## Files Modified/Created

### New Files
- `src/tools/download_exchange_info.py` - CLI tool for downloading exchangeInfo
- `src/backtester/symbol_rules.py` - Symbol rules parsing and loading
- `tests/test_symbol_rules.py` - Unit tests

### Modified Files
- `src/backtester/engine.py` - Dynamic symbol filters loading
- `src/backtester/runner.py` - Added `--rules-file` CLI argument
- `config.yaml` - Added explicit testnet URLs
