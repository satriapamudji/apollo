# AI-Assisted Binance USDⓈ-M Perpetual Futures Trend-Following Bot

## Technical Specification v1.0

---

## DISCLAIMER AND SAFETY NOTICE

> **THIS IS NOT FINANCIAL ADVICE.**
>
> Trading cryptocurrency perpetual futures involves substantial risk of loss. Losses can exceed your initial deposit. Past performance does not guarantee future results.
>
> **EXPLICITLY FORBIDDEN:**
> - Unlimited leverage or leverage exceeding configured hard caps
> - Doubling-down after losses (martingale strategies)
> - Self-modifying live trading logic without offline validation
> - Deploying to live trading without completing the phased rollout
>
> **REQUIRED PHASED ROLLOUT:**
> 1. Backtest on historical data (minimum 6 months)
> 2. Paper trading on Binance Testnet (minimum 2 weeks)
> 3. Live trading with minimum size ($5-10 notional) for 2 weeks
> 4. Gradual scale-up with continuous monitoring
>
> By using this system, you acknowledge these risks and accept full responsibility for any losses.

---

## 1. Overview

This system is an AI-assisted automated trading bot for Binance USDⓈ-M perpetual futures contracts, designed for multi-day trend-following positions (2-7 day holding periods) with a $100 starting equity constraint. The architecture strictly separates "thinking" (LLM-powered analysis and classification) from "doing" (deterministic risk management and execution), ensuring that an AI model can propose and classify but never directly place orders. The system uses event sourcing for full auditability and restart safety, implements hard-coded risk limits that cannot be overridden at runtime, and reconciles state on every startup to handle manual interventions or unexpected shutdowns gracefully.

**What "AI-Assisted" means:** The LLM component acts as a news classifier and risk modifier—it reads RSS feeds, extracts relevant entities, classifies sentiment/event-type, and outputs structured JSON recommendations. These recommendations modulate the scoring engine and risk parameters but do not bypass deterministic risk gates. The LLM never places orders, modifies position sizes beyond bounds, or changes core trading rules.

---

## 2. Goals and Non-Goals

### Goals

| ID | Goal | Constraint |
|----|------|------------|
| G1 | Execute multi-day trend-following trades on USDⓈ-M perps | 2-7 day holding period |
| G2 | Operate safely with $100 starting equity | Max 1 position, micro-sizing |
| G3 | Use news/LLM as risk modifier, not sole signal | Classification only |
| G4 | Full restart safety and auditability | Event sourcing |
| G5 | Deterministic risk enforcement | Hard-coded, non-bypassable |
| G6 | Reconcile manual interventions | Detect external changes |
| G7 | Observable and alertable | Structured logs, metrics |

### Non-Goals

| ID | Non-Goal | Rationale |
|----|----------|-----------|
| NG1 | Predicting tops/bottoms | Trend-following only; no reversal calls |
| NG2 | Live self-learning that modifies trading rules | All learning is offline; changes require explicit promotion |
| NG3 | High-frequency trading | Multi-day holds; no sub-minute execution |
| NG4 | Spot trading or margin trading | USDⓈ-M perps only |
| NG5 | Multi-exchange arbitrage | Binance Futures only |
| NG6 | Full portfolio optimization | Max 1-2 positions for $100 account |
| NG7 | Martingale or grid strategies | Explicitly forbidden |

---

## 3. Assumptions

| Category | Assumption |
|----------|------------|
| **Market** | Binance USDⓈ-M perpetual futures only |
| **Collateral** | USDT margin mode |
| **Position Mode** | One-way mode (not hedge mode) |
| **Starting Equity** | $100 (scalable to larger accounts) |
| **Max Positions** | 1 initially; optionally 2 after proven track record |
| **Holding Period** | 2-7 days typical |
| **Analysis Timeframes** | 1D for trend direction, 4H for entry timing |
| **Execution Timeframe** | 4H candle close or triggered conditions |
| **Leverage** | Max 5x (hard cap), default 2-3x |
| **Trading Hours** | 24/7 (crypto markets) |
| **Connectivity** | Stable internet; system handles reconnections |
| **Manual Override** | User may manually close positions; system reconciles |

---

## 4. System Architecture

### Module Boundaries

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              EXTERNAL SYSTEMS                               │
├─────────────────┬─────────────────┬─────────────────┬───────────────────────┤
│  Binance REST   │  Binance WS     │   RSS Feeds     │      LLM API          │
│  (orders, info) │  (market, user) │   (news)        │   (classification)    │
└────────┬────────┴────────┬────────┴────────┬────────┴───────────┬───────────┘
         │                 │                 │                     │
         ▼                 ▼                 ▼                     ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           CONNECTOR LAYER                                   │
├─────────────────┬─────────────────┬─────────────────┬───────────────────────┤
│ REST Connector  │  WS Connector   │  News Ingester  │   LLM Connector       │
│ (rate-limited)  │  (reconnecting) │  (dedupe)       │   (async, cached)     │
└────────┬────────┴────────┬────────┴────────┬────────┴───────────┬───────────┘
         │                 │                 │                     │
         ▼                 ▼                 ▼                     ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              EVENT BUS                                      │
│            (all events appended to EVENT LEDGER before processing)          │
└─────────────────────────────────┬───────────────────────────────────────────┘
                                  │
         ┌────────────────────────┼────────────────────────┐
         ▼                        ▼                        ▼
┌─────────────────┐    ┌─────────────────────┐    ┌─────────────────┐
│ FEATURE PIPELINE│    │   NEWS PROCESSOR    │    │  STATE MANAGER  │
│ - OHLCV cache   │    │ - Entity extraction │    │ - Positions     │
│ - TA indicators │    │ - LLM classification│    │ - Orders        │
│ - Funding rates │    │ - Risk flags        │    │ - Equity        │
└────────┬────────┘    └─────────┬───────────┘    └────────┬────────┘
         │                       │                         │
         └───────────────────────┼─────────────────────────┘
                                 ▼
                    ┌────────────────────────┐
                    │    SIGNAL ENGINE       │
                    │ - Trend scoring        │
                    │ - Entry quality        │
                    │ - News modifier        │
                    │ - Composite score      │
                    └───────────┬────────────┘
                                │
                                ▼
                    ┌────────────────────────┐
                    │     RISK ENGINE        │
                    │ - Position sizing      │
                    │ - Hard limit gates     │
                    │ - Circuit breakers     │
                    │ - Approval/Rejection   │
                    └───────────┬────────────┘
                                │
                                ▼
                    ┌────────────────────────┐
                    │   EXECUTION ENGINE     │
                    │ - Order placement      │
                    │ - SL/TP management     │
                    │ - Verification loop    │
                    │ - Idempotency          │
                    └───────────┬────────────┘
                                │
                                ▼
                    ┌────────────────────────┐
                    │   MONITORING/ALERTS    │
                    │ - Metrics export       │
                    │ - Log aggregation      │
                    │ - Alert triggers       │
                    └────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│                        OFFLINE COMPONENTS                                   │
