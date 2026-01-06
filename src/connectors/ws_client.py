"""Binance WebSocket client with auto-reconnect."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Awaitable, Callable, TYPE_CHECKING

import websockets
import structlog

from src.config.settings import Settings

if TYPE_CHECKING:
    from src.monitoring.metrics import Metrics


MessageHandler = Callable[[dict], Awaitable[None] | None]


class BinanceWebSocketClient:
    """WebSocket client for market and user streams."""

    def __init__(self, settings: Settings, metrics: "Metrics" | None = None) -> None:
        self.settings = settings
        self.base_url = settings.binance_ws_url
        self._stop = asyncio.Event()
        self.log = structlog.get_logger(__name__)
        self._metrics = metrics
        self._last_message_time: float | None = None

    async def run(self, streams: list[str], handler: MessageHandler) -> None:
        backoff = 1
        while not self._stop.is_set():
            url = self._build_url(streams)
            try:
                self.log.info("ws_connecting", stream_count=len(streams))
                async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
                    self.log.info("ws_connected", stream_count=len(streams))
                    if self._metrics is not None:
                        self._metrics.ws_connected.set(1)
                    backoff = 1
                    while not self._stop.is_set():
                        recv_task = asyncio.create_task(ws.recv())
                        stop_task = asyncio.create_task(self._stop.wait())
                        done, _ = await asyncio.wait(
                            {recv_task, stop_task},
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        if stop_task in done:
                            recv_task.cancel()
                            break
                        stop_task.cancel()
                        try:
                            message = recv_task.result()
                        except asyncio.CancelledError:
                            break
                        payload = json.loads(message)
                        self._last_message_time = time.time()
                        if self._metrics is not None:
                            self._metrics.ws_last_message_age_sec.set(0)
                        result = handler(payload)
                        if asyncio.iscoroutine(result):
                            await result
            except Exception as exc:
                self.log.warning("ws_disconnected", error=str(exc), backoff_sec=backoff)
                if self._metrics is not None:
                    self._metrics.ws_connected.set(0)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)
            finally:
                if self._metrics is not None and self._stop.is_set():
                    self._metrics.ws_connected.set(0)

    def stop(self) -> None:
        self._stop.set()

    def last_message_age_sec(self) -> float | None:
        if self._last_message_time is None:
            return None
        return max(0.0, time.time() - self._last_message_time)

    def _build_url(self, streams: list[str]) -> str:
        if len(streams) == 1:
            return f"{self.base_url}/ws/{streams[0]}"
        joined = "/".join(streams)
        return f"{self.base_url}/stream?streams={joined}"
