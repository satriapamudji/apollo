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
        self._server_reported_weight: int | None = None

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

    def update_server_reported_weight(self, weight: int) -> None:
        """Update with actual weight reported by Binance."""
        self._server_reported_weight = weight

    @property
    def current_weight(self) -> int:
        """Get current used weight (prefer server-reported if available)."""
        return (
            self._server_reported_weight
            if self._server_reported_weight is not None
            else self.used_weight
        )


class BinanceRestClient:
    """Binance USD-M Futures REST client."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base_url = settings.binance_base_url
        self.market_data_base_url = settings.binance_market_data_base_url
        self.api_key = settings.active_binance_api_key
        self.api_secret = settings.active_binance_secret_key
        self.recv_window = settings.binance.recv_window
        self.http = httpx.AsyncClient(base_url=self.base_url, timeout=10.0)
        # Separate HTTP client for market data (may use different URL)
        self.market_http = httpx.AsyncClient(base_url=self.market_data_base_url, timeout=10.0)
        self.rate_limiter = RateLimitTracker()
        # Server time sync offset in milliseconds
        self._server_time_offset: int = 0
        # Last rate-limit header values
        self._last_order_count_10s: int | None = None
        self._last_order_count_1m: int | None = None
        self.log = structlog.get_logger(__name__)

    async def close(self) -> None:
        await self.http.aclose()
        await self.market_http.aclose()

    async def get_server_time(self) -> int:
        """Get Binance server time in milliseconds.

        Returns:
            Server timestamp in milliseconds since epoch.
        """
        data = await self._market_request("GET", "/fapi/v1/time", weight=0)
        return data["serverTime"]

    async def sync_server_time(self) -> None:
        """Sync local time with Binance server time.

        Calculates and stores the offset between local time and server time.
        This offset is used for signed requests to avoid timestamp errors.
        """
        server_time = await self.get_server_time()
        local_time = int(time.time() * 1000)
        self._server_time_offset = server_time - local_time
        self.log.info(
            "server_time_synced",
            server_time=server_time,
            local_time=local_time,
            offset_ms=self._server_time_offset,
        )

    def get_time_offset(self) -> int:
        """Get the current server time offset in milliseconds."""
        return self._server_time_offset

    def _update_rate_limit_headers(self, response: httpx.Response) -> None:
        """Extract and store rate-limit headers from response."""
        # Update server-reported weight
        if used_weight := response.headers.get("x-mbx-used-weight-1m"):
            try:
                self.rate_limiter.update_server_reported_weight(int(used_weight))
            except ValueError:
                pass

        # Update order count headers
        if order_count_10s := response.headers.get("x-mbx-order-count-10s"):
            try:
                self._last_order_count_10s = int(order_count_10s)
            except ValueError:
                pass

        if order_count_1m := response.headers.get("x-mbx-order-count-1m"):
            try:
                self._last_order_count_1m = int(order_count_1m)
            except ValueError:
                pass

    async def get_exchange_info(self) -> dict[str, Any]:
        data = await self._market_request("GET", "/fapi/v1/exchangeInfo", weight=10)
        self._update_rate_limits(data.get("rateLimits", []))
        return data

    async def get_24h_tickers(self) -> list[dict[str, Any]]:
        return await self._market_request("GET", "/fapi/v1/ticker/24hr", weight=40)

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
        return await self._market_request("GET", "/fapi/v1/klines", params=params, weight=1)

    async def get_mark_price(self, symbol: str | None = None) -> dict[str, Any]:
        params = {"symbol": symbol} if symbol else None
        return await self._market_request("GET", "/fapi/v1/premiumIndex", params=params, weight=1)

    async def get_funding_rate(self, symbol: str) -> float:
        data = await self.get_mark_price(symbol)
        return float(data.get("lastFundingRate", 0.0))

    async def get_funding_rate_history(
        self,
        symbol: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Get historical funding rate records.

        Args:
            symbol: Trading pair (e.g., BTCUSDT)
            limit: Number of records to return (max 1000)

        Returns:
            List of funding rate records with fundingTime, fundingRate, etc.
        """
        params = {"symbol": symbol, "limit": min(limit, 1000)}
        return await self._market_request("GET", "/fapi/v1/fundingRate", params=params, weight=1)

    async def get_open_interest(
        self,
        symbol: str,
        period: str = "1d",
        limit: int = 30,
    ) -> list[dict[str, Any]]:
        """Get open interest history.

        Args:
            symbol: Trading pair (e.g., BTCUSDT)
            period: Time interval (5m, 15m, 1h, 4h, 1d)
            limit: Number of records to return

        Returns:
            List of open interest records with timestamp, openInterest, turnover
        """
        params = {"symbol": symbol, "period": period, "limit": min(limit, 500)}
        return await self._market_request(
            "GET", "/fapi/v1/openInterestHist", params=params, weight=1
        )

    async def get_open_interest_stats(self, symbol: str) -> dict[str, Any]:
        """Get current open interest statistics.

        Args:
            symbol: Trading pair (e.g., BTCUSDT)

        Returns:
            Dict with openInterest, openInterestValue, etc.
        """
        params = {"symbol": symbol}
        return await self._market_request("GET", "/fapi/v1/openInterest", params=params, weight=1)

    async def get_long_short_account_ratio(
        self,
        symbol: str,
        period: str = "1d",
    ) -> dict[str, Any]:
        """Get global long/short account ratio.

        Args:
            symbol: Trading pair (e.g., BTCUSDT)
            period: Time interval (5m, 15m, 1h, 4h, 1d)

        Returns:
            Dict with longShortRatio, longAccount, shortAccount, timestamp
        """
        params = {"symbol": symbol, "period": period}
        return await self._market_request(
            "GET", "/fapi/v1/globalLongShortRatio", params=params, weight=1
        )

    async def get_top_trader_long_short_ratio(
        self,
        symbol: str,
        period: str = "1d",
    ) -> dict[str, Any]:
        """Get top trader long/short ratio.

        Args:
            symbol: Trading pair (e.g., BTCUSDT)
            period: Time interval (5m, 15m, 1h, 4h, 1d)

        Returns:
            Dict with longShortRatio, longAccount, shortAccount, timestamp
        """
        params = {"symbol": symbol, "period": period}
        return await self._market_request(
            "GET", "/fapi/v1/topLongShortRatio", params=params, weight=1
        )

    async def get_taker_long_short_ratio(
        self,
        symbol: str,
        period: str = "1d",
    ) -> dict[str, Any]:
        """Get taker buy/sell (long/short) ratio.

        Args:
            symbol: Trading pair (e.g., BTCUSDT)
            period: Time interval (5m, 15m, 1h, 4h, 1d)

        Returns:
            Dict with buySellRatio, buySellVolRatio, timestamp
        """
        params = {"symbol": symbol, "period": period}
        return await self._market_request(
            "GET", "/fapi/v1/takerlongshortRatio", params=params, weight=1
        )

    async def get_book_ticker(self, symbol: str) -> dict[str, Any]:
        """Get best bid/ask prices for a symbol from the book ticker endpoint."""
        params = {"symbol": symbol}
        return await self._market_request(
            "GET", "/fapi/v1/ticker/bookTicker", params=params, weight=1
        )

    async def get_spread_pct(self, symbol: str) -> float:
        """Return current spread as percentage of mid price.

        Args:
            symbol: Trading pair (e.g., BTCUSDT)

        Returns:
            Spread as percentage of mid price (e.g., 0.015 means 0.015%)
        """
        ticker = await self.get_book_ticker(symbol)
        bid = float(ticker["bidPrice"])
        ask = float(ticker["askPrice"])
        mid = (bid + ask) / 2
        return ((ask - bid) / mid) * 100 if mid > 0 else 0.0

    async def get_account_info(self) -> dict[str, Any]:
        return await self._request("GET", "/fapi/v2/account", signed=True, weight=5)

    async def get_position_risk(self) -> list[dict[str, Any]]:
        return await self._request("GET", "/fapi/v2/positionRisk", signed=True, weight=5)

    async def get_open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        params = {"symbol": symbol} if symbol else None
        return await self._request(
            "GET", "/fapi/v1/openOrders", params=params, signed=True, weight=1
        )

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
        return await self._request(
            "POST", "/fapi/v1/leverage", params=params, signed=True, weight=1
        )

    async def set_margin_type(self, symbol: str, margin_type: str) -> dict[str, Any]:
        params = {"symbol": symbol, "marginType": margin_type}
        return await self._request(
            "POST", "/fapi/v1/marginType", params=params, signed=True, weight=1
        )

    async def set_position_mode(self, dual_side_position: bool) -> dict[str, Any]:
        params = {"dualSidePosition": "true" if dual_side_position else "false"}
        return await self._request(
            "POST", "/fapi/v1/positionSide/dual", params=params, signed=True, weight=1
        )

    async def get_income_history(
        self,
        income_type: str | None = None,
        symbol: str | None = None,
        start_time: int | None = None,
        end_time: int | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """Get income history (fees, funding, PnL).

        Args:
            income_type: Filter by type: TRANSFER, WELCOME_BONUS, REALIZED_PNL,
                        FUNDING_FEE, COMMISSION, INSURANCE_CLEAR, REFERRAL_KICKBACK,
                        COMMISSION_REBATE, API_REBATE, CONTEST_REWARD, CROSS_COLLATERAL_TRANSFER,
                        OPTIONS_PREMIUM_FEE, OPTIONS_SETTLE_PROFIT, INTERNAL_TRANSFER,
                        AUTO_EXCHANGE, DELIVERED_SETTELMENT, COIN_SWAP_DEPOSIT, COIN_SWAP_WITHDRAW
            symbol: Filter by symbol (e.g., BTCUSDT)
            start_time: Start timestamp in milliseconds
            end_time: End timestamp in milliseconds
            limit: Max records to return (default 1000, max 1000)

        Returns:
            List of income records with: symbol, incomeType, income, asset, time, etc.
        """
        params: dict[str, Any] = {"limit": min(limit, 1000)}
        if income_type:
            params["incomeType"] = income_type
        if symbol:
            params["symbol"] = symbol
        if start_time:
            params["startTime"] = start_time
        if end_time:
            params["endTime"] = end_time
        return await self._request("GET", "/fapi/v1/income", params=params, signed=True, weight=30)

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
            # Use server-synced timestamp to avoid -1021 errors
            params["timestamp"] = int(time.time() * 1000) + self._server_time_offset
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
                # Update rate-limit headers from response
                self._update_rate_limit_headers(response)
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

    async def _market_request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        weight: int = 1,
    ) -> Any:
        """Make a request to the market data endpoint.

        Market data requests don't require signing and use the market data URL.
        """
        await self.rate_limiter.consume(weight)
        params = params.copy() if params else {}
        headers: dict[str, str] = {}

        log_http = self.settings.monitoring.log_http
        log_http_responses = self.settings.monitoring.log_http_responses
        max_body_chars = self.settings.monitoring.log_http_max_body_chars
        safe_params = self._sanitize_params(params)

        for attempt in range(3):
            start = time.perf_counter()
            try:
                if log_http:
                    self.log.info(
                        "rest_market_request",
                        method=method,
                        path=path,
                        params=safe_params,
                        weight=weight,
                        attempt=attempt + 1,
                    )
                response = await self.market_http.request(
                    method, path, params=params, headers=headers
                )
                latency_ms = (time.perf_counter() - start) * 1000
                if response.status_code == 429:
                    if log_http:
                        self.log.warning(
                            "rest_market_rate_limited",
                            method=method,
                            path=path,
                            status_code=response.status_code,
                            latency_ms=round(latency_ms, 2),
                        )
                    await asyncio.sleep(2**attempt)
                    continue
                response.raise_for_status()
                data = response.json()
                # Update rate-limit headers from response
                self._update_rate_limit_headers(response)
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
                    self.log.info("rest_market_response", **payload)
                return data
            except httpx.HTTPStatusError as exc:
                latency_ms = (time.perf_counter() - start) * 1000
                if exc.response.status_code in {429, 418, 500, 502, 503}:
                    if log_http:
                        self.log.warning(
                            "rest_market_http_error_retrying",
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
                        "rest_market_http_error",
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
                        "rest_market_request_error_retrying",
                        method=method,
                        path=path,
                        latency_ms=round(latency_ms, 2),
                        error=str(exc),
                    )
                await asyncio.sleep(2**attempt)
                continue
        response = await self.market_http.request(method, path, params=params, headers=headers)
        response.raise_for_status()
        data = response.json()
        if log_http:
            self.log.info(
                "rest_market_response",
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
