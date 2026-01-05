"""RSS news ingestion utilities."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import calendar
from typing import Any

import feedparser

from src.config.settings import NewsConfig, NewsSourceConfig


@dataclass(frozen=True)
class NewsItem:
    source: str
    title: str
    link: str
    published: datetime
    summary: str
    content: str

    def dedupe_key(self) -> str:
        raw = f"{self.source}:{self.title}:{self.published.isoformat()}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class NewsIngester:
    """Poll RSS feeds and return deduplicated items."""

    def __init__(self, config: NewsConfig) -> None:
        self.config = config
        self._seen: set[str] = set()

    def fetch(self) -> list[NewsItem]:
        items: list[NewsItem] = []
        for source in self.config.sources:
            if not source.enabled:
                continue
            items.extend(self._fetch_source(source))
        return items

    def _fetch_source(self, source: NewsSourceConfig) -> list[NewsItem]:
        now = datetime.now(timezone.utc)
        results: list[NewsItem] = []
        feed = feedparser.parse(source.url)
        for entry in feed.entries:
            published = self._parse_published(entry) or now
            if now - published > timedelta(hours=self.config.max_age_hours):
                continue
            title = entry.get("title", "").strip()
            link = entry.get("link", "")
            summary = entry.get("summary", "")
            content = ""
            if "content" in entry and entry["content"]:
                content = entry["content"][0].get("value", "")
            item = NewsItem(
                source=source.name,
                title=title,
                link=link,
                published=published,
                summary=summary,
                content=content,
            )
            key = item.dedupe_key()
            if key in self._seen:
                continue
            self._seen.add(key)
            results.append(item)
        return results

    @staticmethod
    def _parse_published(entry: dict[str, Any]) -> datetime | None:
        if "published_parsed" in entry and entry["published_parsed"]:
            ts = calendar.timegm(entry["published_parsed"])
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        if "published" in entry:
            try:
                return datetime.fromisoformat(entry["published"].replace("Z", "+00:00"))
            except ValueError:
                return None
        return None
