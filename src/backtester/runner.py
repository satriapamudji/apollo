"""CLI entrypoint for backtesting."""

from __future__ import annotations

import argparse
from pathlib import Path

from src.backtester.data import load_symbol_interval
from src.backtester.engine import Backtester
from src.backtester.reporting import compute_metrics, generate_report, print_summary
from src.config.settings import load_settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Run backtest for a symbol.")
    parser.add_argument("--symbol", required=True, help="Symbol, e.g. BTCUSDT")
    parser.add_argument("--interval", default="4h", help="Interval used in CSV filename")
    parser.add_argument("--data-path", default="./data/market", help="Path to CSV files")
    parser.add_argument("--config", default=None, help="Path to config.yaml")
    parser.add_argument(
        "--initial-equity",
        type=float,
        default=100.0,
        help="Initial equity for backtest (default: 100.0)",
    )
    parser.add_argument(
        "--fee-pct",
        type=float,
        default=0.0006,
        help="Trading fee percentage (default: 0.0006 = 0.06%%)",
    )
    parser.add_argument(
        "--slippage-pct",
        type=float,
        default=0.0005,
        help="Slippage percentage (default: 0.0005 = 0.05%%)",
    )
    parser.add_argument(
        "--out-dir",
        type=str,
        default=None,
        help="Output directory for report artifacts (trades.csv, equity.csv, summary.json)",
    )
    args = parser.parse_args()

    settings = load_settings(args.config)
    data = load_symbol_interval(args.data_path, args.symbol, args.interval)

    backtester = Backtester(
        strategy_config=settings.strategy,
        risk_config=settings.risk,
        initial_equity=args.initial_equity,
        fee_pct=args.fee_pct,
        slippage_pct=args.slippage_pct,
    )
    result = backtester.run(args.symbol, data)

    if args.out_dir:
        # Generate full report with file outputs
        out_path = Path(args.out_dir)
        generate_report(result, out_path, args.symbol)
    else:
        # Just print summary to console
        metrics = compute_metrics(result)
        print_summary(metrics)


if __name__ == "__main__":
    main()