├─────────────────────────────────┬───────────────────────────────────────────┤
│       LEARNING ENGINE           │        BACKTESTER                         │
│  - Parameter optimization       │  - Historical simulation                  │
│  - Walk-forward validation      │  - Performance metrics                    │
│  - Model versioning             │  - Slippage modeling                      │
└─────────────────────────────────┴───────────────────────────────────────────┘
```

### Data Flow Summary

1. **Inbound**: Market data (WS) → Event Ledger → Feature Pipeline
2. **News**: RSS → Dedupe → LLM Classification → Event Ledger → News Processor
3. **Signal**: Features + News Flags → Signal Engine → TradeProposal event
4. **Risk**: TradeProposal → Risk Engine (hard gates) → Approved/Rejected event
5. **Execution**: Approved → Execution Engine → Order events → Verification
6. **State**: All events → State Manager (rebuild on restart)

---

## 5. Binance Integration

### Required Capabilities

| Purpose | Endpoint/Stream | Notes |
|---------|-----------------|-------|
| Exchange metadata | `GET /fapi/v1/exchangeInfo` | Symbol filters, tick/lot size, rate limits |
| 24h ticker stats | `GET /fapi/v1/ticker/24hr` | Volume for universe selection |
| Klines (candles) | `GET /fapi/v1/klines` | Historical OHLCV; respect weight limits |
| Mark price | `GET /fapi/v1/premiumIndex` | Mark price, funding rate, next funding time |
| Real-time klines | WS `<symbol>@kline_<interval>` | 4H candles for live updates |
| Mark price stream | WS `<symbol>@markPrice@1s` | Real-time mark/funding |
| Account info | `GET /fapi/v2/account` | Balances, positions, margin |
| Position risk | `GET /fapi/v2/positionRisk` | Current position details |
| Open orders | `GET /fapi/v1/openOrders` | Active orders |
| Place order | `POST /fapi/v1/order` | New orders with `newClientOrderId` |
| Cancel order | `DELETE /fapi/v1/order` | Cancel by `origClientOrderId` |
| User data stream | WS (listenKey) | ORDER_TRADE_UPDATE, ACCOUNT_UPDATE |
| Listen key | `POST /fapi/v1/listenKey` | Create/keepalive user stream |

### Rate Limit Handling

- **Do not hardcode** specific weight limits; read from `exchangeInfo` or config
- Implement a request-weight tracker per minute window
- Back off when approaching 80% of limit
- Retry with exponential backoff on 429 responses
- Log all rate-limit-related events

### WebSocket Management

- Implement automatic reconnection with exponential backoff
- Ping/pong handling for connection health
- Resubscribe to streams after reconnection
- Track `lastUpdateId` for orderbook consistency (if used)
- Refresh listenKey every 30 minutes (before 60-minute expiry)

---

## 6. Universe Selection (Pairs)

### Selection Process

```
DAILY at 00:00 UTC (or on startup):

1. FETCH all USDⓈ-M perpetual symbols from exchangeInfo
   - Filter: status = "TRADING", contractType = "PERPETUAL", quoteAsset = "USDT"

2. FETCH 24hr ticker for all symbols
   - Sort by quoteVolume descending
   - Take top 30 candidates

3. APPLY filters to each candidate:

   a. Liquidity filter:
      - 24h quoteVolume >= $50,000,000 (configurable)

   b. Symbol filter compliance:
      - minNotional <= $6 (for $100 account with 5x max leverage)
      - Verify tick size and lot size are acceptable

   c. Spread proxy (optional, requires orderbook):
      - If bid-ask spread > 0.1%, deprioritize

   d. Funding rate filter:
      - Abs(fundingRate) < 0.1% per 8h (avoid extreme funding)

4. OUTPUT: Top 3-5 symbols meeting all criteria
   - Store as TRADEABLE_UNIVERSE

5. CONSTRAINT: Max 1 open position initially
   - When position exists, only monitor that symbol + check for exit
   - Universe is for selecting NEW entry candidates
```

### Pseudocode

```python
def select_universe():
    exchange_info = rest.get_exchange_info()
    perp_symbols = [
        s for s in exchange_info['symbols']
        if s['status'] == 'TRADING'
        and s['contractType'] == 'PERPETUAL'
        and s['quoteAsset'] == 'USDT'
    ]

    tickers = rest.get_24hr_tickers()
    ticker_map = {t['symbol']: t for t in tickers}

    candidates = []
    for sym in perp_symbols:
        ticker = ticker_map.get(sym['symbol'])
        if not ticker:
            continue

        quote_volume = float(ticker['quoteVolume'])
        if quote_volume < config.MIN_QUOTE_VOLUME:
            continue

        min_notional = get_filter_value(sym, 'MIN_NOTIONAL')
        if min_notional > config.MAX_MIN_NOTIONAL:
            continue

        funding = get_funding_rate(sym['symbol'])
        if abs(funding) > config.MAX_FUNDING_RATE:
            continue

        candidates.append({
            'symbol': sym['symbol'],
            'quote_volume': quote_volume,
            'filters': sym['filters'],
            'funding_rate': funding
        })

    candidates.sort(key=lambda x: x['quote_volume'], reverse=True)
    return candidates[:config.UNIVERSE_SIZE]  # 3-5 symbols
```

---

## 7. Strategy: Trend Following (Multi-day)

### Timeframes

| Timeframe | Purpose |
|-----------|---------|
| 1D (Daily) | Primary trend direction |
| 4H | Entry timing, stop placement |

### Technical Indicators (Minimal Set)

| Indicator | Parameters | Purpose |
|-----------|------------|---------|
| EMA (fast) | 8-period on 1D | Trend reference |
| EMA (slow) | 21-period on 1D | Trend filter |
| ATR | 14-period on 4H | Volatility, stop distance |
| RSI | 14-period on 4H | Overbought/oversold filter (optional) |

### Trend Direction Rules

```
UPTREND:
  - EMA(8) > EMA(21) on 1D
  - Price > EMA(21) on 1D
  - EMA(8) slope positive (current > 3 bars ago)

DOWNTREND:
  - EMA(8) < EMA(21) on 1D
  - Price < EMA(21) on 1D
  - EMA(8) slope negative

