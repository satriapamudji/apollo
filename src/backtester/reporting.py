"""Backtest reporting utilities for generating decision-grade reports."""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.backtester.engine import BacktestResult, EquityPoint, Trade

if TYPE_CHECKING:
    from src.strategy.package import StrategyMetadata


def compute_metrics(result: BacktestResult) -> dict[str, Any]:
    """Compute comprehensive metrics from backtest results.

    Returns a dictionary with all metrics needed for decision-making:
    - Basic stats: total trades, win rate, total return, max drawdown
    - Expectancy: avg_win * win_rate - avg_loss * loss_rate
    - Profit factor: sum(wins) / sum(losses)
    - Avg R-multiple: average risk-reward achieved
    - Max consecutive losses
    - Monthly returns table
    - Funding metrics: total funding paid, PnL with/without funding
    - Spread metrics: avg spread at entry, spread rejections
    """
    trades = result.trades
    metrics: dict[str, Any] = {
        "initial_equity": result.initial_equity,
        "final_equity": result.final_equity,
        "total_return": result.total_return,
        "total_return_pct": round(result.total_return * 100, 2),
        "total_trades": result.total_trades,
        "win_rate": result.win_rate,
        "win_rate_pct": round(result.win_rate * 100, 2),
        "max_drawdown": result.max_drawdown,
        "max_drawdown_pct": round(result.max_drawdown * 100, 2),
        # Funding metrics
        "total_funding_paid": result.total_funding_paid,
        "pnl_with_funding": result.pnl_with_funding,
        "pnl_without_funding": result.total_return * result.initial_equity
        + result.total_funding_paid,
        # Spread metrics
        "spread_rejections": result.spread_rejections,
        "avg_spread_at_entry_pct": round(result.avg_spread_at_entry_pct, 4),
        "spread_source": result.spread_source,
    }

    if not trades:
        metrics.update(
            {
                "expectancy": 0.0,
                "profit_factor": 0.0,
                "avg_r_multiple": 0.0,
                "max_consecutive_losses": 0,
                "avg_win": 0.0,
                "avg_loss": 0.0,
                "largest_win": 0.0,
                "largest_loss": 0.0,
                "avg_holding_hours": 0.0,
                "monthly_returns": {},
            }
        )
        return metrics

    # Separate wins and losses
    wins = [t for t in trades if t.net_pnl > 0]
    losses = [t for t in trades if t.net_pnl <= 0]

    # Average win/loss
    avg_win = sum(t.net_pnl for t in wins) / len(wins) if wins else 0.0
    avg_loss = abs(sum(t.net_pnl for t in losses) / len(losses)) if losses else 0.0

    # Expectancy: avg_win * win_rate - avg_loss * loss_rate
    win_rate = len(wins) / len(trades) if trades else 0.0
    loss_rate = len(losses) / len(trades) if trades else 0.0
    expectancy = avg_win * win_rate - avg_loss * loss_rate

    # Profit factor: sum(wins) / sum(losses)
    sum_wins = sum(t.net_pnl for t in wins) if wins else 0.0
    sum_losses = abs(sum(t.net_pnl for t in losses)) if losses else 0.0
    if sum_losses > 0:
        profit_factor = sum_wins / sum_losses
    elif sum_wins > 0:
        profit_factor = float("inf")
    else:
        profit_factor = 0.0

    # Average R-multiple (risk = entry - stop, R = pnl / risk)
    r_multiples = []
    for trade in trades:
        # Estimate risk from entry price (assume 2% stop as default if unknown)
        risk = abs(trade.entry_price * 0.02)
        if risk > 0:
            r_multiples.append(trade.net_pnl / risk)
    avg_r_multiple = sum(r_multiples) / len(r_multiples) if r_multiples else 0.0

    # Max consecutive losses
    max_consecutive_losses = 0
    current_streak = 0
    for trade in trades:
        if trade.net_pnl <= 0:
            current_streak += 1
            max_consecutive_losses = max(max_consecutive_losses, current_streak)
        else:
            current_streak = 0

    # Largest win/loss
    largest_win = max((t.net_pnl for t in wins), default=0.0)
    largest_loss = min((t.net_pnl for t in losses), default=0.0)

    # Average holding time
    avg_holding_hours = sum(t.holding_hours for t in trades) / len(trades) if trades else 0.0

    # Monthly returns
    monthly_pnl: dict[str, float] = defaultdict(float)
    for trade in trades:
        month_key = trade.exit_time.strftime("%Y-%m")
        monthly_pnl[month_key] += trade.net_pnl

    # Convert to returns (based on initial equity)
    monthly_returns = {
        month: round(pnl / result.initial_equity * 100, 2)
        for month, pnl in sorted(monthly_pnl.items())
    }

    metrics.update(
        {
            "expectancy": round(expectancy, 4),
            "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else "inf",
            "avg_r_multiple": round(avg_r_multiple, 2),
            "max_consecutive_losses": max_consecutive_losses,
            "avg_win": round(avg_win, 4),
            "avg_loss": round(avg_loss, 4),
            "largest_win": round(largest_win, 4),
            "largest_loss": round(largest_loss, 4),
            "avg_holding_hours": round(avg_holding_hours, 2),
            "winning_trades": len(wins),
            "losing_trades": len(losses),
            "monthly_returns": monthly_returns,
        }
    )

    return metrics


