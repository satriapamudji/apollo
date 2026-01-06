"""CLI tool to download Binance Futures exchangeInfo for reproducible backtesting.

Usage:
    python -m src.tools.download_exchange_info
    python -m src.tools.download_exchange_info --output-dir ./data/exchange_info

This tool downloads the current exchangeInfo from Binance Futures and saves:
1. Raw response JSON with timestamped filename
2. Normalized symbol rules table derived from filters

The symbol rules are used by the backtester to ensure realistic position sizing
with accurate tick_size, step_size, min_qty, and min_notional constraints.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

import httpx

# Binance Futures public API base URL
BASE_URL = "https://fapi.binance.com"


async def fetch_exchange_info(client: httpx.AsyncClient) -> dict[str, Any]:
    """Fetch exchangeInfo from Binance Futures API with retries."""
    for attempt in range(5):
        try:
            response = await client.get("/fapi/v1/exchangeInfo")

            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", 60))
                print(f"Rate limited. Waiting {retry_after}s...")
                await asyncio.sleep(retry_after)
                continue

            if response.status_code == 418:
                print("IP temporarily banned. Waiting 120s...")
                await asyncio.sleep(120)
                continue

            response.raise_for_status()
            return cast(dict[str, Any], response.json())

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

    raise RuntimeError("Failed to fetch exchangeInfo after 5 attempts")


def parse_symbol_rules(exchange_info: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Parse exchangeInfo into normalized symbol rules.

    Extracts per-symbol:
    - tick_size (price increment) from PRICE_FILTER
    - step_size (qty increment) from LOT_SIZE
    - min_qty from LOT_SIZE
    - min_notional from MIN_NOTIONAL or NOTIONAL filter
    - Contract metadata: contractType, status, quoteAsset

    Returns a dict keyed by symbol name.
    """
    rules: dict[str, dict[str, Any]] = {}

    for symbol_info in exchange_info.get("symbols", []):
        symbol = symbol_info.get("symbol", "")
        if not symbol:
            continue

        # Initialize with defaults
        rule: dict[str, Any] = {
            "symbol": symbol,
            "tick_size": 0.01,
            "step_size": 0.001,
            "min_qty": 0.001,
            "min_notional": 5.0,
            "contract_type": symbol_info.get("contractType", "UNKNOWN"),
            "status": symbol_info.get("status", "UNKNOWN"),
            "quote_asset": symbol_info.get("quoteAsset", "UNKNOWN"),
            "base_asset": symbol_info.get("baseAsset", "UNKNOWN"),
            "margin_asset": symbol_info.get("marginAsset", "UNKNOWN"),
            "price_precision": symbol_info.get("pricePrecision", 2),
            "quantity_precision": symbol_info.get("quantityPrecision", 3),
            "defaults_applied": [],
        }

        # Track which defaults were applied
        found_price_filter = False
        found_lot_size = False
        found_min_notional = False

        # Parse filters
        for flt in symbol_info.get("filters", []):
            filter_type = flt.get("filterType")

            if filter_type == "PRICE_FILTER":
                found_price_filter = True
                tick_size = flt.get("tickSize")
                if tick_size:
                    rule["tick_size"] = float(tick_size)

            elif filter_type == "LOT_SIZE":
                found_lot_size = True
                min_qty = flt.get("minQty")
                step_size = flt.get("stepSize")
                if min_qty:
                    rule["min_qty"] = float(min_qty)
                if step_size:
                    rule["step_size"] = float(step_size)

            elif filter_type == "MARKET_LOT_SIZE":
                # Use as fallback if LOT_SIZE not found
                if not found_lot_size:
                    min_qty = flt.get("minQty")
                    step_size = flt.get("stepSize")
                    if min_qty:
                        rule["min_qty"] = max(rule["min_qty"], float(min_qty))
                    if step_size:
                        rule["step_size"] = max(rule["step_size"], float(step_size))

            elif filter_type in {"MIN_NOTIONAL", "NOTIONAL"}:
                found_min_notional = True
                # Try 'notional' first (newer format), then 'minNotional' (older)
                notional = flt.get("notional") or flt.get("minNotional")
                if notional:
                    rule["min_notional"] = float(notional)

        # Record defaults applied
        if not found_price_filter:
            rule["defaults_applied"].append("tick_size")
        if not found_lot_size:
            rule["defaults_applied"].append("step_size")
            rule["defaults_applied"].append("min_qty")
        if not found_min_notional:
            rule["defaults_applied"].append("min_notional")

        rules[symbol] = rule

    return rules


async def download_exchange_info(output_dir: Path) -> tuple[Path, Path]:
    """Download exchangeInfo and save raw + normalized files.

    Returns tuple of (raw_file_path, rules_file_path).
    """
    # Create directories
    raw_dir = output_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    # Get current timestamp
    now = datetime.now(timezone.utc)
    timestamp_str = now.strftime("%Y%m%d_%H%M%S")

    print(f"Downloading exchangeInfo from {BASE_URL}...")

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30.0) as client:
        exchange_info = await fetch_exchange_info(client)

    # Save raw response
    raw_file = raw_dir / f"exchange_info_{timestamp_str}.json"
    with open(raw_file, "w") as f:
        json.dump(exchange_info, f, indent=2)
    print(f"Saved raw exchangeInfo to {raw_file}")

    # Parse and save normalized rules
    rules = parse_symbol_rules(exchange_info)

    rules_data = {
        "effective_date": now.isoformat(),
        "source_file": raw_file.name,
        "server_time": exchange_info.get("serverTime"),
        "timezone": exchange_info.get("timezone", "UTC"),
        "symbol_count": len(rules),
        "rules": rules,
    }

    rules_file = output_dir / f"symbol_rules_{timestamp_str}.json"
    with open(rules_file, "w") as f:
        json.dump(rules_data, f, indent=2)
    print(f"Saved {len(rules)} symbol rules to {rules_file}")

    # Update 'latest' symlink/file for convenience
    latest_file = output_dir / "symbol_rules_latest.json"
    with open(latest_file, "w") as f:
        json.dump(rules_data, f, indent=2)
    print(f"Updated latest rules at {latest_file}")

    return raw_file, rules_file


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download Binance Futures exchangeInfo for backtesting."
    )
    parser.add_argument(
        "--output-dir",
        default="./data/exchange_info",
        help="Output directory. Default: ./data/exchange_info",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    asyncio.run(download_exchange_info(output_dir))


if __name__ == "__main__":
    main()
