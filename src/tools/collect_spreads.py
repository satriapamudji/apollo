"""CLI tool to collect bid-ask spread snapshots from Binance Futures.

Usage:
    python -m src.tools.collect_spreads --symbols BTCUSDT,ETHUSDT --interval 60 --duration 3600

This tool periodically samples the bookTicker endpoint to build a historical
spread dataset for use in backtesting with the HistoricalSpreadProvider.

Output format: CSV with columns [timestamp, symbol, bid, ask, bid_qty, ask_qty]
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


async def fetch_book_ticker(
    client: httpx.AsyncClient,
    symbol: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch current best bid/ask from Binance Futures.

    Args:
        client: HTTP client
        symbol: Specific symbol to fetch, or None for all symbols

    Returns:
        List of book ticker dicts with keys: symbol, bidPrice, bidQty, askPrice, askQty, time
    """
    url = f"{BASE_URL}/fapi/v1/ticker/bookTicker"
    params = {}
    if symbol:
        params["symbol"] = symbol

    response = await client.get(url, params=params if params else None)
    response.raise_for_status()

    data = response.json()
    # Single symbol returns a dict, multiple returns a list
    if isinstance(data, dict):
        return [data]
    return data


def format_timestamp(ts: datetime) -> str:
    """Format timestamp as ISO 8601 string."""
    return ts.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


async def collect_spreads(
    symbols: list[str],
    interval_sec: int,
    duration_sec: int,
    output_dir: Path,
) -> None:
    """Collect spread snapshots for specified symbols.

    Args:
        symbols: List of trading symbols to track
        interval_sec: Seconds between samples
        duration_sec: Total duration to collect data
        output_dir: Directory to save CSV files
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # Open CSV files for each symbol
    files = {}
    writers = {}
    fieldnames = ["timestamp", "symbol", "bid", "ask", "bid_qty", "ask_qty"]

    for symbol in symbols:
        filepath = output_dir / f"{symbol}_spreads.csv"
        file_exists = filepath.exists()
        f = open(filepath, "a", newline="")
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        files[symbol] = f
        writers[symbol] = writer

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            start_time = datetime.now(timezone.utc)
            samples_collected = 0

            print(f"Collecting spreads for {symbols}")
            print(f"Interval: {interval_sec}s, Duration: {duration_sec}s")
            print(f"Output: {output_dir}")
            print("-" * 50)

            while True:
                elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
                if elapsed >= duration_sec:
                    break

                sample_time = datetime.now(timezone.utc)

                for symbol in symbols:
                    try:
                        tickers = await fetch_book_ticker(client, symbol)
                        for ticker in tickers:
                            row = {
                                "timestamp": format_timestamp(sample_time),
                                "symbol": ticker["symbol"],
                                "bid": float(ticker["bidPrice"]),
                                "ask": float(ticker["askPrice"]),
                                "bid_qty": float(ticker["bidQty"]),
                                "ask_qty": float(ticker["askQty"]),
                            }
                            writers[symbol].writerow(row)
                            files[symbol].flush()
                    except Exception as e:
                        print(f"Error fetching {symbol}: {e}")

                samples_collected += 1
                remaining = duration_sec - elapsed
                print(
                    f"\rSamples: {samples_collected}, "
                    f"Elapsed: {elapsed:.0f}s, "
                    f"Remaining: {remaining:.0f}s",
                    end="",
                )

                await asyncio.sleep(interval_sec)

            print(f"\n\nCollection complete. {samples_collected} samples per symbol.")

    finally:
        for f in files.values():
            f.close()


def main() -> None:
    """Entry point for the spread collector CLI."""
    parser = argparse.ArgumentParser(
        description="Collect bid-ask spread snapshots from Binance Futures"
    )
    parser.add_argument(
        "--symbols",
        type=str,
        required=True,
        help="Comma-separated list of symbols (e.g., BTCUSDT,ETHUSDT)",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=60,
        help="Seconds between samples (default: 60)",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=3600,
        help="Total duration in seconds (default: 3600 = 1 hour)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="./data/spreads",
        help="Output directory for CSV files (default: ./data/spreads)",
    )

    args = parser.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",")]
    output_dir = Path(args.output_dir)

    asyncio.run(
        collect_spreads(
            symbols=symbols,
            interval_sec=args.interval,
            duration_sec=args.duration,
            output_dir=output_dir,
        )
    )


if __name__ == "__main__":
    main()
