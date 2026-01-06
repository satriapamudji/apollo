"""CLI tool to build a universe snapshot for backtesting.

Usage:
    python -m src.tools.build_universe --as-of-date 2024-01-01 --size 10

This tool fetches ticker and exchange info data from Binance Futures
and creates a reproducible universe snapshot file.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from src.data.models import (
    UniverseProvenance,
    UniverseSelectionParams,
    UniverseSnapshot,
    UniverseSymbol,
)

# Binance Futures public API base URL
BASE_URL = "https://fapi.binance.com"

# Rate limiting
REQUEST_DELAY = 0.1  # 100ms between requests


async def fetch_24h_tickers(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    """Fetch 24-hour ticker data for all symbols."""
    for attempt in range(5):
        try:
            response = await client.get("/fapi/v1/ticker/24hr")
            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", 60))
                print(f"Rate limited. Waiting {retry_after}s...")
                await asyncio.sleep(retry_after)
                continue
            response.raise_for_status()
            data = response.json()
            if isinstance(data, list):
                return data
            return []
        except (httpx.HTTPStatusError, httpx.RequestError) as e:
            wait_time = 2**attempt
            print(f"Error fetching tickers: {e}. Retrying in {wait_time}s...")
            await asyncio.sleep(wait_time)
    raise RuntimeError("Failed to fetch 24h tickers after 5 attempts")


async def fetch_exchange_info(client: httpx.AsyncClient) -> dict[str, Any]:
    """Fetch exchange info for symbol filtering."""
    for attempt in range(5):
        try:
            response = await client.get("/fapi/v1/exchangeInfo")
            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", 60))
                print(f"Rate limited. Waiting {retry_after}s...")
                await asyncio.sleep(retry_after)
                continue
            response.raise_for_status()
            data = response.json()
            if isinstance(data, dict):
                return data
            return {}
        except (httpx.HTTPStatusError, httpx.RequestError) as e:
            wait_time = 2**attempt
            print(f"Error fetching exchange info: {e}. Retrying in {wait_time}s...")
            await asyncio.sleep(wait_time)
    raise RuntimeError("Failed to fetch exchange info after 5 attempts")


def parse_exchange_info(
    exchange_info: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Parse exchange info into a symbol -> info mapping."""
    symbols_map: dict[str, dict[str, Any]] = {}
    for sym in exchange_info.get("symbols", []):
        symbol = sym.get("symbol", "")
        if symbol:
            symbols_map[symbol] = {
                "status": sym.get("status", ""),
                "contractType": sym.get("contractType", ""),
                "quoteAsset": sym.get("quoteAsset", ""),
                "baseAsset": sym.get("baseAsset", ""),
            }
    return symbols_map


def build_universe(
    tickers: list[dict[str, Any]],
    exchange_info_map: dict[str, dict[str, Any]],
    min_quote_volume_usd: float,
    size: int,
    allow_list: list[str] | None = None,
    deny_list: list[str] | None = None,
) -> tuple[list[UniverseSymbol], dict[str, int]]:
    """Build universe from ticker and exchange info data.

    Returns:
        Tuple of (selected symbols, filter stats)
    """
    stats = {
        "total_fetched": len(tickers),
        "after_status_filter": 0,
        "after_volume_filter": 0,
    }

    # Filter and enrich with exchange info
    candidates: list[dict[str, Any]] = []
    for ticker in tickers:
        symbol = ticker.get("symbol", "")
        if not symbol:
            continue

        # Get exchange info for this symbol
        info = exchange_info_map.get(symbol, {})
        status = info.get("status", "")
        contract_type = info.get("contractType", "")
        quote_asset = info.get("quoteAsset", "")

        # Filter: must be TRADING, PERPETUAL, USDT-margined
        if status != "TRADING":
            continue
        if contract_type != "PERPETUAL":
            continue
        if quote_asset != "USDT":
            continue

        # Apply deny list
        if deny_list and symbol in deny_list:
            continue

        candidates.append(
            {
                "symbol": symbol,
                "quote_volume": float(ticker.get("quoteVolume", 0)),
                "last_price": float(ticker.get("lastPrice", 0)),
                "price_change_pct": float(ticker.get("priceChangePercent", 0)),
                "high": float(ticker.get("highPrice", 0)),
                "low": float(ticker.get("lowPrice", 0)),
                "trades": int(ticker.get("count", 0)),
                "status": status,
                "contract_type": contract_type,
                "quote_asset": quote_asset,
            }
        )

    stats["after_status_filter"] = len(candidates)

    # Filter by minimum volume
    candidates = [c for c in candidates if c["quote_volume"] >= min_quote_volume_usd]
    stats["after_volume_filter"] = len(candidates)

    # Sort by volume descending
    candidates.sort(key=lambda x: x["quote_volume"], reverse=True)

    # Apply allow list (if specified, only include these symbols)
    if allow_list:
        allowed_set = set(allow_list)
        candidates = [c for c in candidates if c["symbol"] in allowed_set]

    # Take top N
    selected = candidates[:size]

    # Convert to UniverseSymbol models
    universe_symbols = [
        UniverseSymbol(
            symbol=c["symbol"],
            quote_volume_24h=c["quote_volume"],
            last_price=c["last_price"],
            price_change_pct_24h=c["price_change_pct"],
            high_24h=c["high"],
            low_24h=c["low"],
            trades_24h=c["trades"],
            status=c["status"],
            contract_type=c["contract_type"],
            quote_asset=c["quote_asset"],
        )
        for c in selected
    ]

    return universe_symbols, stats


