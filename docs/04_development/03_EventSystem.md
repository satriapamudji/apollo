# Event System

Deep dive into the event sourcing architecture used in the Binance Trend Bot.

## Overview

The bot uses event sourcing to maintain a complete, auditable history of all state changes. Every action that affects trading state is recorded as an immutable event, enabling:

- **Auditability**: Complete history of all decisions and actions
- **Reproducibility**: State can be reconstructed from events
- **Debugging**: Trace any issue back to its source event
- **Recovery**: Restart from any point in history

**Source Files**:
- `src/ledger/events.py` - Event type definitions
- `src/ledger/bus.py` - Event bus (pub/sub)
- `src/ledger/store.py` - Event ledger (persistence)
- `src/ledger/state.py` - State manager

---

## Core Concepts

### Event

An immutable record of something that happened:

```python
@dataclass(frozen=True)
class Event:
    event_id: str              # UUID
    event_type: EventType      # Event category
    timestamp: datetime        # When it happened
    sequence_num: int          # Global ordering
    payload: dict[str, Any]    # Event-specific data
    metadata: dict[str, Any]   # Context (source, etc.)
```

### Event Bus

Pub/sub system for distributing events to handlers:

```python
class EventBus:
    def register(event_type: EventType, handler: Callable) -> None
    async def publish(event_type: EventType, payload: dict, metadata: dict = None) -> Event
```

### Event Ledger

Append-only persistence to JSONL file:

```python
class EventLedger:
    def append(event: Event) -> None
    def load_all() -> list[Event]
    def iter_events_tail(n: int) -> Iterator[Event]
```

### State Manager

Reconstructs current state by replaying events:

```python
class StateManager:
    def rebuild(events: list[Event]) -> None
    def apply_event(event: Event) -> None
    @property
    def state(self) -> TradingState
```

---

## Event Types

### System Events

| Event | Description | Payload |
|-------|-------------|---------|
| `SYSTEM_STARTED` | Bot started | `version` |
| `SYSTEM_STOPPED` | Bot stopped | `reason`, `initiated_by` |

### Market Events

| Event | Description | Payload |
|-------|-------------|---------|
| `MARKET_TICK` | Price update | `symbol`, `price`, `timestamp` |
| `CANDLE_CLOSE` | Bar closed | `symbol`, `interval`, `ohlcv` |
| `FUNDING_UPDATE` | Funding rate | `symbol`, `rate`, `mark_price` |

### News Events

| Event | Description | Payload |
|-------|-------------|---------|
| `NEWS_INGESTED` | News item fetched | `source`, `title`, `link`, `published` |
| `NEWS_CLASSIFIED` | News classified | `symbol`, `risk_level`, `reason`, `confidence` |

### Universe Events

| Event | Description | Payload |
|-------|-------------|---------|
| `UNIVERSE_UPDATED` | Universe refreshed | `symbols`, `filtered_count` |
| `SYMBOL_FILTERED` | Symbol excluded | `symbol`, `reason` |

### Signal Events

| Event | Description | Payload |
|-------|-------------|---------|
| `SIGNAL_COMPUTED` | Signal generated | `symbol`, `signal_type`, `score` |
| `TRADE_PROPOSED` | Trade proposed | `symbol`, `side`, `entry_price`, `stop_price` |

### Risk Events

| Event | Description | Payload |
|-------|-------------|---------|
| `RISK_APPROVED` | Trade approved | `symbol`, `trade_id` |
| `RISK_REJECTED` | Trade rejected | `symbol`, `reasons` |
| `ENTRY_SKIPPED` | Entry skipped | `symbol`, `reason` |

### Execution Events

| Event | Description | Payload |
|-------|-------------|---------|
| `ORDER_PLACED` | Order sent | `symbol`, `side`, `quantity`, `price`, `order_type`, `client_order_id` |
| `ORDER_FILLED` | Order filled | `symbol`, `side`, `quantity`, `price`, `fee`, `order_id` |
| `ORDER_PARTIAL_FILL` | Partial fill | `symbol`, `filled_qty`, `remaining_qty`, `price` |
| `ORDER_CANCELLED` | Order cancelled | `symbol`, `order_id`, `reason` |
| `ORDER_EXPIRED` | Order expired | `symbol`, `order_id`, `reason` |

