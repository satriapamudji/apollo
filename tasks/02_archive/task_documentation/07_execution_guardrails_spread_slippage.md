# Task 07: Execution Guardrails - Spread/Slippage Enforcement

## Overview

This document describes the implementation of pre-trade spread and slippage checks in the execution engine to prevent trading during unfavorable market conditions.

## Before

The execution engine (`src/execution/engine.py`) had no microstructure checks before placing orders. Orders could be submitted even when:
- The bid-ask spread was abnormally wide (indicating low liquidity or high volatility)
- The current market price had moved significantly from the signal's expected entry price

This could lead to poor execution prices and increased slippage, especially during volatile periods or on low-liquidity altcoins.

## After

### Changes Made

#### 1. Added `get_book_ticker()` method (`src/connectors/rest_client.py:90-93`)

```python
async def get_book_ticker(self, symbol: str) -> dict[str, Any]:
    """Get best bid/ask prices for a symbol from the book ticker endpoint."""
    params = {"symbol": symbol}
    return await self._request("GET", "/fapi/v1/ticker/bookTicker", params=params, weight=1)
```

This method fetches the current best bid and ask prices from Binance's book ticker endpoint.

#### 2. Added `_check_spread_slippage()` method (`src/execution/engine.py:855-915`)

New method that:
- Fetches the book ticker for the symbol
- Calculates spread percentage: `((ask - bid) / mid_price) * 100`
- Compares spread against `max_spread_pct` (default 0.3%)
- Calculates slippage: deviation between expected entry and current mark price
- Compares slippage against `max_slippage_pct` (default 0.5%)
- Returns (True, None) if acceptable, (False, reason) if rejected
- Fails open (allows trading) if book ticker cannot be fetched

#### 3. Integrated checks into `execute_entry()` (`src/execution/engine.py:113-134`)

Added pre-trade checks after duplicate order validation and before `_ensure_account_settings()`:

```python
# Check spread and slippage constraints before proceeding
is_acceptable, rejection_reason = await self._check_spread_slippage(
    proposal, proposal.entry_price
)
if not is_acceptable:
    await self.event_bus.publish(
        EventType.RISK_REJECTED,
        {
            "symbol": proposal.symbol,
            "reason": rejection_reason,
            "trade_id": proposal.trade_id,
            "entry_price": proposal.entry_price,
        },
        {"source": "execution_engine", "trade_id": proposal.trade_id},
    )
    self.log.info(
        "trade_rejected_microstructure",
        symbol=proposal.symbol,
        reason=rejection_reason,
        trade_id=proposal.trade_id,
    )
    return
```

#### 4. Added thinking log method (`src/monitoring/thinking_log.py:143-163`)

New method `log_pretrade_rejection()` for logging microstructure rejections to `logs/thinking.jsonl`:

```python
def log_pretrade_rejection(
    self,
    proposal: TradeProposal,
    rejection_type: str,
    reason: str,
    details: dict[str, Any] | None = None,
) -> None:
    """Log a pre-trade rejection due to microstructure constraints."""
```

### Configuration

The following settings (already existed in `ExecutionConfig`) control the thresholds:

| Setting | Default | Range | Description |
|---------|---------|-------|-------------|
| `max_spread_pct` | 0.3% | 0.05 - 1.0% | Maximum acceptable bid-ask spread |
| `max_slippage_pct` | 0.5% | 0.1 - 2.0% | Maximum acceptable slippage from expected entry |

Configured via `config.yaml` under `execution` section.

## Why This Matters

1. **Prevents bad entries**: Wide spreads often indicate:
   - Low liquidity (small market cap pairs)
   - High volatility (during news events)
   - Market stress (fast-moving markets)

2. **Reduces slippage**: By checking deviation from expected entry, we avoid:
   - Chasing prices that have already moved
   - Trading at stale signal prices

3. **Auditability**: Rejections are:
   - Logged to ledger via `RISK_REJECTED` events
   - Logged to `logs/thinking.jsonl` for analysis
   - Include detailed reason strings for debugging

## Event Flow

```
Signal → Risk Check → execute_entry() → _check_spread_slippage()
                                              ↓
                                    Spread OK? Slippage OK?
                                              ↓
                                    If NO → RISK_REJECTED event → Ledger + Thinking Log
                                              ↓
                                    If YES → Continue with order placement
```

## Backward Compatibility

- Checks are skipped in simulation mode (`self.simulate = True`)
- If book ticker fetch fails, trading is allowed (fail-open for safety)
- Existing configuration values are used with same defaults
