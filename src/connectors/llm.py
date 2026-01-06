"""LLM connector for news classification."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from typing import Any

import structlog

from src.config.settings import LLMConfig
from src.connectors.news import NewsItem


@dataclass(frozen=True)
class LLMResult:
    event_type: str
    symbols_mentioned: list[str]
    sentiment: str
    risk_level: str
    risk_reason: str | None
    confidence: float
    summary: str

    def to_payload(self) -> dict[str, Any]:
        # Keep payload stable across classifier providers.
        return {
            "event_type": self.event_type,
            "symbols_mentioned": self.symbols_mentioned,
            "exchanges_mentioned": [],
            "sentiment": self.sentiment,
            "risk_level": self.risk_level,
            "risk_reason": self.risk_reason,
            "matched_rules": [],
            "confidence": self.confidence,
            "summary": self.summary,
        }


class LLMClassifier:
    """Classify news using configured LLM provider."""

    def __init__(self, config: LLMConfig, api_key: str) -> None:
        self.config = config
        self.api_key = api_key
        self._cache: dict[str, LLMResult] = {}
        self._call_times: list[float] = []
        self._log = structlog.get_logger(__name__)

    async def classify(self, item: NewsItem) -> LLMResult:
        cache_key = self._cache_key(item)
        if cache_key in self._cache:
            return self._cache[cache_key]
        await self._rate_limit()
        result = await self._classify_with_retries(item)
        self._cache[cache_key] = result
        return result

    async def _rate_limit(self) -> None:
        if self.config.rate_limit_per_minute <= 0:
            return
        loop = asyncio.get_running_loop()
        now = loop.time()
        window = 60
        self._call_times = [t for t in self._call_times if now - t < window]
        if len(self._call_times) >= self.config.rate_limit_per_minute:
            sleep_for = window - (now - self._call_times[0])
            await asyncio.sleep(max(0, sleep_for))
        self._call_times.append(loop.time())

    def _cache_key(self, item: NewsItem) -> str:
        raw = f"{item.source}:{item.title}:{item.published.isoformat()}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _prompt(self, item: NewsItem) -> str:
        content = item.content or item.summary
        return (
            "You are a crypto news classifier. Analyze the following news item and output ONLY valid JSON.\n\n"
            f"NEWS:\nTitle: {item.title}\nSource: {item.source}\n"
            f"Published: {item.published.isoformat()}\nContent: {content}\n\n"
            "OUTPUT FORMAT:\n"
            "{\n"
            '  "event_type": "regulatory|exchange|technical|macro|project|other",\n'
            '  "symbols_mentioned": ["BTC", "ETH"],\n'
            '  "sentiment": "bullish|bearish|neutral",\n'
            '  "risk_level": "HIGH|MEDIUM|LOW",\n'
            '  "risk_reason": "brief explanation if HIGH or MEDIUM",\n'
            '  "confidence": 0.0,\n'
            '  "summary": "one sentence summary"\n'
            "}\n"
        )

    def _local_stub(self, item: NewsItem) -> LLMResult:
        return LLMResult(
            event_type="other",
            symbols_mentioned=[],
            sentiment="neutral",
            risk_level="LOW",
            risk_reason=None,
            confidence=0.0,
            summary=item.title[:200],
        )

    async def _classify_openai(self, item: NewsItem) -> LLMResult:
        if not self.api_key:
            return self._local_stub(item)
        try:
            from openai import AsyncOpenAI
        except ImportError:
            return self._local_stub(item)
        client = AsyncOpenAI(api_key=self.api_key)
        response = await client.chat.completions.create(
            model=self.config.model,
            messages=[{"role": "user", "content": self._prompt(item)}],
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
        )
        content = response.choices[0].message.content or "{}"
        return self._parse_response(content)

    async def _classify_anthropic(self, item: NewsItem) -> LLMResult:
        if not self.api_key:
            return self._local_stub(item)
        try:
            from anthropic import AsyncAnthropic
        except ImportError:
            return self._local_stub(item)
        client = AsyncAnthropic(api_key=self.api_key)
        response = await client.messages.create(
            model=self.config.model,
            max_tokens=self.config.max_tokens,
            temperature=self.config.temperature,
            messages=[{"role": "user", "content": self._prompt(item)}],
        )
        content = response.content[0].text if response.content else "{}"
        return self._parse_response(content)

    async def _classify_with_retries(self, item: NewsItem) -> LLMResult:
        if self.config.provider == "local":
            return self._local_stub(item)
        retries = max(0, self.config.retry_attempts)
        backoff = self.config.retry_backoff_sec
        for attempt in range(retries + 1):
            try:
                if self.config.provider == "openai":
                    return await asyncio.wait_for(
                        self._classify_openai(item),
                        timeout=self.config.request_timeout_sec,
                    )
                if self.config.provider == "anthropic":
                    return await asyncio.wait_for(
                        self._classify_anthropic(item),
                        timeout=self.config.request_timeout_sec,
                    )
                return self._local_stub(item)
            except Exception as exc:
                self._log.warning(
                    "llm_classify_failed",
                    provider=self.config.provider,
                    attempt=attempt + 1,
                    error=str(exc),
                )
                if attempt < retries:
                    await asyncio.sleep(backoff * (2**attempt))
        return self._local_stub(item)

    def _parse_response(self, raw: str) -> LLMResult:
        import json

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return LLMResult(
                event_type="other",
                symbols_mentioned=[],
                sentiment="neutral",
                risk_level="LOW",
                risk_reason=None,
                confidence=0.0,
                summary=raw[:200],
            )
        event_type = str(data.get("event_type", "other")).lower()
        if event_type not in {"regulatory", "exchange", "technical", "macro", "project", "other"}:
            event_type = "other"
        sentiment = str(data.get("sentiment", "neutral")).lower()
        if sentiment not in {"bullish", "bearish", "neutral"}:
            sentiment = "neutral"
        risk_level = str(data.get("risk_level", "LOW")).upper()
        if risk_level not in {"HIGH", "MEDIUM", "LOW"}:
            risk_level = "LOW"
        symbols = data.get("symbols_mentioned", [])
        if not isinstance(symbols, list):
            symbols = []
        symbols = [str(s) for s in symbols if s]
        return LLMResult(
            event_type=event_type,
            symbols_mentioned=symbols,
            sentiment=sentiment,
            risk_level=risk_level,
            risk_reason=data.get("risk_reason"),
            confidence=self._safe_float(data.get("confidence", 0.0)),
            summary=str(data.get("summary", ""))[:200],
        )

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        if value is None:
            return default
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