NO_TREND:
  - Conditions not clearly met
  - Action: No new entries
```

### Entry Style (Default: Pullback)

**Pullback Entry (Default):**
```
LONG setup:
  - 1D trend = UPTREND
  - 4H price pulls back to or below EMA(21) on 4H
  - 4H candle closes back above EMA(8) on 4H
  - RSI(14) on 4H > 40 (not deeply oversold continuation)

SHORT setup:
  - 1D trend = DOWNTREND
  - 4H price rallies to or above EMA(21) on 4H
  - 4H candle closes back below EMA(8) on 4H
  - RSI(14) on 4H < 60
```

**Breakout Entry (Alternative):**
```
LONG setup:
  - 1D trend = UPTREND
  - Price breaks above 20-period high on 4H
  - Volume > 1.5x 20-period average (optional)

SHORT setup:
  - 1D trend = DOWNTREND
  - Price breaks below 20-period low on 4H
```

### Exit Rules

| Exit Type | Rule |
|-----------|------|
| Initial Stop | 2.0 × ATR(14) from entry price |
| Trailing Stop | After 1.5 × ATR profit, trail at 1.5 × ATR |
| Time Stop | If position held > 7 days with < 0.5 × ATR profit, exit at market |
| Trend Invalidation | If 1D trend flips, exit at next 4H close |
| Take Profit | Optional: 3.0 × ATR (risk:reward = 1.5:1) |

### Scoring Model

```
COMPOSITE_SCORE = (
    TREND_SCORE × 0.35 +
    VOLATILITY_SCORE × 0.15 +
    ENTRY_QUALITY × 0.25 +
    FUNDING_PENALTY × 0.10 +
    NEWS_MODIFIER × 0.15
)

Threshold: COMPOSITE_SCORE >= 0.65 to consider entry
```

#### Component Definitions

**TREND_SCORE (0.0 - 1.0):**
```
trend_alignment = 1.0 if EMA(8) > EMA(21) else 0.0  # for longs
slope_strength = normalize(abs(ema8 - ema8_3bars_ago) / atr, 0, 0.5)
price_position = 1.0 if price > ema21 else 0.5

TREND_SCORE = (trend_alignment × 0.5) + (slope_strength × 0.3) + (price_position × 0.2)
```

**VOLATILITY_SCORE (0.0 - 1.0):**
```
atr_percent = ATR(14) / price × 100

# Sweet spot: 2-5% ATR for multi-day holds
if 2.0 <= atr_percent <= 5.0:
    VOLATILITY_SCORE = 1.0
elif atr_percent < 2.0:
    VOLATILITY_SCORE = atr_percent / 2.0  # Too quiet
else:
    VOLATILITY_SCORE = max(0, 1.0 - (atr_percent - 5.0) / 5.0)  # Too volatile
```

**ENTRY_QUALITY (0.0 - 1.0):**
```
# For pullback entries:
pullback_depth = abs(price - ema21) / atr
optimal_depth = 0.5 to 1.0 ATR

if 0.5 <= pullback_depth <= 1.0:
    ENTRY_QUALITY = 1.0
elif pullback_depth < 0.5:
    ENTRY_QUALITY = pullback_depth / 0.5  # Shallow pullback
else:
    ENTRY_QUALITY = max(0, 1.0 - (pullback_depth - 1.0) / 1.0)  # Too deep
```

**FUNDING_PENALTY (0.0 - 1.0):**
```
funding_rate = current 8h funding rate

# Penalty if funding works against position
if (LONG and funding_rate > 0.03%) or (SHORT and funding_rate < -0.03%):
    FUNDING_PENALTY = 1.0 - min(abs(funding_rate) / 0.1, 1.0)
else:
    FUNDING_PENALTY = 1.0  # Funding favorable or neutral
```

**NEWS_MODIFIER (0.0 - 1.0):**
```
# From LLM classification (see Section 8)
if news_risk_flag == "HIGH":
    NEWS_MODIFIER = 0.0  # Block entry
elif news_risk_flag == "MEDIUM":
    NEWS_MODIFIER = 0.5
else:
    NEWS_MODIFIER = 1.0  # No adverse news
```

---

## 8. News + LLM Integration

### RSS Ingestion

**Source Allowlist (configurable):**
```yaml
news_sources:
  - name: "CoinDesk"
    url: "https://www.coindesk.com/arc/outboundfeeds/rss/"
    priority: 1
  - name: "CoinTelegraph"
    url: "https://cointelegraph.com/rss"
    priority: 1
  - name: "Decrypt"
    url: "https://decrypt.co/feed"
    priority: 2
  - name: "TheBlock"
    url: "https://www.theblock.co/rss.xml"
    priority: 1
```

**Ingestion Rules:**
- Poll RSS feeds every 15 minutes
- Deduplicate by (source, title_hash, published_date)
- Discard items older than 24 hours
- Store raw items in event ledger before processing
- Rate-limit LLM calls (max 10/minute)

### LLM Responsibilities

The LLM **MUST ONLY**:
1. Classify event type (regulatory, exchange, technical, macro, project-specific)
2. Extract mentioned symbols/entities
3. Assess sentiment and risk level
4. Output structured JSON

The LLM **MUST NEVER**:
- Place or suggest specific orders
- Override risk parameters
- Modify position sizes directly
- Access trading APIs

### LLM Prompt Template

```
You are a crypto news classifier. Analyze the following news item and output ONLY valid JSON.

NEWS:
Title: {title}
Source: {source}
Published: {published_date}
Content: {content_snippet}

OUTPUT FORMAT:
{
  "event_type": "regulatory|exchange|technical|macro|project|other",
  "symbols_mentioned": ["BTC", "ETH", ...],
  "sentiment": "bullish|bearish|neutral",
  "risk_level": "HIGH|MEDIUM|LOW",
  "risk_reason": "brief explanation if HIGH or MEDIUM",
  "confidence": 0.0-1.0,
  "summary": "one sentence summary"
}

RULES:
- HIGH risk: exchange hacks, regulatory bans, major delistings, smart contract exploits
- MEDIUM risk: regulatory uncertainty, large liquidations, notable FUD
- LOW risk: routine news, minor updates, general market commentary
- Only mention symbols explicitly named, do not infer
```

### LLM Output Schema

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "required": ["event_type", "symbols_mentioned", "sentiment", "risk_level", "confidence", "summary"],
  "properties": {
    "event_type": {
      "type": "string",
      "enum": ["regulatory", "exchange", "technical", "macro", "project", "other"]
    },
    "symbols_mentioned": {
      "type": "array",
      "items": {"type": "string"}
    },
    "sentiment": {
      "type": "string",
      "enum": ["bullish", "bearish", "neutral"]
    },
    "risk_level": {
      "type": "string",
      "enum": ["HIGH", "MEDIUM", "LOW"]
    },
    "risk_reason": {
      "type": "string"
    },
    "confidence": {
      "type": "number",
      "minimum": 0,
      "maximum": 1
    },
    "summary": {
      "type": "string",
      "maxLength": 200
    }
  }
}
```

