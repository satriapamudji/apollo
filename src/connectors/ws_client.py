"""Binance WebSocket client with auto-reconnect."""

from __future__ import annotations

import asyncio
import json
from typing import Awaitable, Callable

import websockets
import structlog

from src.config.settings import Settings


MessageHandler = Callable[[dict], Awaitable[None] | None]


class BinanceWebSocketClient:
    """WebSocket client for market and user streams."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base_url = settings.binance_ws_url
        self._stop = asyncio.Event()
        self.log = structlog.get_logger(__name__)

    async def run(self, streams: list[str], handler: MessageHandler) -> None:
        backoff = 1
        while not self._stop.is_set():
            url = self._build_url(streams)
            try:
                self.log.info("ws_connecting", stream_count=len(streams))
                async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
                    self.log.info("ws_connected", stream_count=len(streams))
                    backoff = 1
                    async for message in ws:
                        payload = json.loads(message)
                        result = handler(payload)
                        if asyncio.iscoroutine(result):
                            await result
            except Exception as exc:
                self.log.warning("ws_disconnected", error=str(exc), backoff_sec=backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

    def stop(self) -> None:
        self._stop.set()

    def _build_url(self, streams: list[str]) -> str:
        if len(streams) == 1:
            return f"{self.base_url}/ws/{streams[0]}"
        joined = "/".join(streams)
        return f"{self.base_url}/stream?streams={joined}"
