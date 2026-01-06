"""Backtesting engine for the trend-following strategy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from src.backtester.execution_sim import ExecutionSimulator
from src.backtester.funding import FundingRateProvider
from src.config.settings import RegimeConfig, RiskConfig, StrategyConfig
from src.ledger.state import Position, TradingState
from src.models import TradeProposal
from src.risk.engine import RiskEngine
from src.risk.sizing import SymbolFilters
from src.strategy.signals import SignalGenerator, SignalType


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
    net_pnl: float
    holding_hours: float
    slippage_bps: float = 0.0
    is_partial_fill: bool = False
    funding_cost: float = 0.0  # Cumulative funding paid during hold


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
    total_funding_paid: float = 0.0  # Sum of all funding costs across trades
    pnl_with_funding: float = 0.0  # net_pnl - total_funding_paid


class Backtester:
    """Backtest the trend-following strategy on historical data."""

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
        self.symbol_filters = SymbolFilters(
            min_notional=5.0,
            min_qty=0.001,
            step_size=0.001,
            tick_size=0.01,
        )
        self.exec_sim = ExecutionSimulator(random_seed=random_seed)

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
        position_funding_accrued: dict[str, float] = {}  # track funding per position

        # Funding rate provider
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

        # Record initial equity point
        if not fourh.empty:
            equity_curve.append(
                EquityPoint(
                    timestamp=fourh.index[0].to_pydatetime(),
                    equity=equity,
                    drawdown=0.0,
                )
            )

        for timestamp in fourh.index:
            # Compute daily data point-in-time (no lookahead bias)
            daily_slice = self._get_daily_at_time(fourh, timestamp)

            if daily_slice.empty:
                continue

            fourh_slice = fourh.loc[:timestamp].copy()
            if len(fourh_slice) < 30 or len(daily_slice) < 30:
                continue

            if open_position:
                # Track funding accrual for open positions
                funding_info = funding_provider.get_rate(timestamp.to_pydatetime())
                hrs_since_entry = (
                    timestamp.to_pydatetime() - open_position.opened_at
                ).total_seconds() / 3600
                position_notional = open_position.entry_price * open_position.quantity
                funding_cost = funding_provider.calculate_funding_cost(
                    position_notional=position_notional,
                    leverage=open_position.leverage,
                    funding_rate=funding_info.rate,
                    hours_held=hrs_since_entry,
                )

                # Deduct funding from equity if positive (paying funding)
                # If funding is negative (receiving funding), add to equity
                if funding_cost != 0:
                    equity -= funding_cost
                    total_funding_paid += funding_cost
                    position_funding_accrued[open_position.symbol] = (
                        position_funding_accrued.get(open_position.symbol, 0.0) + funding_cost
                    )

                bar = fourh_slice.iloc[-1]
                exit_price = self._check_stop_take(open_position, bar)
                if exit_price:
                    # Calculate any remaining funding from last close to exit
                    funding_info = funding_provider.get_rate(timestamp.to_pydatetime())
                    position_notional = open_position.entry_price * open_position.quantity
                    funding_cost = funding_provider.calculate_funding_cost(
                        position_notional=position_notional,
                        leverage=open_position.leverage,
                        funding_rate=funding_info.rate,
                        hours_held=hrs_since_entry,
                    )
                    total_funding_paid += funding_cost

                    equity, trade = self._close_position(
                        symbol,
                        open_position,
                        exit_price,
                        timestamp,
                        equity,
                        funding_cost=funding_cost,
                    )
                    trades.append(trade)
                    open_position = None
                    peak_equity = max(peak_equity, equity)
                    current_dd = (peak_equity - equity) / peak_equity if peak_equity > 0 else 0.0
                    drawdown = max(drawdown, current_dd)
                    equity_curve.append(
                        EquityPoint(
                            timestamp=timestamp.to_pydatetime(),
                            equity=equity,
                            drawdown=current_dd,
                        )
                    )
                    continue

            signal = self.signal_generator.generate(
                symbol=symbol,
                daily_df=daily_slice,
                fourh_df=fourh_slice,
                funding_rate=0.0,
                news_risk="LOW",
                open_position=open_position,
                current_time=timestamp.to_pydatetime(),
            )

            if signal.signal_type == SignalType.EXIT and open_position:
                # Calculate funding cost up to exit
                hrs_since_entry = (
                    timestamp.to_pydatetime() - open_position.opened_at
                ).total_seconds() / 3600
                position_notional = open_position.entry_price * open_position.quantity
                funding_info = funding_provider.get_rate(timestamp.to_pydatetime())
                funding_cost = funding_provider.calculate_funding_cost(
                    position_notional=position_notional,
                    leverage=open_position.leverage,
                    funding_rate=funding_info.rate,
                    hours_held=hrs_since_entry,
                )
                total_funding_paid += funding_cost

                equity, trade = self._close_position(
                    symbol,
                    open_position,
                    signal.price,
                    timestamp,
                    equity,
                    funding_cost=funding_cost,
                )
                trades.append(trade)
                open_position = None
                peak_equity = max(peak_equity, equity)
                current_dd = (peak_equity - equity) / peak_equity if peak_equity > 0 else 0.0
                drawdown = max(drawdown, current_dd)
                equity_curve.append(
                    EquityPoint(
                        timestamp=timestamp.to_pydatetime(),
                        equity=equity,
                        drawdown=current_dd,
                    )
                )
                continue

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
                    created_at=timestamp.to_pydatetime(),
                    is_entry=True,
                )
                risk = self.risk_engine.evaluate(
                    state=self._mock_state(equity),
                    proposal=proposal,
                    symbol_filters=self.symbol_filters,
                    now=timestamp.to_pydatetime(),
                )
                if risk.approved:
                    entry_signals_count += 1

                    if self.execution_model == "realistic":
                        # Use realistic execution simulation
                        bar = fourh_slice.iloc[-1]
                        current_price = bar["close"]
                        atr_pct = proposal.atr / current_price if current_price > 0 else 0.0

                        # Simulate order execution
                        filled, fill_price, is_partial = self.exec_sim.fill_order(
                            proposal_price=proposal.entry_price,
                            current_price=current_price,
                            order_type="LIMIT",
                            atr=proposal.atr,
                            atr_pct=atr_pct,
                        )

                        if not filled:
                            missed_entries += 1
                            continue

                        if is_partial:
                            partial_fills += 1

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
                            symbol_filters=self.symbol_filters,
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
                                opened_at=timestamp.to_pydatetime(),
                                stop_price=proposal.stop_price,
                                take_profit=proposal.take_profit,
                                trade_id=proposal.trade_id,
                            )
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
                            symbol_filters=self.symbol_filters,
                            leverage=proposal.leverage,
                        )
                        if sizing:
                            open_position = Position(
                                symbol=symbol,
                                side=proposal.side,
                                quantity=sizing.quantity,
                                entry_price=entry_price,
                                leverage=proposal.leverage,
                                opened_at=timestamp.to_pydatetime(),
                                stop_price=proposal.stop_price,
                                take_profit=proposal.take_profit,
                                trade_id=proposal.trade_id,
                            )

        total_return = (equity - self.initial_equity) / self.initial_equity
        win_rate = self._win_rate(trades)

        # Record final equity point
        if not fourh.empty:
            final_dd = (peak_equity - equity) / peak_equity if peak_equity > 0 else 0.0
            equity_curve.append(
                EquityPoint(
                    timestamp=fourh.index[-1].to_pydatetime(),
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
        timestamp: pd.Timestamp,
        equity: float,
        slippage_bps: float = 0.0,
        is_partial_fill: bool = False,
        funding_cost: float = 0.0,
    ) -> tuple[float, Trade]:
        gross_pnl = (exit_price - position.entry_price) * position.quantity
        if position.side == "SHORT":
            gross_pnl = -gross_pnl
        fees = abs(position.entry_price * position.quantity) * self.fee_pct
        fees += abs(exit_price * position.quantity) * self.fee_pct
        net_pnl = gross_pnl - fees - funding_cost
        equity += net_pnl
        holding_hours = (timestamp.to_pydatetime() - position.opened_at).total_seconds() / 3600
        trade = Trade(
            trade_id=position.trade_id or "",
            symbol=symbol,
            direction=position.side,
            entry_price=position.entry_price,
            exit_price=exit_price,
            quantity=position.quantity,
            entry_time=position.opened_at,
            exit_time=timestamp.to_pydatetime(),
            gross_pnl=gross_pnl,
            net_pnl=net_pnl,
            holding_hours=holding_hours,
            slippage_bps=slippage_bps,
            is_partial_fill=is_partial_fill,
            funding_cost=funding_cost,
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