### How News Modifies Trading

| Risk Level | Impact on Trading |
|------------|-------------------|
| HIGH | Block all new entries for mentioned symbols for 24h |
| HIGH | Tighten stops on existing positions to 1.0 × ATR |
| HIGH | If confidence > 0.8, consider immediate exit |
| MEDIUM | Reduce position size by 50% |
| MEDIUM | Raise entry threshold to 0.75 (from 0.65) |
| MEDIUM | Tighten stops to 1.5 × ATR |
| LOW | No modification |

**News Risk Decay:**
- HIGH risk decays to MEDIUM after 12 hours
- MEDIUM risk decays to LOW after 6 hours
- Risk resets if new related news arrives

---

## 9. Risk Management (Hard Rules)

### Core Risk Parameters

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Risk per trade | 1.0% of equity | $1 risk on $100 account |
| Max leverage | 5x | Hard cap, never exceeded |
| Default leverage | 2-3x | Normal operation |
| Max daily loss | 3% of equity | $3 on $100 account |
| Max drawdown | 10% of equity | $10 on $100 account |
| Max consecutive losses | 3 | Circuit breaker trigger |
| Position limit | 1 (initially) | Single position only |
| Max exposure per symbol | 100% of allowed | Only one position anyway |
| Cooldown after loss | 4 hours | Prevent revenge trading |
| Cooldown after max daily loss | 24 hours | Force reflection |

### Position Sizing Formula

```python
def calculate_position_size(equity, entry_price, stop_price, max_leverage):
    """
    Risk-based position sizing with leverage cap.
    """
    risk_amount = equity * RISK_PER_TRADE  # e.g., $1 on $100
    stop_distance_pct = abs(entry_price - stop_price) / entry_price

    # Position size based on risk
    position_value = risk_amount / stop_distance_pct

    # Apply leverage cap
    max_position_value = equity * max_leverage
    position_value = min(position_value, max_position_value)

    # Check min notional
    if position_value < symbol_min_notional:
        return 0  # Cannot trade, too small

    # Calculate quantity
    quantity = position_value / entry_price
    quantity = round_to_lot_size(quantity, symbol_lot_size)

    return quantity
```

### Pre-Order Risk Checklist

The Risk Engine MUST evaluate ALL conditions before approving any order:

```
PRE-ORDER RISK CHECKLIST
========================

□ 1. EQUITY CHECK
    - Current equity >= $10 (minimum operational threshold)
    - Unrealized PnL + realized daily PnL > -3% (daily loss limit)

□ 2. DRAWDOWN CHECK
    - Peak equity - current equity < 10% of peak
    - If exceeded: KILL_SWITCH activated

□ 3. POSITION LIMIT CHECK
    - Current open positions < MAX_POSITIONS (1)
    - If entry order: must have room for new position
    - If exit order: always allowed

□ 4. LEVERAGE CHECK
    - Proposed position notional / equity <= MAX_LEVERAGE (5x)
    - Recalculate after every fill

□ 5. CONSECUTIVE LOSS CHECK
    - Consecutive losses < 3
    - If at limit: 4-hour cooldown required

□ 6. COOLDOWN CHECK
    - Time since last loss > cooldown period
    - Time since max daily loss > 24 hours

□ 7. SYMBOL EXPOSURE CHECK
    - Not already exposed to this symbol (for entries)

□ 8. STOP LOSS VERIFICATION
    - Every entry MUST have accompanying SL order
    - SL distance <= 3 × ATR (prevent unreasonable stops)

□ 9. FUNDING RATE CHECK
    - If abs(funding) > 0.1%: warn and reduce size by 25%
    - If abs(funding) > 0.2%: block entry

□ 10. NEWS RISK CHECK
    - No HIGH risk flag for symbol in last 24h
    - If MEDIUM: size reduced per rules

□ 11. MARGIN RATIO CHECK
    - Projected margin ratio after trade < 80%
    - Leave buffer for adverse moves

□ 12. ORDER VALIDITY CHECK
    - Quantity meets lot size filter
    - Notional meets min notional filter
    - Price (if limit) meets tick size filter

RESULT: ALL checks pass → APPROVED
        ANY check fails → REJECTED with reason code
```

### Circuit Breakers

| Trigger | Action | Reset Condition |
|---------|--------|-----------------|
| Daily loss >= 3% | Halt all trading for 24h | Next calendar day |
| Drawdown >= 10% | KILL_SWITCH: flatten all, cancel all | Manual reset only |
| 3 consecutive losses | 4-hour cooldown | Time elapsed |
| 5 losses in 24h | 24-hour cooldown | Time elapsed |
| Exchange disconnect > 5 min | Cancel open orders, alert | Reconnection + manual ack |
| Margin ratio > 90% | Reduce position by 50% | Margin ratio < 70% |

### Kill Switch Behavior

When KILL_SWITCH is activated:
1. Cancel ALL open orders immediately
2. Close ALL positions at market
3. Set system state to HALTED
4. Send critical alert to all channels
5. Require manual intervention to restart
6. Log complete state snapshot before and after

---

## 10. Execution Engine

### Order Types Used

| Scenario | Order Type | Parameters |
|----------|------------|------------|
| Entry | LIMIT (GTC) | Price at or better than signal |
| Entry (urgent) | MARKET | When breakout confirmed |
| Stop Loss | STOP_MARKET | `stopPrice`, `reduceOnly=true` |
| Take Profit | TAKE_PROFIT_MARKET | `stopPrice`, `reduceOnly=true` |
| Exit (manual trigger) | MARKET | `reduceOnly=true` |

### Idempotency

Every order placed must have a unique `newClientOrderId`:
```
Format: {strategy}_{symbol}_{side}_{timestamp}_{nonce}
Example: TREND_BTCUSDT_BUY_1704067200000_a1b2c3
```

- Before placing, check if order with same logical intent exists
- On restart, query open orders and match against intended state
- Never place duplicate orders for same signal

### Execution Sequence

