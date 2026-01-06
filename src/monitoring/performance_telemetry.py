"""Live performance telemetry for strategy health monitoring.

Answers: "Are we actually making money, and why?"

Tracks:
- Fill rate (entries placed vs filled)
- Average slippage bps (entry/exit)
- Fees estimate, funding paid/received
- Expectancy per trade and rolling profit factor
- Time-in-market, average holding time
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from src.ledger.events import Event

if TYPE_CHECKING:
    from src.connectors.rest_client import BinanceRestClient
    from src.monitoring.metrics import Metrics

log = structlog.get_logger(__name__)


@dataclass
class CostSummary:
    """Summary of trading costs from Binance."""

    fees_paid: float = 0.0
    funding_received: float = 0.0
    funding_paid: float = 0.0
    start_time: datetime | None = None
    end_time: datetime | None = None

    @property
    def net_funding(self) -> float:
        return self.funding_received - self.funding_paid

    @property
    def total_costs(self) -> float:
        return self.fees_paid + self.funding_paid - self.funding_received


@dataclass
class TradeSummary:
    """Summary of trade performance from trades.csv."""

    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    total_holding_hours: float = 0.0
    entry_slippage_samples: list[float] = field(default_factory=list)
    exit_slippage_samples: list[float] = field(default_factory=list)

    @property
    def net_pnl(self) -> float:
        return self.gross_profit + self.gross_loss  # gross_loss is negative

    @property
    def win_rate_pct(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return (self.winning_trades / self.total_trades) * 100

    @property
    def expectancy(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return self.net_pnl / self.total_trades

    @property
    def profit_factor(self) -> float:
        if self.gross_loss == 0:
            return float("inf") if self.gross_profit > 0 else 0.0
        return abs(self.gross_profit / self.gross_loss)

    @property
    def avg_holding_hours(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return self.total_holding_hours / self.total_trades

    @property
    def avg_entry_slippage_bps(self) -> float:
        if not self.entry_slippage_samples:
            return 0.0
        return sum(self.entry_slippage_samples) / len(self.entry_slippage_samples)

    @property
    def avg_exit_slippage_bps(self) -> float:
        if not self.exit_slippage_samples:
            return 0.0
        return sum(self.exit_slippage_samples) / len(self.exit_slippage_samples)


@dataclass
class ExecutionSummary:
    """Summary of order execution metrics."""

    orders_placed: int = 0
    orders_filled: int = 0

    @property
    def fill_rate_pct(self) -> float:
        if self.orders_placed == 0:
            return 0.0
        return (self.orders_filled / self.orders_placed) * 100


@dataclass
class DailySummary:
    """Complete daily performance summary."""

    date: str
    session_start: datetime
    trades: TradeSummary
    execution: ExecutionSummary
    costs: CostSummary
    time_in_market_pct: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "date": self.date,
            "session_start": self.session_start.isoformat(),
            "trades": {
                "total": self.trades.total_trades,
                "wins": self.trades.winning_trades,
                "losses": self.trades.losing_trades,
                "win_rate_pct": round(self.trades.win_rate_pct, 2),
            },
            "performance": {
                "gross_profit": round(self.trades.gross_profit, 2),
                "gross_loss": round(self.trades.gross_loss, 2),
                "net_pnl": round(self.trades.net_pnl, 2),
                "expectancy": round(self.trades.expectancy, 2),
                "profit_factor": round(self.trades.profit_factor, 2)
                if self.trades.profit_factor != float("inf")
                else "inf",
            },
            "execution": {
                "orders_placed": self.execution.orders_placed,
                "orders_filled": self.execution.orders_filled,
                "fill_rate_pct": round(self.execution.fill_rate_pct, 2),
                "avg_entry_slippage_bps": round(self.trades.avg_entry_slippage_bps, 2),
                "avg_exit_slippage_bps": round(self.trades.avg_exit_slippage_bps, 2),
            },
            "costs": {
                "fees_paid": round(self.costs.fees_paid, 2),
                "funding_paid": round(self.costs.funding_paid, 2),
                "funding_received": round(self.costs.funding_received, 2),
                "net_funding": round(self.costs.net_funding, 2),
                "total_costs": round(self.costs.total_costs, 2),
            },
            "time": {
                "time_in_market_pct": round(self.time_in_market_pct, 2),
                "avg_holding_hours": round(self.trades.avg_holding_hours, 2),
            },
        }

    def to_log_line(self) -> str:
        """Format as structured log line."""
        pf = (
            f"{self.trades.profit_factor:.2f}"
            if self.trades.profit_factor != float("inf")
            else "inf"
        )
        return (
            f"date={self.date} "
            f"trades={self.trades.total_trades} "
            f"win_rate={self.trades.win_rate_pct:.1f}% "
            f"expectancy={self.trades.expectancy:.2f} "
            f"profit_factor={pf} "
            f"fill_rate={self.execution.fill_rate_pct:.1f}% "
            f"avg_slippage_bps={self.trades.avg_entry_slippage_bps:.1f} "
            f"fees={self.costs.fees_paid:.2f} "
            f"net_funding={self.costs.net_funding:.2f} "
            f"time_in_market={self.time_in_market_pct:.1f}%"
        )


class PerformanceTelemetry:
    """
    Track and expose live strategy performance metrics.

    Answers: "Are we actually making money, and why?"
    - Fill rate: entries placed vs filled
    - Slippage: average entry/exit slippage in bps
    - Costs: fees paid, funding paid/received
    - Expectancy: average PnL per trade
    - Profit factor: gross profit / gross loss
    - Time metrics: time-in-market, average holding time
    """

    def __init__(
        self,
        metrics: Metrics,
        rest_client: BinanceRestClient | None,
        logs_path: str,
        trades_csv_path: str,
        simulate: bool = False,
    ) -> None:
        self.metrics = metrics
        self.rest_client = rest_client
        self.logs_path = Path(logs_path)
        self.trades_csv_path = Path(trades_csv_path)
        self.simulate = simulate

        # Session tracking
        self.session_start = datetime.now(timezone.utc)

        # Execution tracking (in-memory, reset on restart)
        self._orders_placed: int = 0
        self._orders_filled: int = 0
        self._entry_slippage_samples: list[float] = []
        self._exit_slippage_samples: list[float] = []

        # Time-in-market tracking
        self._position_open_times: dict[str, datetime] = {}
        self._total_position_seconds: float = 0.0

        # Cost tracking from Binance (cached)
        self._last_cost_fetch: datetime | None = None
        self._cached_costs: CostSummary = CostSummary()

        # Ensure logs path exists
        self.logs_path.mkdir(parents=True, exist_ok=True)

    # === Event Handlers ===

    async def handle_order_placed(self, event: Event) -> None:
        """Track when entry orders are placed (non-reduce-only)."""
        payload = event.payload or {}
        # Only count entry orders (not reduce-only protective orders)
        if payload.get("reduce_only") or payload.get("reduceOnly"):
            return
        self._orders_placed += 1
        self.metrics.orders_placed_total.inc()
        self._update_fill_rate()

    async def handle_order_filled(self, event: Event) -> None:
        """Track when entry orders are filled."""
        payload = event.payload or {}
        # Only count entry orders (not reduce-only)
        if payload.get("reduce_only") or payload.get("reduceOnly"):
            return
        self._orders_filled += 1
        self.metrics.orders_filled_total.inc()
        self._update_fill_rate()

        # Track entry slippage if we have expected vs actual price
        # The slippage_bps histogram in metrics already captures this
        # We extract from the event if available
        expected_price = payload.get("expected_price")
        actual_price = payload.get("price")
        if expected_price and actual_price:
            slippage_bps = abs((actual_price - expected_price) / expected_price) * 10000
            self._entry_slippage_samples.append(slippage_bps)
            self._update_slippage_metrics()

    async def handle_position_opened(self, event: Event) -> None:
        """Track when positions are opened for time-in-market."""
        payload = event.payload or {}
        symbol = payload.get("symbol")
        if symbol:
            self._position_open_times[symbol] = event.timestamp

    async def handle_position_closed(self, event: Event) -> None:
        """Track when positions are closed."""
        payload = event.payload or {}
        symbol = payload.get("symbol")
        self.metrics.trades_closed_total.inc()

        # Update time-in-market
        if symbol and symbol in self._position_open_times:
            open_time = self._position_open_times.pop(symbol)
            duration = (event.timestamp - open_time).total_seconds()
            self._total_position_seconds += duration

        # Track exit slippage if available
        expected_exit = payload.get("expected_exit_price")
        actual_exit = payload.get("exit_price")
        if expected_exit and actual_exit:
            slippage_bps = abs((actual_exit - expected_exit) / expected_exit) * 10000
            self._exit_slippage_samples.append(slippage_bps)
            self._update_slippage_metrics()

    # === Metric Updates ===

    def _update_fill_rate(self) -> None:
        """Update fill rate gauge."""
        if self._orders_placed > 0:
            rate = (self._orders_filled / self._orders_placed) * 100
            self.metrics.fill_rate_pct.set(rate)

    def _update_slippage_metrics(self) -> None:
        """Update slippage gauges."""
        if self._entry_slippage_samples:
            avg = sum(self._entry_slippage_samples) / len(self._entry_slippage_samples)
            self.metrics.avg_entry_slippage_bps.set(avg)
        if self._exit_slippage_samples:
            avg = sum(self._exit_slippage_samples) / len(self._exit_slippage_samples)
            self.metrics.avg_exit_slippage_bps.set(avg)

    async def update_metrics(self) -> None:
        """
        Update all performance metrics.

        Should be called periodically (e.g., every 5 minutes).
        """
        # Compute trade metrics from CSV
        session_trades = self.compute_from_trades_csv()
        trades_7d = self.compute_from_trades_csv(window_days=7)
        trades_30d = self.compute_from_trades_csv(window_days=30)

        # Update performance metrics
        self.metrics.expectancy_per_trade.set(session_trades.expectancy)
        self.metrics.profit_factor_session.set(
            session_trades.profit_factor if session_trades.profit_factor != float("inf") else 0
        )
        self.metrics.profit_factor_7d.set(
            trades_7d.profit_factor if trades_7d.profit_factor != float("inf") else 0
        )
        self.metrics.profit_factor_30d.set(
            trades_30d.profit_factor if trades_30d.profit_factor != float("inf") else 0
        )
        self.metrics.win_rate_pct.set(session_trades.win_rate_pct)
        self.metrics.avg_holding_time_hours.set(session_trades.avg_holding_hours)

        # Update time-in-market
        time_in_market = self._compute_time_in_market()
        self.metrics.time_in_market_pct.set(time_in_market)

        # Fetch costs from Binance (if not in simulate mode)
        if not self.simulate and self.rest_client:
            costs = await self.fetch_costs_from_binance(self.session_start)
            self.metrics.net_funding.set(costs.net_funding)

    async def fetch_costs_from_binance(
        self, start_time: datetime, end_time: datetime | None = None
    ) -> CostSummary:
        """
        Fetch fee and funding data from Binance API.

        Args:
            start_time: Start of period to query
            end_time: End of period (defaults to now)

        Returns:
            CostSummary with fees and funding breakdown
        """
        if self.rest_client is None:
            return CostSummary()

        end_time = end_time or datetime.now(timezone.utc)
        start_ms = int(start_time.timestamp() * 1000)
        end_ms = int(end_time.timestamp() * 1000)

        summary = CostSummary(start_time=start_time, end_time=end_time)

        try:
            # Fetch commission (fees)
            fees = await self.rest_client.get_income_history(
                income_type="COMMISSION",
                start_time=start_ms,
                end_time=end_ms,
            )
            for record in fees:
                income = float(record.get("income", 0))
                summary.fees_paid += abs(income)  # Fees are negative in API
                self.metrics.fees_paid_total.inc(abs(income))

            # Fetch funding fees
            funding = await self.rest_client.get_income_history(
                income_type="FUNDING_FEE",
                start_time=start_ms,
                end_time=end_ms,
            )
            for record in funding:
                income = float(record.get("income", 0))
                if income > 0:
                    summary.funding_received += income
                    self.metrics.funding_received_total.inc(income)
                else:
                    summary.funding_paid += abs(income)
                    self.metrics.funding_paid_total.inc(abs(income))

            self._cached_costs = summary
            self._last_cost_fetch = datetime.now(timezone.utc)

        except Exception as exc:
            log.warning("failed_to_fetch_costs", error=str(exc))

        return summary

    def compute_from_trades_csv(self, window_days: int | None = None) -> TradeSummary:
        """
        Compute trade metrics from trades.csv.

        Args:
            window_days: If set, only include trades from the last N days.
                        If None, include all trades since session start.

        Returns:
            TradeSummary with computed metrics
        """
        summary = TradeSummary()

        if not self.trades_csv_path.exists():
            return summary

        cutoff = None
        if window_days is not None:
            cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
        else:
            cutoff = self.session_start

        try:
            with open(self.trades_csv_path, newline="") as handle:
                reader = csv.DictReader(handle)
                for row in reader:
                    # Skip open trades (no exit_time)
                    exit_time_str = row.get("exit_time", "").strip()
                    if not exit_time_str:
                        continue

                    # Parse exit time and check against cutoff
                    try:
                        exit_time = datetime.fromisoformat(exit_time_str.replace("Z", "+00:00"))
                    except ValueError:
                        continue

                    if exit_time < cutoff:
                        continue

                    # Count trade
                    summary.total_trades += 1

                    # Parse PnL
                    pnl_str = row.get("realized_pnl", "").strip()
                    if pnl_str:
                        try:
                            pnl = float(pnl_str)
                            if pnl > 0:
                                summary.winning_trades += 1
                                summary.gross_profit += pnl
                            else:
                                summary.losing_trades += 1
                                summary.gross_loss += pnl  # Already negative
                        except ValueError:
                            pass

                    # Parse holding time
                    holding_str = row.get("holding_hours", "").strip()
                    if holding_str:
                        try:
                            summary.total_holding_hours += float(holding_str)
                        except ValueError:
                            pass

        except Exception as exc:
            log.warning("failed_to_parse_trades_csv", error=str(exc))

        return summary

    def _compute_time_in_market(self) -> float:
        """Compute percentage of time with open positions."""
        now = datetime.now(timezone.utc)
        session_duration = (now - self.session_start).total_seconds()
        if session_duration <= 0:
            return 0.0

        # Add time for currently open positions
        current_open_time = sum(
            (now - open_time).total_seconds() for open_time in self._position_open_times.values()
        )
        total_time = self._total_position_seconds + current_open_time

        return (total_time / session_duration) * 100

    # === Daily Summary ===

    async def generate_daily_summary(self, date: datetime | None = None) -> DailySummary:
        """
        Generate a daily performance summary.

        Args:
            date: Date to generate summary for (defaults to today UTC)

        Returns:
            DailySummary with all metrics
        """
        date = date or datetime.now(timezone.utc)
        date_str = date.strftime("%Y-%m-%d")

        # Compute trade metrics
        trades = self.compute_from_trades_csv()

        # Compute execution metrics
        execution = ExecutionSummary(
            orders_placed=self._orders_placed,
            orders_filled=self._orders_filled,
        )
        # Add slippage samples to trade summary
        trades.entry_slippage_samples = self._entry_slippage_samples.copy()
        trades.exit_slippage_samples = self._exit_slippage_samples.copy()

        # Fetch costs from Binance
        costs = CostSummary()
        if not self.simulate and self.rest_client:
            costs = await self.fetch_costs_from_binance(self.session_start)

        # Compute time-in-market
        time_in_market = self._compute_time_in_market()

        return DailySummary(
            date=date_str,
            session_start=self.session_start,
            trades=trades,
            execution=execution,
            costs=costs,
            time_in_market_pct=time_in_market,
        )

    def write_daily_snapshot(self, summary: DailySummary) -> tuple[Path, Path]:
        """
        Write daily summary to JSON and CSV files.

        Args:
            summary: The daily summary to write

        Returns:
            Tuple of (json_path, csv_path)
        """
        json_path = self.logs_path / f"daily_summary_{summary.date}.json"
        csv_path = self.logs_path / f"daily_summary_{summary.date}.csv"

        # Write JSON
        with open(json_path, "w") as f:
            json.dump(summary.to_dict(), f, indent=2, default=str)

        # Write CSV (single row with all metrics)
        csv_fields = [
            "date",
            "trades",
            "wins",
            "losses",
            "win_rate_pct",
            "gross_profit",
            "gross_loss",
            "net_pnl",
            "expectancy",
            "profit_factor",
            "fill_rate_pct",
            "avg_entry_slippage_bps",
            "avg_exit_slippage_bps",
            "fees_paid",
            "funding_paid",
            "funding_received",
            "net_funding",
            "time_in_market_pct",
            "avg_holding_hours",
        ]
        csv_row = {
            "date": summary.date,
            "trades": summary.trades.total_trades,
            "wins": summary.trades.winning_trades,
            "losses": summary.trades.losing_trades,
            "win_rate_pct": round(summary.trades.win_rate_pct, 2),
            "gross_profit": round(summary.trades.gross_profit, 2),
            "gross_loss": round(summary.trades.gross_loss, 2),
            "net_pnl": round(summary.trades.net_pnl, 2),
            "expectancy": round(summary.trades.expectancy, 2),
            "profit_factor": round(summary.trades.profit_factor, 2)
            if summary.trades.profit_factor != float("inf")
            else "inf",
            "fill_rate_pct": round(summary.execution.fill_rate_pct, 2),
            "avg_entry_slippage_bps": round(summary.trades.avg_entry_slippage_bps, 2),
            "avg_exit_slippage_bps": round(summary.trades.avg_exit_slippage_bps, 2),
            "fees_paid": round(summary.costs.fees_paid, 2),
            "funding_paid": round(summary.costs.funding_paid, 2),
            "funding_received": round(summary.costs.funding_received, 2),
            "net_funding": round(summary.costs.net_funding, 2),
            "time_in_market_pct": round(summary.time_in_market_pct, 2),
            "avg_holding_hours": round(summary.trades.avg_holding_hours, 2),
        }

        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=csv_fields)
            writer.writeheader()
            writer.writerow(csv_row)

        return json_path, csv_path

    async def run_daily_summary(self) -> None:
        """
        Generate and log the daily summary.

        Writes snapshot files and logs the summary line.
        """
        summary = await self.generate_daily_summary()

        # Write snapshot files
        json_path, csv_path = self.write_daily_snapshot(summary)

        # Log the summary
        log.info("operator_summary", **{"summary": summary.to_log_line()})
        log.info(
            "daily_snapshot_written",
            json_path=str(json_path),
            csv_path=str(csv_path),
        )

    def get_execution_summary(self) -> ExecutionSummary:
        """Get current execution metrics summary."""
        return ExecutionSummary(
            orders_placed=self._orders_placed,
            orders_filled=self._orders_filled,
        )
