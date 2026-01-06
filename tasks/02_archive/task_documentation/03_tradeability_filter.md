# Task 03: Universe Selection Tradeability Filter

## Summary

Implemented a tradeability filter for universe selection that prevents the bot from selecting symbols the account cannot trade due to Binance's minimum quantity/step size constraints.

## What Was Already Implemented

The core tradeability filtering logic was already present in `src/strategy/universe.py`:

- `UniverseSelector` class with `_compute_min_equity_required()` method
- `select_with_filtering()` method returning `UniverseSelectionResult`
- `FilteredSymbol` dataclass capturing filtering details
- `EventType.SYMBOL_FILTERED` in `src/ledger/events.py`

## What Was Added

### 1. EventBus Integration (`src/strategy/universe.py`)

Added ability for `UniverseSelector` to emit ledger events:

- Added imports: `EventType` from `src.ledger.events`, `EventBus` from `src.ledger.bus`
- Added optional `event_bus: EventBus | None = None` parameter to constructor
- Added `_emit_filter_event()` async method to publish `SYMBOL_FILTERED` events
- Modified `select_with_filtering()` to call `_emit_filter_event()` when a symbol is filtered

### 2. Updated Universe Loop (`src/main.py`)

- Pass `event_bus` to `UniverseSelector` constructor
- Use `select_with_filtering()` instead of legacy `select()` method
- Updated `UNIVERSE_UPDATED` event payload to include:
  - `filtered_count`: Number of symbols filtered out
  - `filtered_symbols`: List of filtered symbol names

## Changes Made

### `src/strategy/universe.py`

```python
# Added imports
from src.ledger.events import EventType
from src.ledger.bus import EventBus

# Updated constructor
def __init__(
    self,
    rest: BinanceRestClient,
    config: UniverseConfig,
    risk_config: RiskConfig | None = None,
    equity: float = 0.0,
    event_bus: EventBus | None = None,  # NEW
) -> None:
    # ...
    self.event_bus = event_bus  # NEW

# NEW method
async def _emit_filter_event(self, filtered: FilteredSymbol) -> None:
    """Emit a SYMBOL_FILTERED event when a symbol is filtered out."""
    if self.event_bus is None:
        return
    try:
        await self.event_bus.publish(
            EventType.SYMBOL_FILTERED,
            {
                "symbol": filtered.symbol,
                "reason": filtered.reason,
                "min_equity_required": filtered.min_equity_required,
                "current_equity": filtered.current_equity,
                "mark_price": filtered.mark_price,
                "min_qty": filtered.min_qty,
                "min_notional": filtered.min_notional,
                "step_size": filtered.step_size,
            },
            {"source": "universe_selector"},
        )
    except Exception:
        log.exception("failed_to_emit_symbol_filtered_event", symbol=filtered.symbol)
```

### `src/main.py`

```python
# Updated universe_selector initialization
universe_selector = UniverseSelector(rest, settings.universe, event_bus=event_bus)

# Updated universe_loop
async def universe_loop() -> None:
    while True:
        try:
            result = await universe_selector.select_with_filtering()  # NEW
            symbols = [u.symbol for u in result.selected]
            filtered_symbols = [f.symbol for f in result.filtered]  # NEW
            await event_bus.publish(
                EventType.UNIVERSE_UPDATED,
                {
                    "symbols": symbols,
                    "filtered_count": len(filtered_symbols),  # NEW
                    "filtered_symbols": filtered_symbols,  # NEW
                },
                {"source": "universe_selector"},
            )
            # ... logging updated to include filtered info
```

## Acceptance Criteria Met

- On an account with low equity, the bot now avoids proposing trades that will always be rejected due to sizing constraints
- Logs show why each symbol was excluded (minQty/stepSize/minNotional vs equity)
- Ledger events (`SYMBOL_FILTERED`) are emitted with full details about filtering decisions
- `UNIVERSE_UPDATED` events include metadata about filtered symbols

## Additional Fix

Fixed a pre-existing issue in `src/config/settings.py` where `ScoringConfig` and `FactorWeightsConfig` were defined after their usage in `StrategyConfig`, causing a `NameError` during import.
