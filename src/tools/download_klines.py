"""CLI tool to download Binance Futures klines for backtesting.

Usage:
    python -m src.tools.download_klines --symbol BTCUSDT --interval 4h --start 2024-01-01

This tool downloads historical klines from Binance Futures and saves them
in a format compatible with the backtester's load_ohlcv_csv function.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

# Binance Futures public API base URL
BASE_URL = "https://fapi.binance.com"

# Rate limiting: Binance allows 1200 weight/minute, klines endpoint = 1 weight
MAX_REQUESTS_PER_MINUTE = 1000  # Conservative limit
REQUEST_DELAY = 60.0 / MAX_REQUESTS_PER_MINUTE

# Binance returns max 1500 klines per request
MAX_KLINES_PER_REQUEST = 1500


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


async def fetch_klines(
    client: httpx.AsyncClient,
    symbol: str,
    interval: str,
    start_time: int,
    end_time: int,
    limit: int = MAX_KLINES_PER_REQUEST,
) -> list[list[Any]]:
    """Fetch klines from Binance Futures API with retries."""
    params = {
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
                # Rate limited - wait and retry
                retry_after = int(response.headers.get("Retry-After", 60))
                print(f"Rate limited. Waiting {retry_after}s...")
                await asyncio.sleep(retry_after)
                continue

            if response.status_code == 418:
                # IP banned - wait longer
                print("IP temporarily banned. Waiting 120s...")
                await asyncio.sleep(120)
                continue

            response.raise_for_status()
            return response.json()

        except httpx.HTTPStatusError as e:
            if e.response.status_code in {500, 502, 503}:
                wait_time = 2**attempt
                print(f"Server error {e.response.status_code}. Retrying in {wait_time}s...")
                await asyncio.sleep(wait_time)
                continue
            raise

        except httpx.RequestError as e:
            wait_time = 2**attempt
            print(f"Request error: {e}. Retrying in {wait_time}s...")
            await asyncio.sleep(wait_time)
            continue

    raise RuntimeError(f"Failed to fetch klines after 5 attempts: {symbol} {interval}")


async def download_klines(
    symbol: str,
    interval: str,
    start_date: datetime,
    end_date: datetime,
    output_path: Path,
) -> int:
    """Download klines for a symbol and interval, save to CSV.

    Returns the number of klines downloaded.
    """
    start_ms = datetime_to_ms(start_date)
    end_ms = datetime_to_ms(end_date)
    interval_ms = interval_to_ms(interval)

    all_klines: list[list[Any]] = []
    current_start = start_ms

    print(f"Downloading {symbol} {interval} from {start_date.date()} to {end_date.date()}")

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30.0) as client:
        while current_start < end_ms:
            # Calculate end time for this batch
            batch_end = min(current_start + (MAX_KLINES_PER_REQUEST * interval_ms), end_ms)

            klines = await fetch_klines(
                client,
                symbol,
                interval,
                current_start,
                batch_end,
            )

            if not klines:
                print(f"No more data available after {ms_to_datetime(current_start)}")
                break

            all_klines.extend(klines)

            # Update progress
            last_time = ms_to_datetime(int(klines[-1][0]))
            print(f"  Downloaded {len(all_klines)} klines... (up to {last_time.date()})")

            # Move to next batch (start from the next candle after the last one)
            current_start = int(klines[-1][0]) + interval_ms

            # Rate limiting delay
            await asyncio.sleep(REQUEST_DELAY)

    if not all_klines:
        print("No klines downloaded!")
        return 0

    # Ensure output directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Write to CSV
    # Binance kline format: [open_time, open, high, low, close, volume, close_time, ...]
    # We use close_time as the timestamp (consistent with live trading)
    fieldnames = ["timestamp", "open", "high", "low", "close", "volume"]

    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for kline in all_klines:
            writer.writerow({
                "timestamp": kline[6],  # close_time in ms
                "open": kline[1],
                "high": kline[2],
                "low": kline[3],
                "close": kline[4],
                "volume": kline[5],
            })

    print(f"Saved {len(all_klines)} klines to {output_path}")
    return len(all_klines)


async def download_funding_rates(
    symbol: str,
    start_date: datetime,
    end_date: datetime,
    output_path: Path,
) -> int:
    """Download funding rate history for a symbol, save to CSV.

    Returns the number of funding records downloaded.
    """
    start_ms = datetime_to_ms(start_date)
    end_ms = datetime_to_ms(end_date)

    all_funding: list[dict[str, Any]] = []
    current_start = start_ms

    print(f"Downloading {symbol} funding rates from {start_date.date()} to {end_date.date()}")

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30.0) as client:
        while current_start < end_ms:
            params = {
                "symbol": symbol,
                "startTime": current_start,
                "endTime": end_ms,
                "limit": 1000,
            }

            for attempt in range(5):
                try:
                    response = await client.get("/fapi/v1/fundingRate", params=params)

                    if response.status_code == 429:
                        retry_after = int(response.headers.get("Retry-After", 60))
                        print(f"Rate limited. Waiting {retry_after}s...")
                        await asyncio.sleep(retry_after)
                        continue

                    response.raise_for_status()
                    funding_data = response.json()
                    break

                except (httpx.HTTPStatusError, httpx.RequestError) as e:
                    wait_time = 2**attempt
                    print(f"Error: {e}. Retrying in {wait_time}s...")
                    await asyncio.sleep(wait_time)
            else:
                raise RuntimeError("Failed to fetch funding rates after 5 attempts")

            if not funding_data:
                break

            all_funding.extend(funding_data)

            # Update progress
            last_time = ms_to_datetime(int(funding_data[-1]["fundingTime"]))
            print(f"  Downloaded {len(all_funding)} funding records... (up to {last_time.date()})")

            # Move to next batch
            current_start = int(funding_data[-1]["fundingTime"]) + 1

            await asyncio.sleep(REQUEST_DELAY)

    if not all_funding:
        print("No funding data downloaded!")
        return 0

    # Ensure output directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Write to CSV
    fieldnames = ["timestamp", "symbol", "funding_rate", "mark_price"]

    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for record in all_funding:
            writer.writerow({
                "timestamp": record["fundingTime"],
                "symbol": record["symbol"],
                "funding_rate": record["fundingRate"],
                "mark_price": record.get("markPrice", ""),
            })

    print(f"Saved {len(all_funding)} funding records to {output_path}")
    return len(all_funding)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download Binance Futures klines for backtesting."
    )
    parser.add_argument(
        "--symbol",
        required=True,
        help="Trading symbol, e.g., BTCUSDT",
    )
    parser.add_argument(
        "--interval",
        default="4h",
        help="Candle interval (1m, 5m, 15m, 1h, 4h, 1d, 1w). Default: 4h",
    )
    parser.add_argument(
        "--start",
        required=True,
        help="Start date in YYYY-MM-DD format",
    )
    parser.add_argument(
        "--end",
        default=None,
        help="End date in YYYY-MM-DD format. Default: today",
    )
    parser.add_argument(
        "--output-dir",
        default="./data/market",
        help="Output directory. Default: ./data/market",
    )
    parser.add_argument(
        "--funding",
        action="store_true",
        help="Also download funding rate history to ./data/funding/",
    )
    args = parser.parse_args()

    # Parse dates
    start_date = parse_date(args.start)
    end_date = parse_date(args.end) if args.end else datetime.now(timezone.utc)

    # Validate dates
    if start_date >= end_date:
        print("Error: Start date must be before end date")
        return

    # Download klines
    output_path = Path(args.output_dir) / f"{args.symbol}_{args.interval}.csv"
    asyncio.run(download_klines(args.symbol, args.interval, start_date, end_date, output_path))

    # Optionally download funding rates
    if args.funding:
        funding_path = Path("./data/funding") / f"{args.symbol}.csv"
        asyncio.run(download_funding_rates(args.symbol, start_date, end_date, funding_path))


if __name__ == "__main__":
    main()
