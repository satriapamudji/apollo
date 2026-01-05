# Task 04 — Strategy v2: Factorized Scoring + Regime Filter (Anti-Chop)

## Goal
Upgrade the strategy from “EMA trend + breakout/pullback” to a factorized influence model with an explicit regime filter.

## Why
Crypto trend-following lives or dies by avoiding chop regimes and only deploying risk when there is directional persistence.

## Deliverables
- Refactor scoring into factor modules (trend, momentum, volatility/regime, liquidity/spread, funding, news).
- Add a regime filter that blocks entries in chop (e.g., ADX threshold, choppiness index, volatility contraction/expansion rules).
- Fix `breakout` entry quality: do not use a constant `entry_distance`; compute a real distance/extension vs ATR and reject over-extended breakouts.
- Ensure `logs/thinking.jsonl` includes per-factor scores and a single composite score.

## Acceptance Criteria
- New factors and weights are fully config-driven.
- For a given candle history, the score is deterministic and auditable from logs.

