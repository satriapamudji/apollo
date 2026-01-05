"""Async Binance Futures REST client with rate limiting."""

from __future__ import annotations

import asyncio
import hmac
import time
from hashlib import sha256
from typing import Any
from urllib.parse import urlencode

import httpx
import structlog

from src.config.settings import Settings


class RateLimitTracker:
    """Track request weight usage per minute."""

    def __init__(self, max_weight_per_minute: int = 1200) -> None:
        self.max_weight = max_weight_per_minute
        self.used_weight = 0
        self.reset_at = time.time() + 60

    async def consume(self, weight: int) -> None:
        now = time.time()
        if now >= self.reset_at:
            self.used_weight = 0
            self.reset_at = now + 60
        projected = self.used_weight + weight
        if projected > self.max_weight * 0.8:
            await asyncio.sleep(max(0, self.reset_at - now))
            self.used_weight = 0
            self.reset_at = time.time() + 60
        self.used_weight += weight

    def update_limit(self, max_weight: int) -> None:
        if max_weight > 0:
            self.max_weight = max_weight


class BinanceRestClient:
    """Binance USD-M Futures REST client."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base_url = settings.binance_base_url
        self.api_key = settings.active_binance_api_key
        self.api_secret = settings.active_binance_secret_key
        self.recv_window = settings.binance.recv_window
        self.http = httpx.AsyncClient(base_url=self.base_url, timeout=10.0)
        self.rate_limiter = RateLimitTracker()
        self.log = structlog.get_logger(__name__)

    async def close(self) -> None:
        await self.http.aclose()

    async def get_exchange_info(self) -> dict[str, Any]:
        data = await self._request("GET", "/fapi/v1/exchangeInfo", weight=10)
        self._update_rate_limits(data.get("rateLimits", []))
        return data

    async def get_24h_tickers(self) -> list[dict[str, Any]]:
        return await self._request("GET", "/fapi/v1/ticker/24hr", weight=40)

    async def get_klines(
        self,
        symbol: str,
        interval: str,
        limit: int = 500,
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> list[list[Any]]:
        params: dict[str, Any] = {"symbol": symbol, "interval": interval, "limit": limit}
        if start_time:
            params["startTime"] = start_time
        if end_time:
            params["endTime"] = end_time
        return await self._request("GET", "/fapi/v1/klines", params=params, weight=1)

    async def get_mark_price(self, symbol: str | None = None) -> dict[str, Any]:
        params = {"symbol": symbol} if symbol else None
        return await self._request("GET", "/fapi/v1/premiumIndex", params=params, weight=1)

    async def get_funding_rate(self, symbol: str) -> float:
        data = await self.get_mark_price(symbol)
        return float(data.get("lastFundingRate", 0.0))

    async def get_account_info(self) -> dict[str, Any]:
        return await self._request("GET", "/fapi/v2/account", signed=True, weight=5)

    async def get_position_risk(self) -> list[dict[str, Any]]:
        return await self._request("GET", "/fapi/v2/positionRisk", signed=True, weight=5)

    async def get_open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        params = {"symbol": symbol} if symbol else None
        return await self._request("GET", "/fapi/v1/openOrders", params=params, signed=True, weight=1)

    async def get_order(
        self,
        symbol: str,
        client_order_id: str | None = None,
        order_id: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"symbol": symbol}
        if client_order_id:
            params["origClientOrderId"] = client_order_id
        if order_id is not None:
            params["orderId"] = order_id
        if not client_order_id and order_id is None:
            raise ValueError("client_order_id or order_id is required")
        return await self._request("GET", "/fapi/v1/order", params=params, signed=True, weight=1)

    async def place_order(self, params: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/fapi/v1/order", params=params, signed=True, weight=1)

    async def cancel_order(self, symbol: str, client_order_id: str) -> dict[str, Any]:
        params = {"symbol": symbol, "origClientOrderId": client_order_id}
        return await self._request("DELETE", "/fapi/v1/order", params=params, signed=True, weight=1)

    async def set_leverage(self, symbol: str, leverage: int) -> dict[str, Any]:
        params = {"symbol": symbol, "leverage": leverage}
        return await self._request("POST", "/fapi/v1/leverage", params=params, signed=True, weight=1)

    async def set_margin_type(self, symbol: str, margin_type: str) -> dict[str, Any]:
        params = {"symbol": symbol, "marginType": margin_type}
        return await self._request("POST", "/fapi/v1/marginType", params=params, signed=True, weight=1)

    async def set_position_mode(self, dual_side_position: bool) -> dict[str, Any]:
        params = {"dualSidePosition": "true" if dual_side_position else "false"}
        return await self._request(
            "POST", "/fapi/v1/positionSide/dual", params=params, signed=True, weight=1
        )

    async def start_listen_key(self) -> str:
        # Futures user data stream endpoints require an API key but do not require a signature.
        data = await self._request("POST", "/fapi/v1/listenKey", signed=False, weight=1)
        return data.get("listenKey", "")

    async def keepalive_listen_key(self, listen_key: str) -> None:
        await self._request(
            "PUT",
            "/fapi/v1/listenKey",
            params={"listenKey": listen_key},
            signed=False,
            weight=1,
        )

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        signed: bool = False,
        weight: int = 1,
    ) -> Any:
        await self.rate_limiter.consume(weight)
        params = params.copy() if params else {}
        headers = {}
        if signed:
            params["timestamp"] = int(time.time() * 1000)
            params["recvWindow"] = self.recv_window
            params["signature"] = self._sign_params(params)
            headers["X-MBX-APIKEY"] = self.api_key
        elif self.api_key:
            headers["X-MBX-APIKEY"] = self.api_key

        log_http = self.settings.monitoring.log_http
        log_http_responses = self.settings.monitoring.log_http_responses
        max_body_chars = self.settings.monitoring.log_http_max_body_chars
        safe_params = self._sanitize_params(params)

        for attempt in range(3):
            start = time.perf_counter()
            try:
                if log_http:
                    self.log.info(
                        "rest_request",
                        method=method,
                        path=path,
                        params=safe_params,
                        signed=signed,
                        weight=weight,
                        attempt=attempt + 1,
                    )
                response = await self.http.request(method, path, params=params, headers=headers)
                latency_ms = (time.perf_counter() - start) * 1000
                if response.status_code == 429:
                    if log_http:
                        self.log.warning(
                            "rest_rate_limited",
                            method=method,
                            path=path,
                            status_code=response.status_code,
                            latency_ms=round(latency_ms, 2),
                        )
                    await asyncio.sleep(2**attempt)
                    continue
                response.raise_for_status()
                data = response.json()
                if log_http:
                    payload: dict[str, Any] = {
                        "method": method,
                        "path": path,
                        "status_code": response.status_code,
                        "latency_ms": round(latency_ms, 2),
                    }
                    if log_http_responses and max_body_chars > 0:
                        payload["response_preview"] = self._preview_json(data, max_body_chars)
                    else:
                        payload["response_type"] = type(data).__name__
                    self.log.info("rest_response", **payload)
                return data
            except httpx.HTTPStatusError as exc:
                latency_ms = (time.perf_counter() - start) * 1000
                if exc.response.status_code in {429, 418, 500, 502, 503}:
                    if log_http:
                        self.log.warning(
                            "rest_http_error_retrying",
                            method=method,
                            path=path,
                            status_code=exc.response.status_code,
                            latency_ms=round(latency_ms, 2),
                            error=self._truncate(exc.response.text, max_body_chars),
                        )
                    await asyncio.sleep(2**attempt)
                    continue
                if log_http:
                    self.log.error(
                        "rest_http_error",
                        method=method,
                        path=path,
                        status_code=exc.response.status_code,
                        latency_ms=round(latency_ms, 2),
                        error=self._truncate(exc.response.text, max_body_chars),
                    )
                raise
            except httpx.RequestError as exc:
                latency_ms = (time.perf_counter() - start) * 1000
                if log_http:
                    self.log.warning(
                        "rest_request_error_retrying",
                        method=method,
                        path=path,
                        latency_ms=round(latency_ms, 2),
                        error=str(exc),
                    )
                await asyncio.sleep(2**attempt)
                continue
        response = await self.http.request(method, path, params=params, headers=headers)
        response.raise_for_status()
        data = response.json()
        if log_http:
            self.log.info(
                "rest_response",
                method=method,
                path=path,
                status_code=response.status_code,
            )
        return data

    @staticmethod
    def _sanitize_params(params: dict[str, Any]) -> dict[str, Any]:
        redacted: dict[str, Any] = {}
        for key, value in params.items():
            lowered = key.lower()
            if lowered in {"signature"}:
                redacted[key] = "<redacted>"
                continue
            if lowered in {"timestamp", "recvwindow"}:
                continue
            redacted[key] = value
        return redacted

    @staticmethod
    def _preview_json(data: Any, max_chars: int) -> str:
        import json

        try:
            raw = json.dumps(data, ensure_ascii=True, default=str)
        except TypeError:
            raw = str(data)
        return BinanceRestClient._truncate(raw, max_chars)

    @staticmethod
    def _truncate(text: str, max_chars: int) -> str:
        if max_chars <= 0 or len(text) <= max_chars:
            return text
        return text[: max_chars - 3] + "..."

    def _sign_params(self, params: dict[str, Any]) -> str:
        query = urlencode(params, doseq=True)
        return hmac.new(self.api_secret.encode("utf-8"), query.encode("utf-8"), sha256).hexdigest()

    def _update_rate_limits(self, limits: list[dict[str, Any]]) -> None:
        for limit in limits:
            if limit.get("rateLimitType") == "REQUEST_WEIGHT":
                max_weight = int(limit.get("limit", 1200))
                self.rate_limiter.update_limit(max_weight)