```
ENTRY FLOW:
==========

1. SIGNAL_ENGINE emits TradeProposal event
   │
   ▼
2. RISK_ENGINE evaluates checklist
   │
   ├─► REJECTED: Log reason, emit RiskRejected event, END
   │
   ▼
3. APPROVED: Emit RiskApproved event
   │
   ▼
4. EXECUTION_ENGINE calculates:
   - Entry price (limit: best bid/ask ± buffer, or market)
   - Stop loss price (entry ± ATR multiple)
   - Position quantity (from sizing formula)
   - Generate clientOrderId
   │
   ▼
5. PRE-FLIGHT CHECK:
   - Order not already placed (idempotency)
   - Account has sufficient margin
   - Symbol is tradeable
   │
   ▼
6. PLACE ENTRY ORDER
   - Emit OrderPlaced event
   - Start verification timer (30 seconds for limit)
   │
   ▼
7. WAIT FOR FILL (via user stream)
   │
   ├─► FILLED: Emit Fill event, proceed to step 8
   ├─► PARTIAL: Update state, continue waiting
   ├─► TIMEOUT: Cancel order, emit OrderCancelled, END
   │
   ▼
8. PLACE PROTECTIVE ORDERS (SL, optional TP)
   - SL is MANDATORY
   - Emit OrderPlaced events
   │
   ▼
9. VERIFY EXCHANGE STATE
   - Query positions via REST
   - Confirm position matches expected
   - Confirm SL order is active
   │
   ├─► MATCH: Emit PositionOpened event, END SUCCESS
   │
   ▼
10. MISMATCH HANDLING:
    - Log discrepancy
    - Attempt correction (cancel/replace)
    - If cannot resolve: ALERT + manual intervention flag


EXIT FLOW:
=========

1. EXIT_TRIGGER (stop hit, signal, time, manual)
   │
   ▼
2. VERIFY position still exists
   │
   ├─► NO POSITION: Log, emit PositionAlreadyClosed, END
   │
   ▼
3. CANCEL related open orders (SL/TP if triggered differently)
   │
   ▼
4. PLACE EXIT ORDER (market, reduceOnly=true)
   │
   ▼
5. WAIT FOR FILL
   │
   ▼
6. VERIFY position is flat
   │
   ▼
7. EMIT PositionClosed event with PnL calculation
   │
   ▼
8. UPDATE statistics (win/loss streak, daily PnL)
```

### Retry Policy

```python
RETRY_CONFIG = {
    'max_attempts': 3,
    'initial_delay_ms': 1000,
    'max_delay_ms': 10000,
    'backoff_multiplier': 2,
    'retryable_errors': [
        'TIMEOUT',
        'NETWORK_ERROR',
        'RATE_LIMITED',  # After respecting retry-after
    ],
    'non_retryable_errors': [
        'INSUFFICIENT_BALANCE',
        'INVALID_QUANTITY',
        'SYMBOL_NOT_TRADING',
    ]
}
```

### Slippage Controls

| Control | Limit |
|---------|-------|
| Max slippage for market orders | 0.5% from mark price |
| Limit order buffer from mid | 0.05% |
| Abort if spread > | 0.3% |
| Max retry on slippage | 2 attempts |

---

## 11. State, Ledger, and Restart/Reconciliation

### Event Types

| Event Type | Description |
|------------|-------------|
| `MarketTick` | Price update from WebSocket |
| `CandleClose` | Completed candle (4H/1D) |
| `FundingUpdate` | Funding rate change |
| `NewsIngested` | Raw news item received |
| `NewsClassified` | LLM classification result |
| `UniverseUpdated` | Tradeable symbols changed |
| `SignalComputed` | Strategy signal calculated |
| `TradeProposed` | Entry/exit recommendation |
| `RiskApproved` | Risk engine approved action |
| `RiskRejected` | Risk engine rejected action |
| `OrderPlaced` | Order submitted to exchange |
| `OrderCancelled` | Order cancelled |
| `OrderFilled` | Order fully filled |
| `OrderPartialFill` | Partial fill received |
| `PositionOpened` | New position established |
| `PositionUpdated` | Position size/PnL changed |
| `PositionClosed` | Position fully exited |
| `StopTriggered` | SL/TP order triggered |
| `CircuitBreakerTriggered` | Risk limit hit |
| `ManualInterventionDetected` | External change detected |
| `SystemStarted` | Bot startup |
| `SystemStopped` | Graceful shutdown |
| `ReconciliationCompleted` | Startup reconciliation done |

### Event Schema

```json
{
  "event_id": "uuid-v4",
  "event_type": "OrderPlaced",
  "timestamp": "2024-01-15T10:30:00.000Z",
  "sequence_num": 12345,
  "payload": {
    "symbol": "BTCUSDT",
    "side": "BUY",
    "quantity": 0.001,
    "price": 42000.00,
    "order_type": "LIMIT",
    "client_order_id": "TREND_BTCUSDT_BUY_1704067200000_a1b2c3",
    "stop_price": null,
    "reduce_only": false
  },
  "metadata": {
    "source": "execution_engine",
    "correlation_id": "signal-uuid-xyz",
    "version": "1.0"
  }
}
```

### State Reconstruction

```python
def rebuild_state_from_ledger(ledger):
    """
    Replay all events to reconstruct current state.
    """
    state = {
        'positions': {},
        'open_orders': {},
        'equity': INITIAL_EQUITY,
        'realized_pnl_today': 0,
        'consecutive_losses': 0,
        'last_loss_time': None,
        'daily_loss': 0,
        'peak_equity': INITIAL_EQUITY,
        'news_risk_flags': {},
        'circuit_breaker_active': False
    }

    for event in ledger.iterate_from_start():
        apply_event_to_state(state, event)

    return state

def apply_event_to_state(state, event):
    handlers = {
        'OrderFilled': handle_fill,
        'PositionOpened': handle_position_open,
        'PositionClosed': handle_position_close,
        'CircuitBreakerTriggered': handle_circuit_breaker,
        'ManualInterventionDetected': handle_manual,
        # ... etc
    }
    handler = handlers.get(event['event_type'])
    if handler:
        handler(state, event['payload'])
```

### Startup Reconciliation

