"""CLI entrypoint for backtesting."""

from __future__ import annotations

import argparse
from pathlib import Path

from src.backtester.data import load_funding_csv, load_symbol_interval
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
        help="Slippage percentage for ideal execution (default: 0.0005 = 0.05%%)",
    )
    parser.add_argument(
        "--execution-model",
        type=str,
        choices=["ideal", "realistic"],
        default="realistic",
        help="Execution model: 'ideal' (fixed slippage) or 'realistic' (variable slippage, fill probability)",
    )
    parser.add_argument(
        "--random-seed",
        type=int,
        default=None,
        help="Random seed for reproducibility (only used in realistic mode)",
    )
    parser.add_argument(
        "--out-dir",
        type=str,
        default=None,
        help="Output directory for report artifacts (trades.csv, equity.csv, summary.json)",
    )
    parser.add_argument(
        "--funding-data",
        type=str,
        default=None,
        help="Path to funding data directory (default: ./data)",
    )
    parser.add_argument(
        "--constant-funding-rate",
        type=float,
        default=None,
        help="Constant funding rate for stress testing (e.g., 0.0001 = 0.01%%)",
    )
    args = parser.parse_args()

    settings = load_settings(args.config)
    data = load_symbol_interval(args.data_path, args.symbol, args.interval)

    # Load funding data if provided
    funding_data = None
    if args.funding_data:
        funding_data = load_funding_csv(args.symbol, args.funding_data)

    backtester = Backtester(
        strategy_config=settings.strategy,
        risk_config=settings.risk,
        initial_equity=args.initial_equity,
        fee_pct=args.fee_pct,
        slippage_pct=args.slippage_pct,
        execution_model=args.execution_model,
        random_seed=args.random_seed,
        regime_config=settings.regime,
    )
    result = backtester.run(
        args.symbol,
        data,
        funding_data=funding_data,
        constant_funding_rate=args.constant_funding_rate,
    )

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