### Position Events

| Event | Description | Payload |
|-------|-------------|---------|
| `POSITION_OPENED` | Position opened | `symbol`, `side`, `quantity`, `entry_price`, `leverage` |
| `POSITION_UPDATED` | Position modified | `symbol`, `field`, `old_value`, `new_value` |
| `POSITION_CLOSED` | Position closed | `symbol`, `exit_price`, `pnl`, `reason` |
| `STOP_TRIGGERED` | Stop hit | `symbol`, `stop_type`, `trigger_price` |

### Account Events

| Event | Description | Payload |
|-------|-------------|---------|
| `ACCOUNT_SETTING_UPDATED` | Setting changed | `setting`, `value` |
| `ACCOUNT_SETTING_FAILED` | Setting failed | `setting`, `error` |

### Alert Events

| Event | Description | Payload |
|-------|-------------|---------|
| `CIRCUIT_BREAKER_TRIGGERED` | Circuit breaker | `reason` |
| `MANUAL_INTERVENTION` | Manual review needed | `action`, `reason` |
| `MANUAL_REVIEW_ACKNOWLEDGED` | Review cleared | `reason`, `previously_flagged` |

### Reconciliation Events

| Event | Description | Payload |
|-------|-------------|---------|
| `RECONCILIATION_COMPLETED` | Recon done | `discrepancies` |

### Watchdog Events

| Event | Description | Payload |
|-------|-------------|---------|
| `PROTECTIVE_ORDERS_VERIFIED` | Orders OK | `symbol`, `orders` |
| `PROTECTIVE_ORDERS_MISSING` | Orders missing | `symbol`, `expected` |
| `PROTECTIVE_ORDERS_REPLACED` | Orders replaced | `symbol`, `old_orders`, `new_orders` |

### Cycle Events

| Event | Description | Payload |
|-------|-------------|---------|
| `TRADE_CYCLE_COMPLETED` | Strategy cycle done | `candidates`, `selected`, `summary` |

---

## Event Flow

### Publishing Events

```python
# In any module with access to EventBus
await event_bus.publish(
    EventType.ORDER_PLACED,
    {
        "symbol": "BTCUSDT",
        "side": "BUY",
        "quantity": 0.001,
        "price": 42000.0,
        "order_type": "LIMIT",
        "client_order_id": "entry_123"
    },
    {"source": "execution_engine"}
)
```

### Handling Events

```python
# Register handler
async def handle_order_filled(event: Event) -> None:
    symbol = event.payload["symbol"]
    quantity = event.payload["quantity"]
    price = event.payload["price"]
    # Process fill...

event_bus.register(EventType.ORDER_FILLED, handle_order_filled)
```

### Event Processing Pipeline

```
Producer               EventBus                Handlers
   │                      │                       │
   │  publish(type,       │                       │
   │  payload, metadata)  │                       │
   ├─────────────────────>│                       │
   │                      │                       │
   │                      │  1. Create Event      │
   │                      │  2. Assign sequence   │
   │                      │  3. Persist to ledger │
   │                      │                       │
   │                      │  4. Notify handlers   │
   │                      ├──────────────────────>│
   │                      │                       │
   │                      │                       │  Process event
   │                      │                       │
   │                      │<──────────────────────│
   │                      │                       │
   │<─────────────────────│                       │
   │  return Event        │                       │
```

---

## State Reconstruction

### On Startup

```python
# Load all events from ledger
events = ledger.load_all()

# Rebuild state by replaying events
state_manager.rebuild(events)

# State now reflects all historical events
current_state = state_manager.state
```

### State Update Flow

```python
# When event is published
async def apply_event(event: Event):
    state_manager.apply_event(event)

# Register for all event types
for event_type in EventType:
    event_bus.register(event_type, apply_event)
```

### TradingState Structure