```
ON STARTUP:
===========

1. LOAD last known state from ledger
   │
   ▼
2. QUERY exchange via REST:
   - GET /fapi/v2/account (balances)
   - GET /fapi/v2/positionRisk (positions)
   - GET /fapi/v1/openOrders (orders)
   │
   ▼
3. COMPARE exchange state vs ledger state
   │
   ▼
4. FOR EACH discrepancy:
   │
   ├─► Position exists on exchange, not in ledger:
   │   - Emit ManualInterventionDetected (POSITION_OPENED_EXTERNALLY)
   │   - Add to current state
   │   - ALERT operator
   │
   ├─► Position in ledger, not on exchange:
   │   - Emit ManualInterventionDetected (POSITION_CLOSED_EXTERNALLY)
   │   - Calculate implied PnL
   │   - Update state
   │   - ALERT operator
   │
   ├─► Position size mismatch:
   │   - Emit ManualInterventionDetected (POSITION_SIZE_CHANGED)
   │   - Update to exchange value
   │   - ALERT operator
   │
   ├─► Order exists on exchange, not in ledger:
   │   - Emit ManualInterventionDetected (ORDER_PLACED_EXTERNALLY)
   │   - Decide: track or cancel
   │
   ├─► Order in ledger, not on exchange:
   │   - Likely filled or cancelled while offline
   │   - Query order status, emit appropriate event
   │
   ▼
5. EMIT ReconciliationCompleted event
   │
   ▼
6. IF critical discrepancies found:
   - Set REQUIRES_MANUAL_REVIEW flag
   - Do not auto-trade until operator acknowledges
   │
   ▼
7. RESUME normal operation (or wait for manual ack)
```

---

## 12. Learning and Model Lifecycle (Offline Only)

### What Is Learned vs. Fixed

| Component | Learned (Tunable) | Fixed (Hard-coded) |
|-----------|-------------------|-------------------|
| EMA periods | Yes (within 5-50 range) | - |
| ATR period | Yes (within 10-20 range) | - |
| ATR multiplier for stops | Yes (within 1.5-3.0) | - |
| Score weights | Yes | - |
| Entry threshold | Yes (within 0.5-0.8) | - |
| Risk per trade | - | 1% max |
| Max leverage | - | 5x |
| Max daily loss | - | 3% |
| Max drawdown | - | 10% |
| Kill switch rules | - | All fixed |

### Learning Pipeline

```
OFFLINE LEARNING FLOW:
=====================

1. DATA COLLECTION (continuous)
   - Store all events in ledger
   - After position close: label trade outcome

   Trade Label Schema:
   {
     "trade_id": "uuid",
     "symbol": "BTCUSDT",
     "direction": "LONG",
     "entry_price": 42000,
     "exit_price": 43500,
     "quantity": 0.001,
     "entry_time": "2024-01-10T10:00:00Z",
     "exit_time": "2024-01-14T14:00:00Z",
     "holding_hours": 100,
     "gross_pnl": 1.50,
     "fees_paid": 0.12,
     "funding_paid": -0.05,
     "net_pnl": 1.33,
     "net_pnl_percent": 3.17,
     "max_adverse_excursion": -0.8,
     "max_favorable_excursion": 1.8,
     "signal_score_at_entry": 0.72,
     "news_flag_at_entry": "LOW"
   }

2. WALK-FORWARD VALIDATION
   - Minimum 6 months historical data
   - Train on 4 months, validate on 2 months
   - Roll forward 1 month, repeat
   - Require positive expectancy in ALL validation windows

3. PARAMETER OPTIMIZATION
   - Grid search or Bayesian optimization
   - Objective: Sharpe ratio (not raw returns)
   - Constraints: max drawdown < 15%, win rate > 35%

4. MODEL VERSIONING
   - Each parameter set = versioned model
   - Store: parameters, training data hash, validation metrics
   - Format: model_v{N}_{date}_{hash}.json

5. PROMOTION CRITERIA
   - Validation Sharpe > 1.0
   - Validation max drawdown < 15%
   - Paper trading for 2 weeks minimum
   - Paper trading metrics within 20% of backtest
   - Manual review and approval

6. DEPLOYMENT
   - A/B testing: new model on 50% of signals (paper)
   - If outperforms for 1 week: promote to live
   - Keep previous model as rollback target

7. ROLLBACK PLAN
   - If live performance degrades >30% vs backtest: auto-rollback
   - Restore previous model version
   - Alert operator
   - Investigate before re-promoting
```

### Prohibited Learning Behaviors

- NO online learning during live trading
- NO self-modifying code paths
- NO automatic promotion without paper trading gate
- NO changing risk parameters via learning
- NO LLM fine-tuning on live trading data without human review

---

## 13. Observability and Alerts

### Metrics

| Category | Metric | Type | Alert Threshold |
|----------|--------|------|-----------------|
| **Connectivity** | ws_connected | gauge | 0 for > 1 min |
| | ws_last_message_age_sec | gauge | > 60 |
| | rest_request_latency_ms | histogram | p99 > 5000 |
| | rest_error_rate | counter | > 5/min |
| **Trading** | open_positions | gauge | > MAX_POSITIONS |
| | daily_pnl_percent | gauge | < -3% |
| | drawdown_percent | gauge | > 8% (warn), > 10% (critical) |
| | consecutive_losses | gauge | >= 3 |
| | win_rate_7d | gauge | < 30% |
| **Execution** | order_fill_latency_ms | histogram | p99 > 10000 |
| | order_reject_count | counter | > 0 |
| | slippage_bps | histogram | p95 > 50 |
| **Risk** | margin_ratio | gauge | > 80% |
| | leverage_used | gauge | > 4x (warn), > 5x (critical) |
| | circuit_breaker_active | gauge | 1 |
| **System** | event_ledger_size | gauge | > 1M events |
| | last_reconciliation_age_hr | gauge | > 24 |
| | memory_usage_mb | gauge | > 500 |

### Alert Definitions

```yaml
alerts:
  - name: WebSocketDisconnected
    condition: ws_connected == 0 for 2m
    severity: critical
    action: "Check network, attempt reconnect"

  - name: HighDrawdown
    condition: drawdown_percent > 8
    severity: warning
    action: "Review positions, consider reducing exposure"

  - name: MaxDrawdownBreached
    condition: drawdown_percent > 10
    severity: critical
    action: "Kill switch activated automatically"

  - name: DailyLossLimit
    condition: daily_pnl_percent < -3
    severity: critical
    action: "Trading halted for 24h"

  - name: MarginRatioHigh
    condition: margin_ratio > 80
    severity: critical
    action: "Reduce position immediately"

  - name: OrderReject
    condition: order_reject_count > 0
    severity: warning
    action: "Check reject reason, may need manual intervention"

  - name: UnexpectedPosition
    condition: manual_intervention_detected == true
    severity: warning
    action: "Review and acknowledge reconciliation"

  - name: StaleMarketData
    condition: ws_last_message_age_sec > 60
    severity: critical
    action: "Data feed issue, halt new entries"
```

### Log Structure