def write_trade_csv(trades: list[Trade], path: Path) -> None:
    """Write trade list to CSV file."""
    fieldnames = [
        "trade_id",
        "symbol",
        "side",
        "entry_time",
        "exit_time",
        "entry_price",
        "exit_price",
        "quantity",
        "gross_pnl",
        "net_pnl",
        "funding_cost",
        "pnl_without_funding",
        "holding_hours",
        "spread_at_entry_pct",
    ]

    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for trade in trades:
            pnl_without_funding = trade.net_pnl + trade.funding_cost
            writer.writerow(
                {
                    "trade_id": trade.trade_id,
                    "symbol": trade.symbol,
                    "side": trade.direction,
                    "entry_time": trade.entry_time.isoformat(),
                    "exit_time": trade.exit_time.isoformat(),
                    "entry_price": trade.entry_price,
                    "exit_price": trade.exit_price,
                    "quantity": trade.quantity,
                    "gross_pnl": round(trade.gross_pnl, 6),
                    "net_pnl": round(trade.net_pnl, 6),
                    "funding_cost": round(trade.funding_cost, 6),
                    "pnl_without_funding": round(pnl_without_funding, 6),
                    "holding_hours": round(trade.holding_hours, 2),
                    "spread_at_entry_pct": round(trade.spread_at_entry_pct, 4),
                }
            )


def write_equity_csv(equity_curve: list[EquityPoint], path: Path) -> None:
    """Write equity curve to CSV file."""
    fieldnames = ["timestamp", "equity", "drawdown", "drawdown_pct"]

    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for point in equity_curve:
            writer.writerow(
                {
                    "timestamp": point.timestamp.isoformat(),
                    "equity": round(point.equity, 6),
                    "drawdown": round(point.drawdown, 6),
                    "drawdown_pct": round(point.drawdown * 100, 2),
                }
            )


def write_summary_json(metrics: dict[str, Any], path: Path) -> None:
    """Write summary metrics to JSON file."""
    with open(path, "w") as f:
        json.dump(metrics, f, indent=2, default=str)


