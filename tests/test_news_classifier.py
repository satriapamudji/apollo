from __future__ import annotations

import pytest

from src.connectors.news import NewsItem
from src.connectors.news_classifier import RuleBasedNewsClassifier


@pytest.mark.asyncio
async def test_rule_based_classifier_detects_hack_and_extracts_assets() -> None:
    item = NewsItem(
        source="test",
        title="Ethereum DeFi protocol hacked; funds drained",
        link="https://example.com",
        published=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        summary="Attackers exploited a vulnerability and drained funds.",
        content="ETH was impacted; Bitcoin markets react.",
    )
    classifier = RuleBasedNewsClassifier()
    result = await classifier.classify(item)

    payload = result.to_payload()
    assert payload["risk_level"] == "HIGH"
    assert payload["matched_rules"]
    assert "hack_exploit" in payload["matched_rules"]
    assert set(payload["symbols_mentioned"]) >= {"ETH", "BTC"}
    assert payload["confidence"] >= 0.8
    assert payload["risk_reason"]


@pytest.mark.asyncio
async def test_rule_based_classifier_defaults_to_low_risk() -> None:
    item = NewsItem(
        source="test",
        title="Market update: crypto prices mixed",
        link="https://example.com",
        published=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        summary="No major events.",
        content="",
    )
    classifier = RuleBasedNewsClassifier()
    result = await classifier.classify(item)

    payload = result.to_payload()
    assert payload["risk_level"] == "LOW"
    assert payload["matched_rules"] == []
    assert payload["risk_reason"] is None
