# Task 08: Persist Pending Entries (Restart-Safe Protective Orders)

## What Was Before

The `ExecutionEngine` maintained an in-memory dictionary `_pending_entries` to track entry orders that were placed but not yet filled:

```python
# src/execution/engine.py (line 61)
self._pending_entries: dict[str, PendingEntry] = {}
```

This dictionary was used to store the context needed to place protective orders (stop loss/take profit) after an entry order was filled. The problem: **if the bot restarted between `ORDER_PLACED` and `ORDER_FILLED`, the pending context was lost**, and protective orders would never be placed.

## What Was Changed

### 1. New `PendingEntryStore` Class (`src/execution/state_store.py`)

Created a new class that persists pending entry context to disk (`data/state/pending_entries.jsonl`):

```python
class PendingEntryStore:
    """Persist pending entry context to ensure protective orders are placed even after restart."""

    def __init__(self, state_path: str | Path) -> None:
        self.state_path = Path(state_path)
        self._file = self.state_path / "pending_entries.jsonl"

    def save(self, client_order_id: str, context: dict[str, Any]) -> None:
        """Persist a pending entry context."""

    def remove(self, client_order_id: str) -> None:
        """Remove a pending entry (when filled/cancelled)."""

    def load_all(self) -> dict[str, dict[str, Any]]:
        """Load all persisted pending entries on startup."""
```

### 2. Modified `ExecutionEngine` (`src/execution/engine.py`)

- Added `state_path` parameter to `__init__`
- Added serialization methods (`_serialize_entry`, `_deserialize_entry`)
- Modified `execute_entry()` to persist after adding to `_pending_entries`
- Modified `handle_order_filled()` to remove from store after finalizing
- Modified `handle_order_cancelled()` to remove from store after cancellation

### 3. Added Recovery Logic (`src/main.py`)

Added `_recover_pending_entries()` function that:

1. Loads persisted entries from state store
2. Fetches open orders from Binance
3. Cross-references: only keeps entries whose orders are still open
4. Removes stale entries (order was filled or cancelled while bot was down)

```python
async def _recover_pending_entries(
    execution_engine: ExecutionEngine, rest: BinanceRestClient, event_bus: EventBus
) -> None:
    """Recover pending entries from state store and validate against open orders from Binance."""
```

## Why This Change Was Needed

**Problem Scenario:**
1. Bot places entry order → Stores in `_pending_entries` → Publishes `ORDER_PLACED`
2. Bot crashes/restarts
3. On restart: Event ledger replayed → `ORDER_PLACED` processed → StateManager updates `open_orders`
4. But `_pending_entries` is **EMPTY** (in-memory, not persisted)
5. Order fills via user stream → `ORDER_FILLED` event published
6. `handle_order_filled()` looks for pending entry → **NOT FOUND**
7. **Protective orders are never placed** → Position is unprotected!

**Solution:**
Now the pending entry context is persisted to `data/state/pending_entries.jsonl`, so on restart the context survives and protective orders will be placed when the fill is detected.

## Files Modified

1. `src/execution/state_store.py` - New file (PendingEntryStore class)
2. `src/execution/engine.py` - Integration of persistence
3. `src/main.py` - Recovery logic and state_path injection

## Acceptance Criteria Met

- Kill the bot mid-entry; restart; if the order filled while down:
  - The bot will place protection immediately (via recovered pending entry context)
  - If the order is no longer open on exchange (was filled or cancelled), it's cleaned up from the state store