```json
{
  "timestamp": "2024-01-15T10:30:00.000Z",
  "level": "INFO",
  "logger": "execution_engine",
  "message": "Order placed successfully",
  "context": {
    "symbol": "BTCUSDT",
    "side": "BUY",
    "quantity": 0.001,
    "client_order_id": "TREND_BTCUSDT_BUY_1704067200000_a1b2c3",
    "exchange_order_id": "12345678"
  },
  "correlation_id": "signal-uuid-xyz",
  "trace_id": "abc123"
}
```

### Recommended Dashboards

1. **Overview Dashboard**
   - Current positions (table)
   - Daily/weekly/monthly PnL chart
   - Drawdown chart
   - Win rate rolling 7d/30d

2. **Execution Dashboard**
   - Order fill latency histogram
   - Slippage distribution
   - Order reject reasons pie chart
   - Fill rate by order type

3. **Risk Dashboard**
   - Margin ratio time series
   - Leverage used time series
   - Circuit breaker events
   - Consecutive loss streak

4. **System Health Dashboard**
   - WebSocket connection status
   - REST API latency
   - Event ledger growth
   - Memory/CPU usage

---

## 14. Security

### API Key Permissions

| Permission | Required | Rationale |
|------------|----------|-----------|
| Enable Reading | Yes | Account info, positions |
| Enable Futures | Yes | Place/cancel orders |
| Enable Withdrawals | **NO** | Never needed, security risk |
| Enable Internal Transfer | **NO** | Not needed |

### Secrets Handling

```
REQUIREMENTS:
- API keys stored in environment variables or secrets manager
- NEVER in code, config files committed to git, or logs
- Use separate keys for testnet vs mainnet
- Rotate keys every 90 days
- Revoke immediately if suspected compromise

STORAGE OPTIONS (in order of preference):
1. Cloud secrets manager (AWS Secrets Manager, GCP Secret Manager)
2. HashiCorp Vault
3. Encrypted environment files (age, sops)
4. Environment variables (minimum acceptable)

LOGGING:
- Never log API keys, even partially
- Redact in error messages: "API key: ***REDACTED***"
```

### IP Allowlisting

```
RECOMMENDATION:
- Enable IP allowlist on Binance API key settings
- Allow only:
  - Production server IP(s)
  - Monitoring/alerting system IP (if needed)
- For development: use separate testnet key without IP restriction
- Document all allowed IPs with purpose
```

### Audit Requirements

| Event | Log Content | Retention |
|-------|-------------|-----------|
| API key usage | Timestamp, endpoint, IP | 90 days |
| Order placed | Full order details | Indefinite |
| Order cancelled | Order ID, reason | Indefinite |
| Config changed | Old value, new value, who | Indefinite |
| Login/access | Timestamp, IP, success/fail | 90 days |
| Kill switch | Timestamp, trigger reason | Indefinite |

### Principle of Least Privilege

| Component | Access Granted |
|-----------|----------------|
| Market data connector | Read-only API access |
| Execution engine | Futures trading only |
| Learning engine | No API access (offline) |
| Monitoring | Read-only API access |
| Config loader | Local filesystem only |

---

## 15. Testing Plan

### Unit Tests

| Module | Test Cases |
|--------|------------|
| **Position Sizing** | Risk-based calculation, leverage cap, min notional, lot size rounding |
| **Scoring Engine** | Trend score bounds, volatility score bounds, composite calculation |
| **Risk Checklist** | Each individual check, all-pass case, each single-fail case |
| **Symbol Filters** | Min notional, tick size, lot size validation |
| **Event Application** | State changes for each event type |
| **Idempotency** | Duplicate order detection, same signal handling |
| **News Parser** | RSS parsing, deduplication, timestamp handling |
| **LLM Output Parser** | Valid JSON, schema validation, edge cases |

### Integration Tests

| Scenario | Mock Behavior |
|----------|---------------|
| Full entry flow | Mock Binance REST/WS, verify event sequence |
| Full exit flow | Mock fills, verify position closed |
| Partial fill handling | Mock partial fills, verify state updates |
| Order reject handling | Mock reject, verify retry/abort logic |
| Reconnection | Mock disconnect, verify resubscribe |
| Reconciliation | Mock position mismatch, verify detection |
| Rate limiting | Mock 429, verify backoff |
| Kill switch | Mock drawdown trigger, verify flatten |

### Backtesting Approach

```
DATA REQUIREMENTS:
- Minimum 6 months of 4H candles for all universe symbols
- Funding rate history
- Source: Binance historical data or third-party

SIMULATION RULES:
- Entry at candle close price + slippage model (0.05%)
- Exit at candle close price + slippage model
- Include trading fees (0.04% maker, 0.06% taker)
- Include funding payments (every 8h)
- Respect position limits and risk rules
- No lookahead bias (strict)

METRICS TO COMPUTE:
- Total return, CAGR
- Max drawdown, average drawdown
- Sharpe ratio, Sortino ratio
- Win rate, profit factor
- Average win, average loss
- Average holding period
- Number of trades

VALIDATION:
- Walk-forward testing (not single backtest)
- Out-of-sample performance check
- Monte Carlo simulation of trade sequence
```

### Paper Trading Plan

```
PHASE 1: Binance Testnet (2 weeks minimum)
- Full system deployment on testnet
- Execute all signal types
- Verify order flows end-to-end
- Test restart/reconciliation
- Test all circuit breakers manually

PHASE 2: Live Paper (shadow mode, 2 weeks)
- Connect to live market data
- Generate signals but DO NOT execute
- Log what WOULD have been traded
- Compare paper results to live market
- Verify signal quality

PHASE 3: Micro-Live (2 weeks)
- Live trading with minimum size ($5-10 per trade)
- Verify fills, slippage, fees match expectations
- Full monitoring active
- Low risk (<$0.10 per trade)
```

### Chaos Tests

| Test | Procedure | Expected Behavior |
|------|-----------|-------------------|
| Network disconnect | Kill network mid-operation | Reconnect, reconcile, resume |
| Partial fill then disconnect | Simulate partial fill, drop connection | Detect on reconnect, handle remaining |
| Restart mid-entry | Kill process after order placed, before fill | Reconcile order state, avoid duplicate |
| Restart mid-exit | Kill process during exit | Detect position still open or closed, correct |
| Exchange maintenance | Mock exchange offline | Halt gracefully, alert, resume after |
| Invalid API response | Return malformed JSON | Handle error, alert, do not crash |
| Clock skew | Offset system time by 1 minute | Detect via exchange time, adjust |

---

## 16. Deployment and Configuration

### Configuration File Structure

