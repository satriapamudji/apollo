"""Backtesting engine for the trend-following strategy.

Event-based funding model:
- Funding is applied as discrete cashflows at settlement timestamps only
- No per-bar duration accrual (each settlement is applied exactly once)
- Correct payer/receiver direction based on position side
- No leverage multiplier (funding is based on notional, not margin)
- Equity is updated at settlement times, trade close only adds gross_pnl - fees
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import pandas as pd

from src.backtester.execution_sim import ExecutionSimulator
from src.backtester.funding import FundingRateProvider
from src.backtester.spread import (
    ConservativeSpreadModel,
    HistoricalSpreadProvider,
    HybridSpreadProvider,
    SpreadModel,
)
from src.backtester.symbol_rules import SymbolRulesSnapshot, auto_load_rules
from src.config.settings import RegimeConfig, RiskConfig, StrategyConfig
from src.ledger.state import Position, TradingState
from src.models import TradeProposal
from src.risk.engine import RiskEngine
from src.risk.sizing import SymbolFilters
from src.strategy.signals import SignalGenerator, SignalType

if TYPE_CHECKING:
    from src.ledger.state import PositionSide


@dataclass(frozen=True)
class Trade:
    trade_id: str
    symbol: str
    direction: str
    entry_price: float
    exit_price: float
    quantity: float
    entry_time: datetime
    exit_time: datetime
    gross_pnl: float
    net_pnl: float  # gross_pnl - fees - funding_cost (for reporting)
    holding_hours: float
    slippage_bps: float = 0.0
    is_partial_fill: bool = False
    funding_cost: float = 0.0  # Sum of settlement cashflows during hold (positive=paid)
    spread_at_entry_pct: float = 0.0  # Bid-ask spread at entry time (percentage)


@dataclass(frozen=True)
class EquityPoint:
    """A point on the equity curve."""

    timestamp: datetime
    equity: float
    drawdown: float


@dataclass(frozen=True)
class BacktestResult:
    """Results from a backtest run."""

    trades: list[Trade]
    equity_curve: list[EquityPoint]
    total_return: float
    win_rate: float
    max_drawdown: float
    total_trades: int
    final_equity: float
    initial_equity: float
    # Execution simulation metrics
    fill_rate: float = 1.0
    avg_slippage_bps: float = 0.0
    missed_entries: int = 0
    partial_fills: int = 0
    # Funding metrics
    total_funding_paid: float = 0.0  # Sum of all funding cashflows (positive=paid)
    pnl_with_funding: float = 0.0  # Final equity - initial equity
    # Spread metrics
    spread_rejections: int = 0  # Number of entries rejected due to wide spread
    avg_spread_at_entry_pct: float = 0.0  # Average spread at entry (percentage)
    spread_source: Literal["none", "model", "historical", "hybrid"] = "none"


class Backtester:
    """Backtest the trend-following strategy on historical data.

    Uses event-based funding model where funding is applied as discrete
    cashflows at settlement timestamps only (no per-bar duration accrual).
    """

    def __init__(
        self,
        strategy_config: StrategyConfig,
        risk_config: RiskConfig,
        initial_equity: float = 100.0,
        fee_pct: float = 0.0006,
        slippage_pct: float = 0.0005,
        execution_model: str = "ideal",
        random_seed: int | None = None,
        regime_config: RegimeConfig | None = None,
        symbol_rules_path: Path | str | None = None,
        max_spread_pct: float | None = None,
        spread_model: SpreadModel | None = None,
        spread_data: pd.DataFrame | None = None,
    ) -> None:
        self.strategy_config = strategy_config
        self.risk_config = risk_config
        self.initial_equity = initial_equity
        self.fee_pct = fee_pct
        self.slippage_pct = slippage_pct
        self.execution_model = execution_model
        self.random_seed = random_seed
        self.regime_config = regime_config
        self.signal_generator = SignalGenerator(strategy_config, regime_config)
        self.risk_engine = RiskEngine(risk_config)

        # Load symbol rules from file if provided, otherwise try default location
        self._symbol_rules: SymbolRulesSnapshot | None = None
        if symbol_rules_path is not None:
            self._symbol_rules = auto_load_rules(rules_path=symbol_rules_path)
        else:
            # Try to auto-load from default location (optional - will use fallback if not found)
            self._symbol_rules = auto_load_rules()

        # Fallback symbol filters (used only when no rules file is available)
        self._fallback_symbol_filters = SymbolFilters(
            min_notional=5.0,
            min_qty=0.001,
            step_size=0.001,
            tick_size=0.01,
        )
        self.exec_sim = ExecutionSimulator(random_seed=random_seed)

        # Spread configuration
        # Default max spread threshold from execution config (0.3% = 0.3)
        self._max_spread_pct = max_spread_pct if max_spread_pct is not None else 0.3

        # Initialize spread model/provider
        self._spread_model: SpreadModel | None = None
        self._spread_source: Literal["none", "model", "historical", "hybrid"] = "none"

        if spread_model is not None:
            # Use provided spread model directly
            self._spread_model = spread_model
            self._spread_source = "model"
        elif spread_data is not None:
            # Use hybrid provider with historical data + model fallback
            historical_provider = HistoricalSpreadProvider(spread_data)
            self._spread_model = HybridSpreadProvider(
                historical_provider=historical_provider,
                model=ConservativeSpreadModel(),
            )
            self._spread_source = "hybrid"
        elif execution_model == "realistic":
            # Default to conservative model for realistic execution
            self._spread_model = ConservativeSpreadModel()
            self._spread_source = "model"
        # If execution_model == "ideal" and no spread data, _spread_model stays None

    def get_symbol_filters(self, symbol: str) -> SymbolFilters:
        """Get symbol filters for a specific symbol.

        Loads from symbol rules snapshot if available, otherwise uses fallback defaults.
        Raises KeyError if symbol rules are loaded but the symbol is not found.
        """
        if self._symbol_rules is not None:
            # Fail fast if symbol not in rules
            return self._symbol_rules.get_filters(symbol)
        # Fallback to hardcoded defaults (for backwards compatibility)
        return self._fallback_symbol_filters

    def run(
        self,
        symbol: str,
        fourh: pd.DataFrame,
        funding_data: pd.DataFrame | None = None,
        constant_funding_rate: float | None = None,
    ) -> BacktestResult:
        equity = self.initial_equity
        peak_equity = equity
        drawdown = 0.0
        trades: list[Trade] = []
        equity_curve: list[EquityPoint] = []
        open_position: Position | None = None

        # Event-based funding tracking per position
        # Tracks last funding settlement time applied for each position
        last_funding_time: dict[str, datetime] = {}
        # Accumulated funding cashflows during position hold (for trade reporting)
        position_funding_accumulated: dict[str, float] = {}
        # Spread at entry for each position (for trade reporting)
        position_spread_at_entry: dict[str, float] = {}

        # Get symbol filters for this symbol (loaded from rules file or fallback)
        symbol_filters = self.get_symbol_filters(symbol)

        # Funding rate provider (event-based)
        funding_provider = FundingRateProvider(
            funding_data=funding_data,
            constant_rate=constant_funding_rate,
        )
        total_funding_paid = 0.0

        # Execution simulation metrics
        entry_signals_count = 0
        missed_entries = 0
        partial_fills = 0
        slippage_values_bps: list[float] = []

        # Spread tracking metrics
        spread_rejections = 0
        spreads_at_entry: list[float] = []

        # Track previous bar time for funding interval calculation
        prev_bar_time: datetime | None = None

        # Record initial equity point
        if not fourh.empty:
            first_idx = fourh.index[0]
            first_ts: datetime
            if isinstance(first_idx, pd.Timestamp):
                first_ts = first_idx.to_pydatetime()
            else:
                first_ts = first_idx
            if first_ts.tzinfo is None:
                first_ts = first_ts.replace(tzinfo=timezone.utc)
            equity_curve.append(
                EquityPoint(
                    timestamp=first_ts,
                    equity=equity,
                    drawdown=0.0,
                )
            )

        for timestamp in fourh.index:
            # Convert timestamp to datetime with UTC
            bar_time: datetime
            if isinstance(timestamp, pd.Timestamp):
                bar_time = timestamp.to_pydatetime()
            else:
                bar_time = timestamp
            if bar_time.tzinfo is None:
                bar_time = bar_time.replace(tzinfo=timezone.utc)

            # Compute daily data point-in-time (no lookahead bias)
            daily_slice = self._get_daily_at_time(fourh, timestamp)

            if daily_slice.empty:
                prev_bar_time = bar_time
                continue

            fourh_slice = fourh.loc[:timestamp].copy()
            if len(fourh_slice) < 30 or len(daily_slice) < 30:
                prev_bar_time = bar_time
                continue

            # === EVENT-BASED FUNDING APPLICATION ===
            # Apply funding settlements that occurred since last bar
            if open_position:
                # Determine start time for funding interval
                # Use the later of: position open time, last funding time, or previous bar time
                start_time = open_position.opened_at
                if start_time.tzinfo is None:
                    start_time = start_time.replace(tzinfo=timezone.utc)

                pos_symbol = open_position.symbol
                if pos_symbol in last_funding_time:
                    start_time = max(start_time, last_funding_time[pos_symbol])
                if prev_bar_time is not None:
                    start_time = max(start_time, prev_bar_time)

                # Get current bar for mark price fallback
                bar = fourh_slice.iloc[-1]

                # Apply all funding events in (start_time, bar_time]
                for funding_event in funding_provider.iter_funding_events(start_time, bar_time):
                    # Calculate notional at settlement time
                    # Prefer mark price from funding data, fallback to bar close
                    mark_price = funding_event.mark_price
                    if mark_price is None:
                        mark_price = float(bar["close"])

                    notional = open_position.quantity * mark_price

                    # Calculate funding cashflow with correct direction
                    # Cast position side to the expected type
                    pos_side: PositionSide = open_position.side
                    cashflow = funding_provider.calculate_funding_cashflow(
                        notional=notional,
                        funding_rate=funding_event.rate,
                        position_side=pos_side,
                    )

                    # Apply to equity (positive cashflow = pay, reduce equity)
                    equity -= cashflow
                    total_funding_paid += cashflow

                    # Track accumulated funding for this position (for trade reporting)
                    position_funding_accumulated[pos_symbol] = (
                        position_funding_accumulated.get(pos_symbol, 0.0) + cashflow
                    )

                    # Update last funding time applied
                    last_funding_time[pos_symbol] = funding_event.funding_time

                # Update peak equity and drawdown after funding
                if equity > peak_equity:
                    peak_equity = equity
                current_dd = (peak_equity - equity) / peak_equity if peak_equity > 0 else 0.0
                drawdown = max(drawdown, current_dd)

                # Check for stop-loss or take-profit exit
                exit_price = self._check_stop_take(open_position, bar)
                if exit_price:
                    # Get accumulated funding for this position
                    funding_cost = position_funding_accumulated.pop(pos_symbol, 0.0)
                    last_funding_time.pop(pos_symbol, None)
                    spread_at_entry = position_spread_at_entry.pop(pos_symbol, 0.0)

                    equity, trade = self._close_position(
                        symbol,
                        open_position,
                        exit_price,
                        bar_time,
                        equity,
                        funding_cost=funding_cost,
                        spread_at_entry_pct=spread_at_entry,
                    )
                    trades.append(trade)
                    open_position = None
                    peak_equity = max(peak_equity, equity)
                    current_dd = (peak_equity - equity) / peak_equity if peak_equity > 0 else 0.0
                    drawdown = max(drawdown, current_dd)
                    equity_curve.append(
                        EquityPoint(
                            timestamp=bar_time,
                            equity=equity,
                            drawdown=current_dd,
                        )
                    )
                    prev_bar_time = bar_time
                    continue

            # Generate trading signal
            signal = self.signal_generator.generate(
                symbol=symbol,
                daily_df=daily_slice,
                fourh_df=fourh_slice,
                funding_rate=0.0,
                news_risk="LOW",
                open_position=open_position,
                current_time=bar_time,
            )

            # Handle exit signal
            if signal.signal_type == SignalType.EXIT and open_position:
                pos_symbol = open_position.symbol
                # Get accumulated funding for this position
                funding_cost = position_funding_accumulated.pop(pos_symbol, 0.0)
                last_funding_time.pop(pos_symbol, None)
                spread_at_entry = position_spread_at_entry.pop(pos_symbol, 0.0)

                equity, trade = self._close_position(
                    symbol,
                    open_position,
                    signal.price,
                    bar_time,
                    equity,
                    funding_cost=funding_cost,
                    spread_at_entry_pct=spread_at_entry,
                )
                trades.append(trade)
                open_position = None
                peak_equity = max(peak_equity, equity)
                current_dd = (peak_equity - equity) / peak_equity if peak_equity > 0 else 0.0
                drawdown = max(drawdown, current_dd)
                equity_curve.append(
                    EquityPoint(
                        timestamp=bar_time,
                        equity=equity,
                        drawdown=current_dd,
                    )
                )
                prev_bar_time = bar_time
                continue

            # Handle entry signals
            if signal.signal_type in {SignalType.LONG, SignalType.SHORT}:
                proposal = TradeProposal(
                    symbol=symbol,
                    side="LONG" if signal.signal_type == SignalType.LONG else "SHORT",
                    entry_price=signal.entry_price or signal.price,
                    stop_price=signal.stop_price or signal.price,
                    take_profit=signal.take_profit,
                    atr=signal.atr,
                    leverage=self.risk_config.default_leverage,
                    score=signal.score,
                    funding_rate=0.0,
                    news_risk="LOW",
                    trade_id=signal.trade_id or "",
                    created_at=bar_time,
                    is_entry=True,
                )
                risk = self.risk_engine.evaluate(
                    state=self._mock_state(equity),
                    proposal=proposal,
                    symbol_filters=symbol_filters,
                    now=bar_time,
                )
                if risk.approved:
                    entry_signals_count += 1

                    if self.execution_model == "realistic":
                        # Use realistic execution simulation
                        bar = fourh_slice.iloc[-1]
                        current_price = bar["close"]
                        atr_pct = proposal.atr / current_price if current_price > 0 else 0.0

                        # Get spread for this bar
                        spread_pct: float | None = None
                        spread_at_entry: float = 0.0
                        if self._spread_model is not None:
                            spread_snapshot = self._spread_model.get_spread(
                                symbol=symbol,
                                timestamp=bar_time,
                                price=current_price,
                                atr=proposal.atr,
                            )
                            spread_pct = spread_snapshot.spread_pct
                            spread_at_entry = spread_pct

                            # Gate entry if spread exceeds threshold
                            if spread_pct > self._max_spread_pct:
                                spread_rejections += 1
                                prev_bar_time = bar_time
                                continue

                        # Convert proposal side to order side for fill_order
                        order_side: Literal["BUY", "SELL"] = (
                            "BUY" if proposal.side == "LONG" else "SELL"
                        )

                        # Simulate order execution
                        filled, fill_price, is_partial = self.exec_sim.fill_order(
                            proposal_price=proposal.entry_price,
                            current_price=current_price,
                            order_type="LIMIT",
                            atr=proposal.atr,
                            atr_pct=atr_pct,
                            side=order_side,
                            spread_pct=spread_pct,
                        )

                        if not filled:
                            missed_entries += 1
                            prev_bar_time = bar_time
                            continue

                        if is_partial:
                            partial_fills += 1

                        # Track spread at entry
                        spreads_at_entry.append(spread_at_entry)

                        # Calculate slippage in bps
                        if proposal.side == "LONG":
                            slippage_bps = (
                                (fill_price - proposal.entry_price) / proposal.entry_price
                            ) * 10000
                        else:
                            slippage_bps = (
                                (proposal.entry_price - fill_price) / proposal.entry_price
                            ) * 10000
                        slippage_values_bps.append(abs(slippage_bps))

                        sizing = self.risk_engine.sizer.calculate_size(
                            equity=equity,
                            entry_price=fill_price,
                            stop_price=proposal.stop_price,
                            symbol_filters=symbol_filters,
                            leverage=proposal.leverage,
                        )
                        if sizing:
                            quantity = sizing.quantity * 0.5 if is_partial else sizing.quantity
                            open_position = Position(
                                symbol=symbol,
                                side=proposal.side,
                                quantity=quantity,
                                entry_price=fill_price,
                                leverage=proposal.leverage,
                                opened_at=bar_time,
                                stop_price=proposal.stop_price,
                                take_profit=proposal.take_profit,
                                trade_id=proposal.trade_id,
                            )
                            # Initialize funding tracking for new position
                            position_funding_accumulated[symbol] = 0.0
                            # Store spread at entry for trade reporting
                            position_spread_at_entry[symbol] = spread_at_entry
                    else:
                        # Use ideal execution (fixed slippage)
                        entry_price = proposal.entry_price * (1 + self.slippage_pct)
                        if proposal.side == "SHORT":
                            entry_price = proposal.entry_price * (1 - self.slippage_pct)
                        slippage_values_bps.append(self.slippage_pct * 10000)

                        sizing = self.risk_engine.sizer.calculate_size(
                            equity=equity,
                            entry_price=entry_price,
                            stop_price=proposal.stop_price,
                            symbol_filters=symbol_filters,
                            leverage=proposal.leverage,
                        )
                        if sizing:
                            open_position = Position(
                                symbol=symbol,
                                side=proposal.side,
                                quantity=sizing.quantity,
                                entry_price=entry_price,
                                leverage=proposal.leverage,
                                opened_at=bar_time,
                                stop_price=proposal.stop_price,
                                take_profit=proposal.take_profit,
                                trade_id=proposal.trade_id,
                            )
                            # Initialize funding tracking for new position
                            position_funding_accumulated[symbol] = 0.0

            prev_bar_time = bar_time

        total_return = (equity - self.initial_equity) / self.initial_equity
        win_rate = self._win_rate(trades)

        # Record final equity point
        if not fourh.empty:
            last_idx = fourh.index[-1]
            last_ts: datetime
            if isinstance(last_idx, pd.Timestamp):
                last_ts = last_idx.to_pydatetime()
            else:
                last_ts = last_idx
            if last_ts.tzinfo is None:
                last_ts = last_ts.replace(tzinfo=timezone.utc)
            final_dd = (peak_equity - equity) / peak_equity if peak_equity > 0 else 0.0
            equity_curve.append(
                EquityPoint(
                    timestamp=last_ts,
                    equity=equity,
                    drawdown=final_dd,
                )
            )

        # Calculate execution metrics
        if entry_signals_count > 0:
            fill_rate = (entry_signals_count - missed_entries) / entry_signals_count
        else:
            fill_rate = 1.0
        avg_slippage_bps = (
            sum(slippage_values_bps) / len(slippage_values_bps) if slippage_values_bps else 0.0
        )

        # Calculate spread metrics
        avg_spread_at_entry_pct = (
            sum(spreads_at_entry) / len(spreads_at_entry) if spreads_at_entry else 0.0
        )

        return BacktestResult(
            trades=trades,
            equity_curve=equity_curve,
            total_return=total_return,
            win_rate=win_rate,
            max_drawdown=drawdown,
            total_trades=len(trades),
            final_equity=equity,
            initial_equity=self.initial_equity,
            fill_rate=fill_rate,
            avg_slippage_bps=avg_slippage_bps,
            missed_entries=missed_entries,
            partial_fills=partial_fills,
            total_funding_paid=total_funding_paid,
            pnl_with_funding=equity - self.initial_equity,
            spread_rejections=spread_rejections,
            avg_spread_at_entry_pct=avg_spread_at_entry_pct,
            spread_source=self._spread_source,
        )

    def _resample_daily(self, fourh: pd.DataFrame) -> pd.DataFrame:
        daily = fourh.resample("1D").agg(
            {
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
            }
        )
        return daily.dropna()

    def _get_daily_at_time(self, fourh: pd.DataFrame, current_time: pd.Timestamp) -> pd.DataFrame:
        """Resample 4h to daily using only data available at current_time (no lookahead).

        At 4h candle close time `t`, use daily candles with close time < t,
        OR <= t only when t is a daily close (00:00 UTC).

        This prevents lookahead bias by ensuring daily features at time T
        never depend on 4h bars after T.
        """
        is_daily_close = current_time.hour == 0 and current_time.minute == 0

        if is_daily_close:
            # Include current bar if it closes the daily candle
            fourh_subset = fourh.loc[:current_time]
        else:
            # Use only 4h bars up to the most recent daily close (00:00 UTC today)
            # The bar at 00:00 belongs to the previous day's trading and should be included
            cutoff = current_time.normalize()  # Start of current day (00:00 UTC)
            if cutoff <= fourh.index.min():
                # Not enough data for any complete daily bar
                return pd.DataFrame()
            fourh_subset = fourh.loc[:cutoff]

        if fourh_subset.empty:
            return pd.DataFrame()

        # Shift index by 1 second before resampling so that bars at 00:00 UTC
        # (which represent the previous day's final trading) are grouped
        # with the previous day, not the new day.
        shifted = fourh_subset.copy()
        shifted.index = shifted.index - pd.Timedelta(seconds=1)

        daily = shifted.resample("1D").agg(
            {
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
            }
        )
        daily = daily.dropna()

        # Shift index to represent the daily close time (00:00 UTC of next day)
        daily.index = daily.index + pd.Timedelta(days=1)
        return daily

    def _check_stop_take(self, position: Position, bar: pd.Series) -> float | None:
        if position.stop_price is not None:
            if position.side == "LONG" and bar["low"] <= position.stop_price:
                return float(position.stop_price)
            if position.side == "SHORT" and bar["high"] >= position.stop_price:
                return float(position.stop_price)
        if position.take_profit is not None:
            if position.side == "LONG" and bar["high"] >= position.take_profit:
                return float(position.take_profit)
            if position.side == "SHORT" and bar["low"] <= position.take_profit:
                return float(position.take_profit)
        return None

    def _close_position(
        self,
        symbol: str,
        position: Position,
        exit_price: float,
        exit_time: datetime,
        equity: float,
        slippage_bps: float = 0.0,
        is_partial_fill: bool = False,
        funding_cost: float = 0.0,
        spread_at_entry_pct: float = 0.0,
    ) -> tuple[float, Trade]:
        """Close a position and calculate P&L.

        Note on funding accounting:
        - Funding cashflows have already been applied to equity at settlement times
        - `funding_cost` here is the accumulated sum for reporting purposes
        - We only add (gross_pnl - fees) to equity, NOT net_pnl (which includes funding)
        - Trade.net_pnl includes funding for accurate trade-level reporting
        """
        gross_pnl = (exit_price - position.entry_price) * position.quantity
        if position.side == "SHORT":
            gross_pnl = -gross_pnl
        fees = abs(position.entry_price * position.quantity) * self.fee_pct
        fees += abs(exit_price * position.quantity) * self.fee_pct

        # net_pnl includes funding for trade-level reporting
        net_pnl = gross_pnl - fees - funding_cost

        # Equity update: only add gross_pnl - fees
        # (funding was already applied at settlement times)
        equity += gross_pnl - fees

        holding_hours = (exit_time - position.opened_at).total_seconds() / 3600
        trade = Trade(
            trade_id=position.trade_id or "",
            symbol=symbol,
            direction=position.side,
            entry_price=position.entry_price,
            exit_price=exit_price,
            quantity=position.quantity,
            entry_time=position.opened_at,
            exit_time=exit_time,
            gross_pnl=gross_pnl,
            net_pnl=net_pnl,
            holding_hours=holding_hours,
            slippage_bps=slippage_bps,
            is_partial_fill=is_partial_fill,
            funding_cost=funding_cost,
            spread_at_entry_pct=spread_at_entry_pct,
        )
        return equity, trade

    @staticmethod
    def _win_rate(trades: list[Trade]) -> float:
        if not trades:
            return 0.0
        wins = len([t for t in trades if t.net_pnl > 0])
        return wins / len(trades)

    def _mock_state(self, equity: float) -> TradingState:
        state = TradingState(equity=equity, peak_equity=equity)
        return state