def print_summary(metrics: dict[str, Any]) -> None:
    """Print a formatted summary to console."""
    print("\n" + "=" * 60)
    print("BACKTEST SUMMARY")
    print("=" * 60)

    print(f"\n{'PERFORMANCE':=^40}")
    print(f"  Total Return:       {metrics['total_return_pct']:>10.2f}%")
    print(f"  Final Equity:       {metrics['final_equity']:>10.2f}")
    print(f"  Max Drawdown:       {metrics['max_drawdown_pct']:>10.2f}%")

    # Funding breakdown
    total_funding = metrics.get("total_funding_paid", 0.0)
    pnl_with_funding = metrics.get(
        "pnl_with_funding", metrics["total_return"] * metrics["initial_equity"]
    )
    pnl_without_funding = metrics.get("pnl_without_funding", pnl_with_funding - total_funding)

    print(f"\n{'FUNDING BREAKDOWN':=^40}")
    print(f"  PnL (no funding):   {pnl_without_funding:>10.2f}")
    print(
        f"  Total Funding Paid: ({abs(total_funding):>10.2f})"
        if total_funding >= 0
        else f"  Total Funding Paid:  {abs(total_funding):>10.2f}"
    )
    print(f"  PnL (with funding): {pnl_with_funding:>10.2f}")

    print(f"\n{'TRADES':=^40}")
    print(f"  Total Trades:       {metrics['total_trades']:>10}")
    print(f"  Winning Trades:     {metrics.get('winning_trades', 0):>10}")
    print(f"  Losing Trades:      {metrics.get('losing_trades', 0):>10}")
    print(f"  Win Rate:           {metrics['win_rate_pct']:>10.2f}%")

    print(f"\n{'QUALITY METRICS':=^40}")
    print(f"  Expectancy:         {metrics['expectancy']:>10.4f}")
    print(f"  Profit Factor:      {metrics['profit_factor']:>10}")
    print(f"  Avg R-Multiple:     {metrics['avg_r_multiple']:>10.2f}")
    print(f"  Max Consec. Losses: {metrics['max_consecutive_losses']:>10}")

    print(f"\n{'TRADE DETAILS':=^40}")
    print(f"  Avg Win:            {metrics['avg_win']:>10.4f}")
    print(f"  Avg Loss:           {metrics['avg_loss']:>10.4f}")
    print(f"  Largest Win:        {metrics['largest_win']:>10.4f}")
    print(f"  Largest Loss:       {metrics['largest_loss']:>10.4f}")
    print(f"  Avg Holding (hrs):  {metrics['avg_holding_hours']:>10.2f}")

    monthly = metrics.get("monthly_returns", {})
    if monthly:
        print(f"\n{'MONTHLY RETURNS (%)':=^40}")
        for month, ret in monthly.items():
            print(f"  {month}:           {ret:>10.2f}%")

    # Spread metrics (only show if spread model was used)
    spread_source = metrics.get("spread_source", "none")
    if spread_source != "none":
        print(f"\n{'SPREAD ANALYSIS':=^40}")
        print(f"  Spread Source:      {spread_source:>10}")
        print(f"  Avg Spread Entry:   {metrics.get('avg_spread_at_entry_pct', 0.0):>10.4f}%")
        print(f"  Spread Rejections:  {metrics.get('spread_rejections', 0):>10}")

    print("\n" + "=" * 60)


def generate_report(
    result: BacktestResult,
    out_dir: Path,
    symbol: str,
    strategy_metadata: StrategyMetadata | None = None,
) -> dict[str, Any]:
    """Generate full backtest report with all artifacts.

    Creates:
    - trades.csv: Detailed trade list
    - equity.csv: Equity curve over time
    - summary.json: All computed metrics

    Args:
        result: BacktestResult from backtester engine
        out_dir: Output directory for report files
        symbol: Symbol that was backtested
        strategy_metadata: Optional strategy metadata for recording spec info

    Returns the computed metrics dictionary.
    """
    # Ensure output directory exists
    out_dir.mkdir(parents=True, exist_ok=True)

    # Compute metrics
    metrics = compute_metrics(result)
    metrics["symbol"] = symbol
    metrics["generated_at"] = datetime.utcnow().isoformat()

    # Add strategy metadata if provided
    if strategy_metadata is not None:
        metrics["strategy"] = {
            "name": strategy_metadata.name,
            "version": strategy_metadata.version,
            "spec_hash": strategy_metadata.spec_hash,
            "resolved_parameters": strategy_metadata.resolved_parameters,
        }

    # Write files
    write_trade_csv(result.trades, out_dir / "trades.csv")
    write_equity_csv(result.equity_curve, out_dir / "equity.csv")
    write_summary_json(metrics, out_dir / "summary.json")

    # Print to console
    print_summary(metrics)

    print(f"\nReport saved to: {out_dir}")
    print(f"  - trades.csv ({len(result.trades)} trades)")
    print(f"  - equity.csv ({len(result.equity_curve)} points)")
    print("  - summary.json")

    return metrics