```yaml
# config.yaml

environment: "production"  # testnet | production

binance:
  base_url: "https://fapi.binance.com"  # or testnet URL
  ws_url: "wss://fstream.binance.com"
  recv_window: 5000

strategy:
  name: "trend_following_v1"
  timeframes:
    trend: "1d"
    entry: "4h"
  indicators:
    ema_fast: 8
    ema_slow: 21
    atr_period: 14
  entry:
    style: "pullback"  # pullback | breakout
    score_threshold: 0.65
  exit:
    atr_stop_multiplier: 2.0
    trailing_start_atr: 1.5
    trailing_distance_atr: 1.5
    time_stop_days: 7
    time_stop_min_profit_atr: 0.5

risk:
  risk_per_trade_pct: 1.0
  max_leverage: 5
  default_leverage: 3
  max_daily_loss_pct: 3.0
  max_drawdown_pct: 10.0
  max_consecutive_losses: 3
  cooldown_after_loss_hours: 4
  cooldown_after_max_daily_hours: 24
  max_positions: 1
  max_funding_rate_pct: 0.1

universe:
  min_quote_volume_usd: 50000000
  max_min_notional_usd: 6
  max_funding_rate_pct: 0.1
  size: 5

news:
  enabled: true
  poll_interval_minutes: 15
  max_age_hours: 24
  high_risk_block_hours: 24
  medium_risk_block_hours: 6
  sources:
    - url: "https://www.coindesk.com/arc/outboundfeeds/rss/"
      name: "coindesk"
      enabled: true

llm:
  provider: "openai"  # openai | anthropic | local
  model: "gpt-4-turbo-preview"
  max_tokens: 500
  temperature: 0.1
  rate_limit_per_minute: 10

execution:
  max_slippage_pct: 0.5
  limit_order_buffer_pct: 0.05
  max_spread_pct: 0.3
  retry_attempts: 3
  retry_initial_delay_ms: 1000
  retry_max_delay_ms: 10000
  order_timeout_sec: 30

storage:
  ledger_path: "./data/ledger"
  state_path: "./data/state"
  logs_path: "./logs"

monitoring:
  metrics_port: 9090
  log_level: "INFO"
  alert_webhooks:
    - url: "https://hooks.slack.com/..."
      events: ["critical"]
```

### Environment Variables

```bash
# Required
BINANCE_API_KEY=<your-api-key>
BINANCE_SECRET_KEY=<your-secret-key>

# Optional LLM
OPENAI_API_KEY=<your-openai-key>
# or
ANTHROPIC_API_KEY=<your-anthropic-key>

# Optional overrides
CONFIG_PATH=/path/to/config.yaml
LOG_LEVEL=DEBUG
```

### Startup Sequence

```
1. LOAD configuration
   - Read config.yaml
   - Override with environment variables
   - Validate all required fields
   │
   ▼
2. INITIALIZE logging
   - Configure structured logging
   - Set log level
   │
   ▼
3. INITIALIZE storage
   - Connect to ledger store
   - Load or create state
   │
   ▼
4. REBUILD state from ledger
   - Replay all events
   - Calculate current state
   │
   ▼
5. CONNECT to Binance REST
   - Test connectivity
   - Fetch exchange info
   - Store symbol filters
   │
   ▼
6. RECONCILE with exchange
   - Compare positions/orders
   - Detect discrepancies
   - Emit reconciliation events
   │
   ▼
7. CONNECT to Binance WebSocket
   - Market data streams
   - User data stream
   │
   ▼
8. START monitoring
   - Expose metrics endpoint
   - Initialize alerting
   │
   ▼
9. CHECK circuit breakers
   - If any active: stay halted
   - If manual review needed: wait for ack
   │
   ▼
10. START main loop
    - Process events
    - Generate signals
    - Execute approved trades
```

### Graceful Shutdown

```
ON SIGTERM/SIGINT:
==================

1. SET shutting_down = true
   │
   ▼
2. STOP accepting new signals
   │
   ▼
3. COMPLETE in-flight operations
   - Wait for pending order confirmations (max 30s)
   │
   ▼
4. CANCEL non-protective open orders
   - Keep SL/TP orders active
   │
   ▼
5. FLUSH event ledger
   - Ensure all events persisted
   │
   ▼
6. EMIT SystemStopped event
   │
   ▼
7. CLOSE connections
   - WebSocket
   - REST client
   │
   ▼
8. EXIT with code 0
```

---

## 17. Implementation Roadmap

### Phase 1: Foundation (Backtest-Ready)
- [ ] Event ledger implementation
- [ ] Configuration management
- [ ] Binance REST connector (read-only)
- [ ] Historical data fetcher
- [ ] Feature pipeline (EMA, ATR)
- [ ] Scoring engine
- [ ] Risk engine (calculation only)
- [ ] Backtester
- [ ] Unit tests for all components

### Phase 2: Paper Trading Ready
- [ ] Binance WebSocket connector
- [ ] State manager
- [ ] Reconciliation logic
- [ ] Execution engine (testnet)
- [ ] Monitoring/metrics
- [ ] Alert system
- [ ] Integration tests
- [ ] Deploy to testnet

### Phase 3: News Integration
- [ ] RSS ingestion
- [ ] LLM connector
- [ ] News classification pipeline
- [ ] News risk modifier integration
- [ ] Extended backtests with news

### Phase 4: Live Ready
- [ ] Security hardening
- [ ] Production deployment scripts
- [ ] Runbook documentation
- [ ] Incident response procedures
- [ ] Final chaos testing
- [ ] Micro-live deployment

### Phase 5: Optimization
- [ ] Learning pipeline (offline)
- [ ] Walk-forward validation automation
- [ ] Model versioning system
- [ ] A/B testing framework
- [ ] Performance dashboards

---

## Appendix A: Glossary

| Term | Definition |
|------|------------|
| ATR | Average True Range - volatility indicator |
| EMA | Exponential Moving Average |
| Perp | Perpetual futures contract (no expiry) |
| USDⓈ-M | USD-margined contracts (settled in USDT/USDC) |
| Funding Rate | Periodic payment between longs and shorts |
| Mark Price | Fair price used for liquidation calculations |
| Notional | Position size × price |
| Margin Ratio | Maintenance margin / margin balance |
| Reduce-Only | Order that can only reduce position size |
| GTC | Good-Till-Cancelled order |
| Stop-Market | Market order triggered at stop price |

## Appendix B: References

- Binance Futures API Documentation: https://binance-docs.github.io/apidocs/futures/en/
- Binance Testnet: https://testnet.binancefuture.com
- Event Sourcing Pattern: https://martinfowler.com/eaaDev/EventSourcing.html
- Walk-Forward Optimization: https://www.investopedia.com/terms/w/walk-forward-testing.asp

---

*Specification Version: 1.0*
*Last Updated: 2024-01-15*
*Status: Draft - Pending Review*
