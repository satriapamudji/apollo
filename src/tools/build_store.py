"""CLI tool to build the shared data store for backtesting.

Usage:
    python -m src.tools.build_store \
        --universe ./data/datasets/usdm/store/universe/universe_2024-01-01.json \
        --start 2024-01-01 \
        --end 2024-06-01 \
        --intervals 4h \
        --include-funding

This tool downloads market data artifacts (klines, funding, exchange info)
and stores them in a partitioned, append-only structure.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from src.data.models import SymbolRules, SymbolRulesSnapshot, UniverseSnapshot

# Binance Futures public API base URL
BASE_URL = "https://fapi.binance.com"

# Rate limiting: conservative limit
MAX_REQUESTS_PER_MINUTE = 1000
REQUEST_DELAY = 60.0 / MAX_REQUESTS_PER_MINUTE

# Binance returns max 1500 klines per request
MAX_KLINES_PER_REQUEST = 1500

# Funding rate max limit per request
MAX_FUNDING_PER_REQUEST = 1000


def parse_date(date_str: str) -> datetime:
    """Parse date string in YYYY-MM-DD format to datetime."""
    return datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def datetime_to_ms(dt: datetime) -> int:
    """Convert datetime to milliseconds since epoch."""
    return int(dt.timestamp() * 1000)


def ms_to_datetime(ms: int) -> datetime:
    """Convert milliseconds since epoch to datetime."""
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def interval_to_ms(interval: str) -> int:
    """Convert interval string to milliseconds."""
    multipliers = {
        "m": 60 * 1000,
        "h": 60 * 60 * 1000,
        "d": 24 * 60 * 60 * 1000,
        "w": 7 * 24 * 60 * 60 * 1000,
    }
    unit = interval[-1]
    value = int(interval[:-1])
    return value * multipliers.get(unit, 60 * 1000)


def get_month_key(dt: datetime) -> str:
    """Get month key in YYYY-MM format."""
    return dt.strftime("%Y-%m")


def compute_sha256(file_path: Path) -> str:
    """Compute SHA256 hash of a file."""
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256_hash.update(chunk)
    return sha256_hash.hexdigest()


async def fetch_klines(
    client: httpx.AsyncClient,
    symbol: str,
    interval: str,
    start_time: int,
    end_time: int,
    limit: int = MAX_KLINES_PER_REQUEST,
) -> list[list[Any]]:
    """Fetch klines from Binance Futures API with retries."""
    params: dict[str, str | int] = {
        "symbol": symbol,
        "interval": interval,
        "startTime": start_time,
        "endTime": end_time,
        "limit": limit,
    }

    for attempt in range(5):
        try:
            response = await client.get("/fapi/v1/klines", params=params)
            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", 60))
                print(f"  Rate limited. Waiting {retry_after}s...")
                await asyncio.sleep(retry_after)
                continue
            if response.status_code == 418:
                print("  IP temporarily banned. Waiting 120s...")
                await asyncio.sleep(120)
                continue
            response.raise_for_status()
            data = response.json()
            if isinstance(data, list):
                return data
            return []
        except httpx.HTTPStatusError as e:
            if e.response.status_code in {500, 502, 503}:
                wait_time = 2**attempt
                print(f"  Server error {e.response.status_code}. Retrying in {wait_time}s...")
                await asyncio.sleep(wait_time)
                continue
            raise
        except httpx.RequestError as e:
            wait_time = 2**attempt
            print(f"  Request error: {e}. Retrying in {wait_time}s...")
            await asyncio.sleep(wait_time)
            continue

    raise RuntimeError(f"Failed to fetch klines after 5 attempts: {symbol} {interval}")


async def fetch_funding_rates(
    client: httpx.AsyncClient,
    symbol: str,
    start_time: int,
    end_time: int,
    limit: int = MAX_FUNDING_PER_REQUEST,
) -> list[dict[str, Any]]:
    """Fetch funding rate history from Binance Futures API with retries."""
    params: dict[str, str | int] = {
        "symbol": symbol,
        "startTime": start_time,
        "endTime": end_time,
        "limit": limit,
    }

    for attempt in range(5):
        try:
            response = await client.get("/fapi/v1/fundingRate", params=params)
            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", 60))
                print(f"  Rate limited. Waiting {retry_after}s...")
                await asyncio.sleep(retry_after)
                continue
            response.raise_for_status()
            data = response.json()
            if isinstance(data, list):
                return data
            return []
        except (httpx.HTTPStatusError, httpx.RequestError) as e:
            wait_time = 2**attempt
            print(f"  Error: {e}. Retrying in {wait_time}s...")
            await asyncio.sleep(wait_time)

    raise RuntimeError(f"Failed to fetch funding rates after 5 attempts: {symbol}")


async def fetch_exchange_info(client: httpx.AsyncClient) -> dict[str, Any]:
    """Fetch exchange info for symbol rules."""
    for attempt in range(5):
        try:
            response = await client.get("/fapi/v1/exchangeInfo")
            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", 60))
                print(f"  Rate limited. Waiting {retry_after}s...")
                await asyncio.sleep(retry_after)
                continue
            response.raise_for_status()
            data = response.json()
            if isinstance(data, dict):
                return data
            return {}
        except (httpx.HTTPStatusError, httpx.RequestError) as e:
            wait_time = 2**attempt
            print(f"  Error: {e}. Retrying in {wait_time}s...")
            await asyncio.sleep(wait_time)

    raise RuntimeError("Failed to fetch exchange info after 5 attempts")


def parse_symbol_rules(exchange_info: dict[str, Any], effective_date: str) -> list[SymbolRules]:
    """Parse exchange info into normalized symbol rules."""
    rules: list[SymbolRules] = []

    for sym in exchange_info.get("symbols", []):
        symbol = sym.get("symbol", "")
        if not symbol:
            continue

        # Extract filter values
        tick_size = 0.01
        step_size = 0.001
        min_qty = 0.001
        max_qty = 1000000.0
        min_notional = 5.0

        for filt in sym.get("filters", []):
            filter_type = filt.get("filterType", "")
            if filter_type == "PRICE_FILTER":
                tick_size = float(filt.get("tickSize", tick_size))
            elif filter_type == "LOT_SIZE":
                step_size = float(filt.get("stepSize", step_size))
                min_qty = float(filt.get("minQty", min_qty))
                max_qty = float(filt.get("maxQty", max_qty))
            elif filter_type == "MIN_NOTIONAL":
                min_notional = float(filt.get("notional", min_notional))

        rules.append(
            SymbolRules(
                symbol=symbol,
                tick_size=tick_size,
                step_size=step_size,
                min_qty=min_qty,
                max_qty=max_qty,
                min_notional=min_notional,
                contract_type=sym.get("contractType", ""),
                status=sym.get("status", ""),
                quote_asset=sym.get("quoteAsset", ""),
                base_asset=sym.get("baseAsset", ""),
                effective_date=effective_date,
            )
        )

    return rules


async def download_klines_for_symbol(
    client: httpx.AsyncClient,
    symbol: str,
    interval: str,
    start_date: datetime,
    end_date: datetime,
    output_dir: Path,
) -> list[Path]:
    """Download klines for a symbol, partitioned by month.

    Returns:
        List of created file paths
    """
    start_ms = datetime_to_ms(start_date)
    end_ms = datetime_to_ms(end_date)
    interval_ms = interval_to_ms(interval)

    print(f"  Downloading {symbol} {interval} klines...")

    # Collect all klines
    all_klines: list[list[Any]] = []
    current_start = start_ms

    while current_start < end_ms:
        batch_end = min(current_start + (MAX_KLINES_PER_REQUEST * interval_ms), end_ms)

        klines = await fetch_klines(client, symbol, interval, current_start, batch_end)

        if not klines:
            break

        all_klines.extend(klines)
        current_start = int(klines[-1][0]) + interval_ms

        await asyncio.sleep(REQUEST_DELAY)

    if not all_klines:
        print(f"    No klines found for {symbol}")
        return []

    # Group by month and write partitioned files
    month_data: dict[str, list[list[Any]]] = {}
    for kline in all_klines:
        close_time_ms = int(kline[6])
        dt = ms_to_datetime(close_time_ms)
        month_key = get_month_key(dt)
        if month_key not in month_data:
            month_data[month_key] = []
        month_data[month_key].append(kline)

    created_files: list[Path] = []
    base_dir = output_dir / "bars" / "trade" / f"interval={interval}" / f"symbol={symbol}"
    base_dir.mkdir(parents=True, exist_ok=True)

    for month_key, klines in sorted(month_data.items()):
        file_path = base_dir / f"month={month_key}.csv"

        # Append-only: skip if file already exists
        if file_path.exists():
            print(f"    Skipping existing file: {file_path}")
            continue

        fieldnames = [
            "open_time",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "close_time",
            "quote_volume",
            "trades",
            "taker_buy_volume",
            "taker_buy_quote_volume",
        ]

        with open(file_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()

            for kline in klines:
                writer.writerow(
                    {
                        "open_time": kline[0],
                        "open": kline[1],
                        "high": kline[2],
                        "low": kline[3],
                        "close": kline[4],
                        "volume": kline[5],
                        "close_time": kline[6],
                        "quote_volume": kline[7],
                        "trades": kline[8],
                        "taker_buy_volume": kline[9],
                        "taker_buy_quote_volume": kline[10],
                    }
                )

        created_files.append(file_path)
        print(f"    Created: {file_path} ({len(klines)} rows)")

    return created_files


async def download_funding_for_symbol(
    client: httpx.AsyncClient,
    symbol: str,
    start_date: datetime,
    end_date: datetime,
    output_dir: Path,
) -> list[Path]:
    """Download funding history for a symbol, partitioned by month.

    Returns:
        List of created file paths
    """
    start_ms = datetime_to_ms(start_date)
    end_ms = datetime_to_ms(end_date)

    print(f"  Downloading {symbol} funding history...")

    # Collect all funding records
    all_funding: list[dict[str, Any]] = []
    current_start = start_ms

    while current_start < end_ms:
        funding_data = await fetch_funding_rates(client, symbol, current_start, end_ms)

        if not funding_data:
            break

        all_funding.extend(funding_data)
        current_start = int(funding_data[-1]["fundingTime"]) + 1

        await asyncio.sleep(REQUEST_DELAY)

    if not all_funding:
        print(f"    No funding data found for {symbol}")
        return []

    # Group by month and write partitioned files
    month_data: dict[str, list[dict[str, Any]]] = {}
    for record in all_funding:
        funding_time_ms = int(record["fundingTime"])
        dt = ms_to_datetime(funding_time_ms)
        month_key = get_month_key(dt)
        if month_key not in month_data:
            month_data[month_key] = []
        month_data[month_key].append(record)

    created_files: list[Path] = []
    base_dir = output_dir / "funding" / f"symbol={symbol}"
    base_dir.mkdir(parents=True, exist_ok=True)

    for month_key, records in sorted(month_data.items()):
        file_path = base_dir / f"month={month_key}.csv"

        # Append-only: skip if file already exists
        if file_path.exists():
            print(f"    Skipping existing file: {file_path}")
            continue

        fieldnames = ["funding_time", "symbol", "funding_rate", "mark_price"]

        with open(file_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()

            for record in records:
                writer.writerow(
                    {
                        "funding_time": record["fundingTime"],
                        "symbol": record["symbol"],
                        "funding_rate": record["fundingRate"],
                        "mark_price": record.get("markPrice", ""),
                    }
                )

        created_files.append(file_path)
        print(f"    Created: {file_path} ({len(records)} rows)")

    return created_files


async def download_exchange_info(
    client: httpx.AsyncClient,
    output_dir: Path,
    effective_date: str,
) -> tuple[Path, Path]:
    """Download and save exchange info.

    Returns:
        Tuple of (raw JSON path, symbol rules JSON path)
    """
    print("Downloading exchange info...")
    exchange_info = await fetch_exchange_info(client)

    # Save raw exchange info
    metadata_dir = output_dir / "metadata" / "exchangeInfo"
    metadata_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    raw_path = metadata_dir / f"{timestamp}.json"

    with open(raw_path, "w") as f:
        json.dump(exchange_info, f, indent=2)

    print(f"  Saved raw exchange info: {raw_path}")

    # Parse and save symbol rules
    rules = parse_symbol_rules(exchange_info, effective_date)
    rules_snapshot = SymbolRulesSnapshot(
        snapshot_id=f"rules_{effective_date}",
        fetched_at_utc=datetime.now(timezone.utc),
        rules=rules,
    )

    rules_dir = output_dir / "metadata" / "symbol_rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    rules_path = rules_dir / f"{effective_date}.json"

    with open(rules_path, "w") as f:
        json.dump(rules_snapshot.model_dump(mode="json"), f, indent=2, default=str)

    print(f"  Saved symbol rules: {rules_path} ({len(rules)} symbols)")

    return raw_path, rules_path


def load_universe(universe_path: Path) -> UniverseSnapshot:
    """Load a universe snapshot from JSON file."""
    with open(universe_path) as f:
        data = json.load(f)
    return UniverseSnapshot.model_validate(data)


async def build_store(
    universe_path: Path,
    start_date: datetime,
    end_date: datetime,
    intervals: list[str],
    output_dir: Path,
    include_funding: bool = True,
    include_exchange_info: bool = True,
) -> dict[str, Any]:
    """Build the shared data store.

    Args:
        universe_path: Path to universe snapshot JSON
        start_date: Start date for data download
        end_date: End date for data download
        intervals: List of kline intervals to download
        output_dir: Output directory for store
        include_funding: Whether to download funding history
        include_exchange_info: Whether to download exchange info

    Returns:
        Summary of created files
    """
    # Load universe
    universe = load_universe(universe_path)
    symbols = [s.symbol for s in universe.symbols]

    print(f"Building store for {len(symbols)} symbols: {symbols}")
    print(f"Time range: {start_date.date()} to {end_date.date()}")
    print(f"Intervals: {intervals}")

    summary: dict[str, Any] = {
        "symbols": symbols,
        "intervals": intervals,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "kline_files": [],
        "funding_files": [],
        "metadata_files": [],
    }

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30.0) as client:
        # Download exchange info
        if include_exchange_info:
            raw_path, rules_path = await download_exchange_info(
                client, output_dir, start_date.strftime("%Y-%m-%d")
            )
            summary["metadata_files"].extend([str(raw_path), str(rules_path)])
            await asyncio.sleep(REQUEST_DELAY)

        # Download klines for each symbol and interval
        for symbol in symbols:
            for interval in intervals:
                files = await download_klines_for_symbol(
                    client, symbol, interval, start_date, end_date, output_dir
                )
                summary["kline_files"].extend([str(f) for f in files])

        # Download funding history for each symbol
        if include_funding:
            for symbol in symbols:
                files = await download_funding_for_symbol(
                    client, symbol, start_date, end_date, output_dir
                )
                summary["funding_files"].extend([str(f) for f in files])

    print("\n=== Store Build Summary ===")
    print(f"Kline files created: {len(summary['kline_files'])}")
    print(f"Funding files created: {len(summary['funding_files'])}")
    print(f"Metadata files created: {len(summary['metadata_files'])}")

    return summary


def parse_intervals(value: str) -> list[str]:
    """Parse comma-separated interval list."""
    return [i.strip() for i in value.split(",") if i.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the shared data store for backtesting.")
    parser.add_argument(
        "--universe",
        type=Path,
        required=True,
        help="Path to universe snapshot JSON file",
    )
    parser.add_argument(
        "--start",
        type=str,
        required=True,
        help="Start date in YYYY-MM-DD format",
    )
    parser.add_argument(
        "--end",
        type=str,
        default=None,
        help="End date in YYYY-MM-DD format. Default: today",
    )
    parser.add_argument(
        "--intervals",
        type=parse_intervals,
        default=["4h"],
        help="Comma-separated kline intervals. Default: 4h",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("./data/datasets/usdm/store"),
        help="Output directory. Default: ./data/datasets/usdm/store",
    )
    parser.add_argument(
        "--include-funding",
        action="store_true",
        default=True,
        help="Include funding history download (default: true)",
    )
    parser.add_argument(
        "--no-funding",
        action="store_true",
        help="Skip funding history download",
    )
    parser.add_argument(
        "--no-exchange-info",
        action="store_true",
        help="Skip exchange info download",
    )
    args = parser.parse_args()

    start_date = parse_date(args.start)
    end_date = parse_date(args.end) if args.end else datetime.now(timezone.utc)

    if start_date >= end_date:
        print("Error: Start date must be before end date")
        return

    include_funding = not args.no_funding
    include_exchange_info = not args.no_exchange_info

    asyncio.run(
        build_store(
            universe_path=args.universe,
            start_date=start_date,
            end_date=end_date,
            intervals=args.intervals,
            output_dir=args.output_dir,
            include_funding=include_funding,
            include_exchange_info=include_exchange_info,
        )
    )


if __name__ == "__main__":
    main()
