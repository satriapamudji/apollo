# Task 18 - Deterministic News Classifier

## Summary

Replaced the LLM-based news classifier with a deterministic, rule-based alternative as the default provider. LLM classification remains available as an optional feature behind a configuration flag.

## What Changed

### Before
- News classification relied on LLM (OpenAI/Anthropic) API calls
- Required API keys to function
- Non-deterministic outputs (LLM responses vary)
- Added cost per classification

### After
- Default classifier is `RuleBasedNewsClassifier` - free and deterministic
- LLM classifier available via `news.classifier_provider: "llm"` config option
- Consistent, reproducible risk assessments
- No external API dependencies for core functionality

## Implementation Details

### Rule-Based Classifier (`src/connectors/news_classifier.py`)

The `RuleBasedNewsClassifier` uses keyword/regex pattern matching with four risk buckets:

| Bucket | Risk Level | Confidence | Keywords |
|--------|------------|------------|----------|
| `hack_exploit` | HIGH | 0.9 | hack, exploit, breach, security incident, private key, leak, drain, stolen, compromised, rug pull |
| `exchange_outage` | HIGH | 0.8 | outage, downtime, halt, suspend deposits/withdrawals, trading halted, maintenance |
| `regulatory` | MEDIUM | 0.7 | SEC, CFTC, DOJ, lawsuit, indictment, sanction, ban, regulation, enforcement, fine, settlement, investigation |
| `macro_shock` | MEDIUM | 0.65 | rate hike/cut, Fed, FOMC, CPI, inflation, recession, bank failure, credit event, war, geopolitical, oil shock, flash crash |

### Asset & Exchange Extraction

Automatically extracts mentions of:
- **Assets**: BTC, ETH (via regex patterns)
- **Exchanges**: BINANCE, COINBASE, KRAKEN, OKX, BYBIT, BITGET

### Audit Fields

Every `NewsClassification` includes:
- `risk_level`: HIGH, MEDIUM, or LOW
- `risk_reason`: Human-readable explanation (e.g., `rule_match:hack_exploit (assets=ETH; exchanges=BINANCE)`)
- `matched_rules`: List of rule IDs that triggered
- `confidence`: Fixed heuristic value based on the highest-severity matched rule

### Configuration

In `config.yaml`:
```yaml
news:
  enabled: true
  classifier_provider: "rules"  # Default: deterministic. Set to "llm" for LLM-based classification
  poll_interval_minutes: 15
  max_age_hours: 24
  high_risk_block_hours: 24
  medium_risk_block_hours: 6
```

### Integration in `main.py`

```python
if settings.news.classifier_provider == "llm":
    news_classifier = LLMNewsClassifierAdapter(llm)
    news_classifier_source = "llm_classifier"
else:
    news_classifier = RuleBasedNewsClassifier()
    news_classifier_source = "rule_classifier"
```

## Reasoning

1. **Determinism**: Trading systems benefit from reproducible behavior. LLM outputs can vary even with low temperature settings.

2. **Cost**: Each LLM call costs money. News classification runs every 15 minutes across multiple sources.

3. **Availability**: The system should function without external API dependencies. Missing API keys shouldn't break news-based risk gating.

4. **Conservative by Design**: The rule-based approach is intentionally conservative - when in doubt, it treats material news as risk. This aligns with the bot's risk-first philosophy.

5. **Extensibility**: The `NewsClassifier` protocol allows easy addition of new classifiers. LLM remains available for users who want more nuanced classification.

## Files Modified

- `src/connectors/news_classifier.py` - Added `RuleBasedNewsClassifier` and `LLMNewsClassifierAdapter`
- `src/config/settings.py` - Added `classifier_provider` to `NewsConfig`
- `src/main.py` - Classifier selection logic based on config
- `src/connectors/__init__.py` - Exported new classes
- `tests/test_news_classifier.py` - Tests for rule-based classifier

## Testing

```bash
pytest tests/test_news_classifier.py -v
```

Tests verify:
- HIGH risk detection for hack/exploit news with asset extraction
- LOW risk default for benign news
- Correct audit field population
