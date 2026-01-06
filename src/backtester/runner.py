"""CLI entrypoint for backtesting."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.backtester.data import load_funding_csv, load_symbol_interval
from src.backtester.engine import Backtester
from src.backtester.replay_engine import EventDrivenBacktester, MultiSymbolResult
from src.backtester.reporting import compute_metrics, generate_report, print_summary
from src.config.settings import load_settings
from src.strategy.package import (
    StrategyNotFoundError,
    StrategyParseError,
    create_strategy_metadata,
    load_strategy_spec,
    validate_data_requirements,
    validate_strategy_config,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run backtest for a symbol.")
    # Single symbol (legacy, backward compatible)
    parser.add_argument("--symbol", default=None, help="Symbol, e.g. BTCUSDT (single-symbol mode)")
    # Multi-symbol support
    parser.add_argument(
        "--symbols",
        type=str,
        default=None,
        help="Comma-separated symbols for multi-symbol backtest, e.g. BTCUSDT,ETHUSDT",
    )
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
        help="Execution model: 'ideal' or 'realistic' (variable slippage)",
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
    parser.add_argument(
        "--rules-file",
        type=str,
        default=None,
        help="Path to symbol rules JSON file (from download_exchange_info). "
        "If not specified, auto-detects from ./data/exchange_info/",
    )
    parser.add_argument(
        "--strategy",
        type=str,
        default=None,
        help="Strategy name (defaults to config.yaml strategy.name)",
    )
    parser.add_argument(
        "--skip-spec-validation",
        action="store_true",
        help="Skip strategy spec validation (not recommended)",
    )
    args = parser.parse_args()

    # Validate symbol arguments
    if args.symbol and args.symbols:
        print("ERROR: Cannot specify both --symbol and --symbols", file=sys.stderr)
        sys.exit(1)
    if not args.symbol and not args.symbols:
        print("ERROR: Must specify either --symbol or --symbols", file=sys.stderr)
        sys.exit(1)

    # Determine mode
    is_multi_symbol = args.symbols is not None
    symbols_list = args.symbols.split(",") if args.symbols else [args.symbol]

    settings = load_settings(args.config)

    # Strategy spec validation
    strategy_name = args.strategy or settings.strategy.name
    strategy_metadata = None

    if not args.skip_spec_validation:
        try:
            spec = load_strategy_spec(strategy_name)

            # Validate config overrides against spec
            config_errors = validate_strategy_config(spec, settings.strategy)
            if config_errors:
                print("ERROR: Invalid strategy configuration:", file=sys.stderr)
                for err in config_errors:
                    print(f"  - {err}", file=sys.stderr)
                sys.exit(1)

            # Validate data requirements
            available_intervals = {args.interval}
            # Check if daily can be derived from source interval
            if args.interval in ("4h", "1h", "15m"):
                available_intervals.add("1d")
            has_funding = args.funding_data is not None or args.constant_funding_rate is not None
            data_errors = validate_data_requirements(spec, available_intervals, has_funding)
            if data_errors:
                print("ERROR: Missing required data inputs:", file=sys.stderr)
                for err in data_errors:
                    print(f"  - {err}", file=sys.stderr)
                sys.exit(1)

            # Create metadata for reporting
            strategy_metadata = create_strategy_metadata(spec, settings.strategy)
            print(f"Strategy: {spec.name} v{spec.version} (hash: {spec.spec_hash[:12]}...)")

        except StrategyNotFoundError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            print(
                "Hint: Create strategies/{strategy_name}/strategy.md or use --skip-spec-validation",
                file=sys.stderr,
            )
            sys.exit(1)
        except StrategyParseError as e:
            print(f"ERROR: Failed to parse strategy spec: {e}", file=sys.stderr)
            sys.exit(1)

    if is_multi_symbol:
        # Multi-symbol mode using EventDrivenBacktester
        _run_multi_symbol(args, settings, symbols_list, strategy_metadata)
    else:
        # Single-symbol mode using legacy Backtester
        _run_single_symbol(args, settings, symbols_list[0], strategy_metadata)


def _run_single_symbol(args, settings, symbol: str, strategy_metadata) -> None:
    """Run single-symbol backtest using legacy engine."""
    data = load_symbol_interval(args.data_path, symbol, args.interval)

    # Load funding data if provided
    funding_data = None
    if args.funding_data:
        funding_data = load_funding_csv(symbol, args.funding_data)

    backtester = Backtester(
        strategy_config=settings.strategy,
        risk_config=settings.risk,
        initial_equity=args.initial_equity,
        fee_pct=args.fee_pct,
        slippage_pct=args.slippage_pct,
        execution_model=args.execution_model,
        random_seed=args.random_seed,
        regime_config=settings.regime,
        symbol_rules_path=args.rules_file,
    )
    result = backtester.run(
        symbol,
        data,
        funding_data=funding_data,
        constant_funding_rate=args.constant_funding_rate,
    )

    if args.out_dir:
        # Generate full report with file outputs
        out_path = Path(args.out_dir)
        generate_report(result, out_path, symbol, strategy_metadata=strategy_metadata)
    else:
        # Just print summary to console
        metrics = compute_metrics(result)
        print_summary(metrics)


def _run_multi_symbol(args, settings, symbols: list[str], strategy_metadata) -> None:
    """Run multi-symbol backtest using event-driven engine."""
    print(f"Multi-symbol backtest: {', '.join(symbols)}")

    engine = EventDrivenBacktester(
        strategy_config=settings.strategy,
        risk_config=settings.risk,
        symbols=symbols,
        data_path=args.data_path,
        interval=args.interval,
        initial_equity=args.initial_equity,
        fee_pct=args.fee_pct,
        execution_model=args.execution_model,
        slippage_pct=args.slippage_pct,
        random_seed=args.random_seed,
        regime_config=settings.regime,
        symbol_rules_path=args.rules_file,
        out_dir=args.out_dir,
    )

    result = engine.run()

    # Print summary
    _print_multi_symbol_summary(result)

    if args.out_dir:
        # Save results to output directory
        _save_multi_symbol_results(result, Path(args.out_dir), strategy_metadata)


def _print_multi_symbol_summary(result: MultiSymbolResult) -> None:
    """Print summary of multi-symbol backtest results."""
    print("\n" + "=" * 60)
    print("MULTI-SYMBOL BACKTEST RESULTS")
    print("=" * 60)
    print(f"Symbols traded: {', '.join(result.symbols_traded)}")
    print(f"Initial equity: {result.initial_equity:.2f}")
    print(f"Final equity: {result.final_equity:.2f}")
    print(f"Total return: {result.total_return * 100:.2f}%")
    print(f"Max drawdown: {result.max_drawdown * 100:.2f}%")
    print(f"Total trades: {result.total_trades}")
    print(f"Win rate: {result.win_rate * 100:.1f}%")
    print(f"Fill rate: {result.fill_rate * 100:.1f}%")
    print(f"Avg slippage: {result.avg_slippage_bps:.2f} bps")
    print(f"Total funding paid: {result.total_funding_paid:.4f}")
    print(f"Bars processed: {result.bars_processed}")
    print(f"Funding events: {result.funding_events_processed}")

    # Per-symbol breakdown
    print("\nPer-symbol breakdown:")
    for symbol, trades in result.trades_by_symbol.items():
        if trades:
            symbol_pnl = sum(t.net_pnl for t in trades)
            symbol_wins = sum(1 for t in trades if t.net_pnl > 0)
            symbol_wr = symbol_wins / len(trades) * 100 if trades else 0
            print(f"  {symbol}: {len(trades)} trades, PnL: {symbol_pnl:.4f}, WR: {symbol_wr:.1f}%")

    print("=" * 60)


def _serialize_strategy_metadata(strategy_metadata):
    """Convert strategy metadata to JSON-serializable dict."""
    if strategy_metadata is None:
        return None
    return {
        "name": strategy_metadata.name,
        "version": strategy_metadata.version,
        "spec_hash": strategy_metadata.spec_hash,
        "resolved_parameters": strategy_metadata.resolved_parameters,
    }


def _save_multi_symbol_results(result: MultiSymbolResult, out_dir: Path, strategy_metadata) -> None:
    """Save multi-symbol results to output directory."""
    import csv
    import json

    out_dir.mkdir(parents=True, exist_ok=True)

    # Save trades CSV
    trades_file = out_dir / "trades.csv"
    with open(trades_file, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "trade_id",
                "symbol",
                "direction",
                "entry_price",
                "exit_price",
                "quantity",
                "entry_time",
                "exit_time",
                "gross_pnl",
                "net_pnl",
                "holding_hours",
                "funding_cost",
            ]
        )
        for t in result.trades:
            writer.writerow(
                [
                    t.trade_id,
                    t.symbol,
                    t.direction,
                    t.entry_price,
                    t.exit_price,
                    t.quantity,
                    t.entry_time.isoformat(),
                    t.exit_time.isoformat(),
                    t.gross_pnl,
                    t.net_pnl,
                    t.holding_hours,
                    t.funding_cost,
                ]
            )

    # Save equity curve CSV
    equity_file = out_dir / "equity.csv"
    with open(equity_file, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "equity", "drawdown"])
        for ep in result.equity_curve:
            writer.writerow([ep.timestamp.isoformat(), ep.equity, ep.drawdown])

    # Save summary JSON
    summary = {
        "symbols_traded": result.symbols_traded,
        "initial_equity": result.initial_equity,
        "final_equity": result.final_equity,
        "total_return": result.total_return,
        "max_drawdown": result.max_drawdown,
        "total_trades": result.total_trades,
        "win_rate": result.win_rate,
        "fill_rate": result.fill_rate,
        "avg_slippage_bps": result.avg_slippage_bps,
        "missed_entries": result.missed_entries,
        "partial_fills": result.partial_fills,
        "total_funding_paid": result.total_funding_paid,
        "bars_processed": result.bars_processed,
        "funding_events_processed": result.funding_events_processed,
        "per_symbol": {
            symbol: {
                "trade_count": len(trades),
                "total_pnl": sum(t.net_pnl for t in trades),
            }
            for symbol, trades in result.trades_by_symbol.items()
            if trades
        },
    }
    if strategy_metadata:
        summary["strategy"] = _serialize_strategy_metadata(strategy_metadata)

    summary_file = out_dir / "summary.json"
    with open(summary_file, "w") as f:
        json.dump(summary, f, indent=2)

    print(f"\nResults saved to: {out_dir}")
    print(f"  - trades.csv: {len(result.trades)} trades")
    print(f"  - equity.csv: {len(result.equity_curve)} points")
    print("  - summary.json: metrics and per-symbol breakdown")


if __name__ == "__main__":
    main()