```python
@dataclass
class TradingState:
    equity: float
    peak_equity: float
    realized_pnl_today: float
    daily_loss: float
    consecutive_losses: int
    cooldown_until: datetime | None
    circuit_breaker_active: bool
    requires_manual_review: bool
    last_reconciliation: datetime | None
    last_event_sequence: int
    universe: list[str]
    positions: dict[str, Position]
    open_orders: dict[str, Order]
    news_risk_flags: dict[str, NewsRisk]
```

---

## Adding New Event Types

### 1. Define Event Type

```python
# src/ledger/events.py
class EventType(str, Enum):
    # ... existing types ...
    MY_NEW_EVENT = "MyNewEvent"
```

### 2. Publish Events

```python
# In your module
await event_bus.publish(
    EventType.MY_NEW_EVENT,
    {"field1": value1, "field2": value2},
    {"source": "my_module"}
)
```

### 3. Handle State Updates (if needed)

```python
# src/ledger/state.py
def apply_event(self, event: Event) -> None:
    if event.event_type == EventType.MY_NEW_EVENT:
        self._handle_my_new_event(event)

def _handle_my_new_event(self, event: Event) -> None:
    # Update state based on event payload
    self.state.some_field = event.payload["field1"]
```

### 4. Register Handlers

```python
# src/main.py
async def my_handler(event: Event) -> None:
    # Handle event
    pass

event_bus.register(EventType.MY_NEW_EVENT, my_handler)
```

---

## Event Ledger Format

Events are stored in JSONL format (one JSON object per line):

**File**: `data/ledger/events.jsonl`

```json
{"event_id":"550e8400-e29b-41d4-a716-446655440000","event_type":"SystemStarted","timestamp":"2024-01-15T10:00:00.000Z","sequence_num":1,"payload":{"version":"0.1.0"},"metadata":{}}
{"event_id":"550e8400-e29b-41d4-a716-446655440001","event_type":"UniverseUpdated","timestamp":"2024-01-15T10:00:01.000Z","sequence_num":2,"payload":{"symbols":["BTCUSDT","ETHUSDT"]},"metadata":{"source":"universe_selector"}}
```

### Sequence File

Separate file tracks the next sequence number:

**File**: `data/ledger/sequence.txt`

```
1235
```

---

## Best Practices

### Event Payload Guidelines

1. **Include all relevant data**: Events should be self-contained
2. **Use primitive types**: Strings, numbers, booleans, arrays, objects
3. **Avoid circular references**: Events must be JSON-serializable
4. **Include timestamps**: For time-sensitive data

### Metadata Guidelines

1. **Always include source**: `{"source": "module_name"}`
2. **Add context**: User ID, request ID, etc.
3. **Don't duplicate payload**: Metadata is for context only

### State Handler Guidelines

1. **Be idempotent**: Same event applied twice = same state
2. **Handle missing fields**: Use defaults for optional payload fields
3. **Don't throw exceptions**: Log warnings for unexpected events

---

## Debugging Events

### View Recent Events

```bash
# Last 10 events
tail -10 data/ledger/events.jsonl | jq

# Filter by type
grep "OrderFilled" data/ledger/events.jsonl | jq

# Count by type
jq -r '.event_type' data/ledger/events.jsonl | sort | uniq -c
```

### Via API

```bash
# Recent events
curl http://localhost:8000/events?tail=50 | jq

# Filter client-side
curl http://localhost:8000/events | jq '.events[] | select(.event_type == "SignalComputed")'
```

### Rebuild State Manually

```python
from src.ledger import EventLedger, StateManager
from src.config.settings import load_settings

settings = load_settings()
ledger = EventLedger(settings.storage.ledger_path)
state_manager = StateManager(
    initial_equity=100.0,
    risk_config=settings.risk,
    news_config=settings.news,
)

# Rebuild from all events
events = ledger.load_all()
state_manager.rebuild(events)

# Inspect state
print(state_manager.state)
```

---

## Related Documentation

- [System Overview](../00_architecture/01_SystemOverview.md) - Architecture
- [Module Reference](../00_architecture/02_ModuleReference.md) - Ledger module details
- [Data Schemas](../04_reference/03_DataSchemas.md) - Event format reference
