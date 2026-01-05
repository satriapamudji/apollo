# Task 18 — Remove Paid LLM Dependency for News Risk (Deterministic + Free)

## Goal
Replace the LLM-based news classifier path with a deterministic, free alternative, while keeping the interface extensible.

## Why
Paid LLM calls are not compatible with the current constraint and add non-determinism. For trading, the news module should be a conservative risk gate, not a “smart predictor”.

## Deliverables
- Implement a rule-based classifier:
  - keyword/regex-based buckets (hack/exploit, regulatory, exchange outage, macro shock)
  - conservative mapping to `HIGH/MEDIUM/LOW`
  - optional symbol extraction for major assets (BTC/ETH) + exchange names
- Keep LLM as an optional provider behind a feature flag (default off).
- Emit clear audit fields: `risk_reason`, `matched_rules`, `confidence` (fixed heuristic confidence is fine).

## Acceptance Criteria
- With no API keys, news risk gating still works and is deterministic.
- `NewsClassified` events remain stable and useful for audit/debug.

