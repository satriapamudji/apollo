"""Deterministic news classification.

The bot uses news classification as a conservative risk gate. This module provides
an extensible classifier interface with a default, deterministic rule-based
implementation.

LLM-based classification is supported via an adapter, but should be considered
optional and is disabled by default.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from src.connectors.news import NewsItem


NewsRiskLevel = Literal["HIGH", "MEDIUM", "LOW"]


@dataclass(frozen=True)
class NewsClassification:
    """Result of classifying a news item for risk gating."""

    event_type: str
    symbols_mentioned: list[str]
    exchanges_mentioned: list[str]
    sentiment: str
    risk_level: NewsRiskLevel
    risk_reason: str | None
    matched_rules: list[str]
    confidence: float
    summary: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "symbols_mentioned": self.symbols_mentioned,
            "exchanges_mentioned": self.exchanges_mentioned,
            "sentiment": self.sentiment,
            "risk_level": self.risk_level,
            "risk_reason": self.risk_reason,
            "matched_rules": self.matched_rules,
            "confidence": self.confidence,
            "summary": self.summary,
        }


class NewsClassifier(Protocol):
    async def classify(self, item: NewsItem) -> NewsClassification:  # pragma: no cover
        ...


class RuleBasedNewsClassifier:
    """Free + deterministic keyword/regex based classifier.

    The intent is to be conservative: when in doubt, treat material news as risk.
    """

    _ASSET_PATTERNS: dict[str, re.Pattern[str]] = {
        "BTC": re.compile(r"\b(BTC|BITCOIN)\b", re.IGNORECASE),
        "ETH": re.compile(r"\b(ETH|ETHEREUM)\b", re.IGNORECASE),
    }

    _EXCHANGE_PATTERNS: dict[str, re.Pattern[str]] = {
        "BINANCE": re.compile(r"\bBINANCE\b", re.IGNORECASE),
        "COINBASE": re.compile(r"\bCOINBASE\b", re.IGNORECASE),
        "KRAKEN": re.compile(r"\bKRAKEN\b", re.IGNORECASE),
        "OKX": re.compile(r"\bOKX\b", re.IGNORECASE),
        "BYBIT": re.compile(r"\bBYBIT\b", re.IGNORECASE),
        "BITGET": re.compile(r"\bBITGET\b", re.IGNORECASE),
    }

    # Buckets
    _RULES: list[tuple[str, str, re.Pattern[str], NewsRiskLevel, float]] = [
        (
            "hack_exploit",
            "technical",
            re.compile(
                r"\b(hack(ed)?|exploit(ed)?|breach|security incident|private key|leak|drain(ed)?|stolen|compromised|rug pull)\b",
                re.IGNORECASE,
            ),
            "HIGH",
            0.9,
        ),
        (
            "exchange_outage",
            "exchange",
            re.compile(
                r"\b(outage|downtime|halt(ed)?|suspend(ed)? (deposits|withdrawals)|withdrawals? paused|trading halted|maintenance)\b",
                re.IGNORECASE,
            ),
            "HIGH",
            0.8,
        ),
        (
            "regulatory",
            "regulatory",
            re.compile(
                r"\b(sec|cftc|doj|lawsuit|indict(ment)?|sanction(s)?|ban(ned)?|regulat(ion|ory)|enforcement|fine(d)?|settlement|investigation)\b",
                re.IGNORECASE,
            ),
            "MEDIUM",
            0.7,
        ),
        (
            "macro_shock",
            "macro",
            re.compile(
                r"\b(rate hike|rate cut|fed\b|fomc|cpi\b|inflation|recession|bank failure|credit event|war|geopolitic(al)?|oil shock|flash crash)\b",
                re.IGNORECASE,
            ),
            "MEDIUM",
            0.65,
        ),
    ]

    def classify_sync(self, item: NewsItem) -> NewsClassification:
        text = self._text(item)

        symbols = self._extract_symbols(text)
        exchanges = self._extract_exchanges(text)

        matched_rules: list[str] = []
        risk_level: NewsRiskLevel = "LOW"
        confidence = 0.2
        event_type = "other"
        reasons: list[str] = []

        for rule_id, bucket_type, pattern, rule_level, rule_conf in self._RULES:
            if pattern.search(text):
                matched_rules.append(rule_id)
                reasons.append(rule_id)
                # Conservative aggregation: take max severity.
                if self._severity(rule_level) > self._severity(risk_level):
                    risk_level = rule_level
                    confidence = rule_conf
                    event_type = bucket_type
                elif self._severity(rule_level) == self._severity(risk_level):
                    confidence = max(confidence, rule_conf)

        risk_reason: str | None
        if risk_level == "LOW":
            risk_reason = None
        else:
            context = []
            if symbols:
                context.append(f"assets={','.join(symbols)}")
            if exchanges:
                context.append(f"exchanges={','.join(exchanges)}")
            ctx = f" ({'; '.join(context)})" if context else ""
            risk_reason = f"rule_match:{'/'.join(matched_rules)}{ctx}" if matched_rules else "rule_match:unknown"

        summary = (item.title or "").strip()[:200]
        if not summary:
            summary = (item.summary or item.content or "")[:200]

        return NewsClassification(
            event_type=event_type,
            symbols_mentioned=symbols,
            exchanges_mentioned=exchanges,
            sentiment="neutral",
            risk_level=risk_level,
            risk_reason=risk_reason,
            matched_rules=matched_rules,
            confidence=confidence,
            summary=summary,
        )

    async def classify(self, item: NewsItem) -> NewsClassification:
        # Keep an async surface for compatibility with LLM-based classifiers.
        return self.classify_sync(item)

    @staticmethod
    def _text(item: NewsItem) -> str:
        parts = [item.title, item.summary, item.content]
        return "\n".join([p for p in parts if p])

    def _extract_symbols(self, text: str) -> list[str]:
        symbols: list[str] = []
        for sym, pat in self._ASSET_PATTERNS.items():
            if pat.search(text):
                symbols.append(sym)
        return symbols

    def _extract_exchanges(self, text: str) -> list[str]:
        exchanges: list[str] = []
        for name, pat in self._EXCHANGE_PATTERNS.items():
            if pat.search(text):
                exchanges.append(name)
        return exchanges

    @staticmethod
    def _severity(level: NewsRiskLevel) -> int:
        return {"LOW": 0, "MEDIUM": 1, "HIGH": 2}[level]


class LLMNewsClassifierAdapter:
    """Adapter around the existing `LLMClassifier`.

    This keeps the rest of the system consuming a stable `NewsClassification`
    interface.
    """

    def __init__(self, llm_classifier) -> None:
        self._llm = llm_classifier

    async def classify(self, item: NewsItem) -> NewsClassification:
        result = await self._llm.classify(item)
        payload = result.to_payload()
        return NewsClassification(
            event_type=str(payload.get("event_type", "other")),
            symbols_mentioned=list(payload.get("symbols_mentioned", []) or []),
            exchanges_mentioned=list(payload.get("exchanges_mentioned", []) or []),
            sentiment=str(payload.get("sentiment", "neutral")),
            risk_level=str(payload.get("risk_level", "LOW")),  # type: ignore[assignment]
            risk_reason=payload.get("risk_reason"),
            matched_rules=list(payload.get("matched_rules", []) or []),
            confidence=float(payload.get("confidence", 0.0) or 0.0),
            summary=str(payload.get("summary", ""))[:200],
        )