async def build_universe_snapshot(
    as_of_date: str,
    min_quote_volume_usd: float,
    size: int,
    output_dir: Path,
    allow_list: list[str] | None = None,
    deny_list: list[str] | None = None,
) -> Path:
    """Build a complete universe snapshot.

    Args:
        as_of_date: Date string in YYYY-MM-DD format
        min_quote_volume_usd: Minimum 24h quote volume threshold
        size: Number of symbols to select
        output_dir: Directory to write output file
        allow_list: Optional list of symbols to include (filters to only these)
        deny_list: Optional list of symbols to exclude

    Returns:
        Path to the created universe file
    """
    fetch_time = datetime.now(timezone.utc)
    errors: list[str] = []

    print(f"Building universe snapshot for {as_of_date}")
    print(f"  Min quote volume: ${min_quote_volume_usd:,.0f}")
    print(f"  Target size: {size}")

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30.0) as client:
        # Fetch ticker data
        print("Fetching 24h ticker data...")
        try:
            tickers = await fetch_24h_tickers(client)
            print(f"  Fetched {len(tickers)} tickers")
        except RuntimeError as e:
            errors.append(str(e))
            tickers = []

        await asyncio.sleep(REQUEST_DELAY)

        # Fetch exchange info
        print("Fetching exchange info...")
        try:
            exchange_info = await fetch_exchange_info(client)
            exchange_info_map = parse_exchange_info(exchange_info)
            print(f"  Fetched info for {len(exchange_info_map)} symbols")
        except RuntimeError as e:
            errors.append(str(e))
            exchange_info_map = {}

    if not tickers or not exchange_info_map:
        raise RuntimeError(f"Failed to fetch required data: {errors}")

    # Build universe
    print("Building universe...")
    symbols, stats = build_universe(
        tickers=tickers,
        exchange_info_map=exchange_info_map,
        min_quote_volume_usd=min_quote_volume_usd,
        size=size,
        allow_list=allow_list,
        deny_list=deny_list,
    )

    print(f"  Total symbols fetched: {stats['total_fetched']}")
    print(f"  After status filter: {stats['after_status_filter']}")
    print(f"  After volume filter: {stats['after_volume_filter']}")
    print(f"  Selected: {len(symbols)}")

    # Create snapshot
    snapshot_id = f"universe_{as_of_date}_{uuid4().hex[:8]}"
    snapshot = UniverseSnapshot(
        snapshot_id=snapshot_id,
        as_of_date=as_of_date,
        created_at_utc=fetch_time,
        selection_params=UniverseSelectionParams(
            min_quote_volume_usd=min_quote_volume_usd,
            size=size,
            allow_list=allow_list,
            deny_list=deny_list,
        ),
        symbols=symbols,
        provenance=UniverseProvenance(
            fetch_timestamp_utc=fetch_time,
            total_symbols_fetched=stats["total_fetched"],
            symbols_after_status_filter=stats["after_status_filter"],
            symbols_after_volume_filter=stats["after_volume_filter"],
            errors=errors,
        ),
    )

    # Write output
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"universe_{as_of_date}.json"

    with open(output_path, "w") as f:
        json.dump(snapshot.model_dump(mode="json"), f, indent=2, default=str)

    print(f"\nUniverse snapshot written to: {output_path}")
    print(f"Symbols selected: {[s.symbol for s in symbols]}")

    return output_path


def parse_date(date_str: str) -> str:
    """Validate and return date string in YYYY-MM-DD format."""
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
        return date_str
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"Invalid date format: {date_str}. Use YYYY-MM-DD") from e


def parse_symbol_list(value: str) -> list[str]:
    """Parse comma-separated symbol list."""
    if not value:
        return []
    return [s.strip().upper() for s in value.split(",") if s.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a universe snapshot for backtesting.")
    parser.add_argument(
        "--as-of-date",
        type=parse_date,
        default=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        help="Date for the snapshot in YYYY-MM-DD format. Default: today",
    )
    parser.add_argument(
        "--min-quote-volume",
        type=float,
        default=50_000_000,
        help="Minimum 24h quote volume in USD. Default: 50,000,000",
    )
    parser.add_argument(
        "--size",
        type=int,
        default=10,
        help="Number of symbols to select. Default: 10",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("./data/datasets/usdm/store/universe"),
        help="Output directory. Default: ./data/datasets/usdm/store/universe",
    )
    parser.add_argument(
        "--allow-list",
        type=parse_symbol_list,
        default=None,
        help="Comma-separated list of symbols to include (filters to only these)",
    )
    parser.add_argument(
        "--deny-list",
        type=parse_symbol_list,
        default=None,
        help="Comma-separated list of symbols to exclude",
    )
    args = parser.parse_args()

    asyncio.run(
        build_universe_snapshot(
            as_of_date=args.as_of_date,
            min_quote_volume_usd=args.min_quote_volume,
            size=args.size,
            output_dir=args.output_dir,
            allow_list=args.allow_list if args.allow_list else None,
            deny_list=args.deny_list if args.deny_list else None,
        )
    )


if __name__ == "__main__":
    main()
