"""Event-driven multi-symbol backtesting engine.

Replays historical data through an event loop, processing bars and funding
events in deterministic order across symbols. Supports cross-sectional
portfolio selection for multi-asset strategies.

Key features:
- Deterministic replay via EventMux heap-merge
- Cross-sectional signal ranking at each timestamp
- Pluggable execution models (ideal/realistic)
- Optional event ledger for analysis
- Backward-compatible with single-symbol runs
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import pandas as pd

from src.backtester.backtest_ledger import BacktestLedger, NullLedger
from src.backtester.data_reader import DatasetReader
from src.backtester.engine import EquityPoint, Trade
from src.backtester.event_mux import (
    EventMux,
    get_bars_by_symbol,
    group_events_by_timestamp,
    separate_events_by_type,
)
from src.backtester.events import BarEvent, FundingEvent
from src.backtester.execution_model import ExecutionModel, create_execution_model
from src.backtester.strategy_runner import MultiSymbolRunner, StrategyContext, TrendFollowingRunner
from src.backtester.symbol_rules import SymbolRulesSnapshot, auto_load_rules
from src.config.settings import RegimeConfig, RiskConfig, StrategyConfig
from src.ledger.state import Position, TradingState
from src.models import TradeProposal
from src.risk.engine import RiskEngine
from src.risk.sizing import SymbolFilters
from src.strategy.portfolio import PortfolioSelector, TradeCandidate
from src.strategy.signals import SignalType

if TYPE_CHECKING:
    from src.backtester.strategy_runner import StrategyRunner
    from src.strategy.signals import Signal


@dataclass
class SymbolState:
    """Per-symbol state during backtest."""

    symbol: str
    fourh_history: list[BarEvent] = field(default_factory=list)
    position: Position | None = None
    last_funding_time: datetime | None = None
    funding_accumulated: float = 0.0  # For current position

    def add_bar(self, bar: BarEvent) -> None:
        """Add a bar to history."""
        self.fourh_history.append(bar)

    def get_history_df(self, lookback: int = 200) -> pd.DataFrame:
        """Convert bar history to DataFrame for strategy.

        Args:
            lookback: Number of bars to include

        Returns:
            DataFrame with OHLCV indexed by close_time
        """
        bars = (
            self.fourh_history[-lookback:]
            if len(self.fourh_history) > lookback
            else self.fourh_history
        )

        if not bars:
            return pd.DataFrame()

        data = {
            "open": [b.open for b in bars],
            "high": [b.high for b in bars],
            "low": [b.low for b in bars],
            "close": [b.close for b in bars],
            "volume": [b.volume for b in bars],
        }
        index = pd.DatetimeIndex([b.timestamp for b in bars], tz=timezone.utc)
        df = pd.DataFrame(data, index=index)
        df.index.name = "close_time"

        # Add open_time column if available
        if bars[0].open_time is not None:
            df["open_time"] = [b.open_time for b in bars]

        return df


@dataclass
class MultiSymbolResult:
    """Results from a multi-symbol backtest run."""

    trades: list[Trade]
    equity_curve: list[EquityPoint]
    total_return: float
    win_rate: float
    max_drawdown: float
    total_trades: int
    final_equity: float
    initial_equity: float
    # Per-symbol breakdown
    trades_by_symbol: dict[str, list[Trade]] = field(default_factory=dict)
    symbols_traded: list[str] = field(default_factory=list)
    # Execution metrics
    fill_rate: float = 1.0
    avg_slippage_bps: float = 0.0
    missed_entries: int = 0
    partial_fills: int = 0
    # Funding metrics
    total_funding_paid: float = 0.0
    # Event counts
    bars_processed: int = 0
    funding_events_processed: int = 0


class EventDrivenBacktester:
    """Multi-symbol event-driven backtesting engine.

    Replays historical events in deterministic order, processing:
    1. Funding settlements (affects equity)
    2. Bar closes (trigger stop/take-profit checks, generate signals)
    3. Cross-sectional portfolio selection
    4. Trade execution

    Example:
        engine = EventDrivenBacktester(
            strategy_config=strategy_config,
            risk_config=risk_config,
            symbols=["BTCUSDT", "ETHUSDT"],
            data_path="./data/market",
        )
        result = engine.run()
    """

    def __init__(
        self,
        strategy_config: StrategyConfig,
        risk_config: RiskConfig,
        symbols: list[str],
        data_path: str | Path,
        interval: str = "4h",
        initial_equity: float = 100.0,
        fee_pct: float = 0.0006,
        execution_model: str = "ideal",
        slippage_pct: float = 0.0005,
        random_seed: int | None = None,
        regime_config: RegimeConfig | None = None,
        symbol_rules_path: Path | str | None = None,
        out_dir: Path | str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        warmup_bars: int = 30,
    ) -> None:
        """Initialize the backtester.

        Args:
            strategy_config: Strategy parameters
            risk_config: Risk management parameters
            symbols: List of symbols to trade
            data_path: Directory containing market data CSVs
            interval: Candle interval (default: 4h)
            initial_equity: Starting equity
            fee_pct: Trading fee percentage
            execution_model: "ideal" or "realistic"
            slippage_pct: Fixed slippage for ideal execution
            random_seed: Seed for reproducible realistic execution
            regime_config: Optional regime classification config
            symbol_rules_path: Path to symbol rules file
            out_dir: Directory for backtest output (events.jsonl)
            start_time: Optional backtest start filter
            end_time: Optional backtest end filter
            warmup_bars: Bars needed before trading (for indicators)
        """
        self.strategy_config = strategy_config
        self.risk_config = risk_config
        self.symbols = symbols
        self.data_path = Path(data_path)
        self.interval = interval
        self.initial_equity = initial_equity
        self.fee_pct = fee_pct
        self.warmup_bars = warmup_bars

        # Create execution model
        self.exec_model: ExecutionModel = create_execution_model(
            model_type=execution_model,
            slippage_pct=slippage_pct,
            random_seed=random_seed,
        )

        # Create strategy runner
        self.strategy_runner: StrategyRunner = TrendFollowingRunner(
            strategy_config=strategy_config,
            regime_config=regime_config,
        )
        self.multi_runner = MultiSymbolRunner(self.strategy_runner)

        # Risk engine and portfolio selector
        self.risk_engine = RiskEngine(risk_config)
        self.portfolio_selector = PortfolioSelector(max_positions=risk_config.max_positions)

        # Symbol rules
        self._symbol_rules: SymbolRulesSnapshot | None = None
        if symbol_rules_path is not None:
            self._symbol_rules = auto_load_rules(rules_path=symbol_rules_path)
        else:
            self._symbol_rules = auto_load_rules()

        self._fallback_filters = SymbolFilters(
            min_notional=5.0,
            min_qty=0.001,
            step_size=0.001,
            tick_size=0.01,
        )

        # Output directory for ledger
        self.out_dir = Path(out_dir) if out_dir else None
        self.start_time = start_time
        self.end_time = end_time

    def get_symbol_filters(self, symbol: str) -> SymbolFilters:
        """Get trading filters for a symbol."""
        if self._symbol_rules is not None:
            try:
                return self._symbol_rules.get_filters(symbol)
            except KeyError:
                pass
        return self._fallback_filters

    def run(self) -> MultiSymbolResult:
        """Run the multi-symbol backtest.

        Returns:
            MultiSymbolResult with trades, equity curve, and metrics
        """
        # Load data
        reader = DatasetReader(
            data_path=self.data_path,
            symbols=self.symbols,
            interval=self.interval,
            start_time=self.start_time,
            end_time=self.end_time,
        )

        if not reader.loaded_symbols:
            raise ValueError(f"No data found for symbols: {self.symbols}")

        # Create event multiplexer
        mux = EventMux(
            bar_iterators=reader.bar_iterators(),
            funding_iterators=reader.funding_iterators(),
        )

        # Initialize ledger
        ledger: BacktestLedger | NullLedger
        if self.out_dir:
            ledger = BacktestLedger(self.out_dir)
        else:
            ledger = NullLedger()

        # Run the replay loop
        with ledger:
            result = self._replay_loop(mux, reader.loaded_symbols, ledger)

        return result

    def _replay_loop(
        self,
        mux: EventMux,
        symbols: list[str],
        ledger: BacktestLedger | NullLedger,
    ) -> MultiSymbolResult:
        """Main event replay loop."""
        # State tracking
        equity = self.initial_equity
        peak_equity = equity
        max_drawdown = 0.0
        trades: list[Trade] = []
        trades_by_symbol: dict[str, list[Trade]] = {s: [] for s in symbols}
        equity_curve: list[EquityPoint] = []

        # Per-symbol state
        symbol_states: dict[str, SymbolState] = {s: SymbolState(symbol=s) for s in symbols}

        # Metrics
        entry_signals_count = 0
        missed_entries = 0
        partial_fills = 0
        slippage_values: list[float] = []
        total_funding_paid = 0.0
        bars_processed = 0
        funding_processed = 0

        # Process events grouped by timestamp
        for timestamp, events in group_events_by_timestamp(mux):
            # Separate by type
            funding_events, bar_events, other_events = separate_events_by_type(events)

            # 1. Process funding events first
            for fe in funding_events:
                funding_processed += 1
                state = symbol_states.get(fe.symbol)
                if state and state.position:
                    # Apply funding settlement
                    cashflow = self._apply_funding(state, fe, equity)
                    equity -= cashflow
                    total_funding_paid += cashflow

            # 2. Process bar events
            bars_by_symbol = get_bars_by_symbol(bar_events)

            for symbol, bar in bars_by_symbol.items():
                bars_processed += 1
                state = symbol_states[symbol]
                state.add_bar(bar)
                ledger.append_backtest_event(bar)

                # Check stop/take-profit on open positions
                if state.position:
                    exit_price = self._check_exit_triggers(state.position, bar)
                    if exit_price:
                        # Close position
                        equity, trade = self._close_position(
                            state, exit_price, bar.timestamp, equity
                        )
                        trades.append(trade)
                        trades_by_symbol[symbol].append(trade)

                        # Update drawdown
                        if equity > peak_equity:
                            peak_equity = equity
                        dd = (peak_equity - equity) / peak_equity if peak_equity > 0 else 0.0
                        max_drawdown = max(max_drawdown, dd)

                        equity_curve.append(
                            EquityPoint(
                                timestamp=bar.timestamp,
                                equity=equity,
                                drawdown=dd,
                            )
                        )

            # 3. Generate signals for all symbols with enough data
            contexts = self._build_contexts(symbol_states, bars_by_symbol, timestamp)

            if not contexts:
                continue

            signal_batch = self.multi_runner.generate_signals(contexts)

            # 4. Handle exit signals first
            for symbol, signal in signal_batch.get_exit_signals().items():
                state = symbol_states[symbol]
                if state.position:
                    equity, trade = self._close_position(state, signal.price, timestamp, equity)
                    trades.append(trade)
                    trades_by_symbol[symbol].append(trade)

                    if equity > peak_equity:
                        peak_equity = equity
                    dd = (peak_equity - equity) / peak_equity if peak_equity > 0 else 0.0
                    max_drawdown = max(max_drawdown, dd)

                    equity_curve.append(
                        EquityPoint(
                            timestamp=timestamp,
                            equity=equity,
                            drawdown=dd,
                        )
                    )

            # 5. Process entry signals through risk and portfolio selection
            entry_signals = signal_batch.get_entry_signals()
            if entry_signals:
                candidates = self._create_candidates(
                    entry_signals, symbol_states, equity, timestamp
                )

                # Get current positions
                current_positions = {
                    s: st.position for s, st in symbol_states.items() if st.position
                }

                # Select best trades cross-sectionally
                trading_state = TradingState(equity=equity, peak_equity=peak_equity)
                selected = self.portfolio_selector.select(
                    candidates=candidates,
                    current_positions=current_positions,
                    blocked_symbols=set(),  # No news blocking in backtest
                    state=trading_state,
                )

                # 6. Execute selected trades
                for candidate in selected:
                    entry_signals_count += 1
                    state = symbol_states[candidate.symbol]
                    bar = bars_by_symbol.get(candidate.symbol)

                    if not bar:
                        continue

                    # Simulate execution
                    sizing = self.risk_engine.sizer.calculate_size(
                        equity=equity,
                        entry_price=candidate.proposal.entry_price,
                        stop_price=candidate.proposal.stop_price,
                        symbol_filters=self.get_symbol_filters(candidate.symbol),
                        leverage=candidate.proposal.leverage,
                    )

                    if not sizing:
                        missed_entries += 1
                        continue

                    fill_result = self.exec_model.simulate_fill(
                        proposal=candidate.proposal,
                        current_price=bar.close,
                        atr=candidate.proposal.atr,
                        requested_quantity=sizing.quantity,
                    )

                    if not fill_result.filled:
                        missed_entries += 1
                        continue

                    if fill_result.is_partial:
                        partial_fills += 1

                    slippage_values.append(fill_result.slippage_bps)

                    # Open position
                    state.position = Position(
                        symbol=candidate.symbol,
                        side=candidate.proposal.side,
                        quantity=fill_result.fill_quantity or sizing.quantity,
                        entry_price=fill_result.fill_price or candidate.proposal.entry_price,
                        leverage=candidate.proposal.leverage,
                        opened_at=timestamp,
                        stop_price=candidate.proposal.stop_price,
                        take_profit=candidate.proposal.take_profit,
                        trade_id=candidate.proposal.trade_id,
                    )
                    state.funding_accumulated = 0.0

            # Record equity periodically (every 10 bars)
            if bars_processed % 10 == 0:
                dd = (peak_equity - equity) / peak_equity if peak_equity > 0 else 0.0
                equity_curve.append(
                    EquityPoint(
                        timestamp=timestamp,
                        equity=equity,
                        drawdown=dd,
                    )
                )

        # Calculate final metrics
        total_return = (equity - self.initial_equity) / self.initial_equity
        win_rate = self._calculate_win_rate(trades)
        fill_rate = (
            (entry_signals_count - missed_entries) / entry_signals_count
            if entry_signals_count > 0
            else 1.0
        )
        avg_slippage = sum(slippage_values) / len(slippage_values) if slippage_values else 0.0

        # Record final equity point
        if equity_curve:
            final_dd = (peak_equity - equity) / peak_equity if peak_equity > 0 else 0.0
            if equity_curve[-1].equity != equity:
                equity_curve.append(
                    EquityPoint(
                        timestamp=equity_curve[-1].timestamp,
                        equity=equity,
                        drawdown=final_dd,
                    )
                )

        return MultiSymbolResult(
            trades=trades,
            equity_curve=equity_curve,
            total_return=total_return,
            win_rate=win_rate,
            max_drawdown=max_drawdown,
            total_trades=len(trades),
            final_equity=equity,
            initial_equity=self.initial_equity,
            trades_by_symbol=trades_by_symbol,
            symbols_traded=[s for s, t in trades_by_symbol.items() if t],
            fill_rate=fill_rate,
            avg_slippage_bps=avg_slippage,
            missed_entries=missed_entries,
            partial_fills=partial_fills,
            total_funding_paid=total_funding_paid,
            bars_processed=bars_processed,
            funding_events_processed=funding_processed,
        )

    def _apply_funding(
        self,
        state: SymbolState,
        funding_event: FundingEvent,
        equity: float,
    ) -> float:
        """Apply funding settlement to position.

        Returns cashflow (positive = pay, reduce equity).
        """
        if not state.position:
            return 0.0

        # Use mark price from event or estimate from position entry
        mark_price = funding_event.mark_price or state.position.entry_price
        notional = state.position.quantity * mark_price

        # Calculate cashflow with correct direction
        # Long pays positive funding, receives negative
        # Short receives positive funding, pays negative
        if state.position.side == "LONG":
            cashflow = notional * funding_event.rate
        else:
            cashflow = -notional * funding_event.rate

        state.funding_accumulated += cashflow
        state.last_funding_time = funding_event.funding_time

        return cashflow

    def _check_exit_triggers(self, position: Position, bar: BarEvent) -> float | None:
        """Check if stop-loss or take-profit was hit.

        Returns exit price if triggered, None otherwise.
        """
        if position.stop_price is not None:
            if position.side == "LONG" and bar.low <= position.stop_price:
                return position.stop_price
            if position.side == "SHORT" and bar.high >= position.stop_price:
                return position.stop_price

        if position.take_profit is not None:
            if position.side == "LONG" and bar.high >= position.take_profit:
                return position.take_profit
            if position.side == "SHORT" and bar.low <= position.take_profit:
                return position.take_profit

        return None

    def _close_position(
        self,
        state: SymbolState,
        exit_price: float,
        exit_time: datetime,
        equity: float,
    ) -> tuple[float, Trade]:
        """Close position and return updated equity and trade record."""
        position = state.position
        if not position:
            raise ValueError("No position to close")

        # Calculate P&L
        gross_pnl = (exit_price - position.entry_price) * position.quantity
        if position.side == "SHORT":
            gross_pnl = -gross_pnl

        fees = abs(position.entry_price * position.quantity) * self.fee_pct
        fees += abs(exit_price * position.quantity) * self.fee_pct

        # Net P&L includes fees and funding (for reporting)
        net_pnl = gross_pnl - fees - state.funding_accumulated

        # Equity only gets gross_pnl - fees (funding was already applied)
        equity += gross_pnl - fees

        holding_hours = (exit_time - position.opened_at).total_seconds() / 3600

        trade = Trade(
            trade_id=position.trade_id or str(uuid4()),
            symbol=position.symbol,
            direction=position.side,
            entry_price=position.entry_price,
            exit_price=exit_price,
            quantity=position.quantity,
            entry_time=position.opened_at,
            exit_time=exit_time,
            gross_pnl=gross_pnl,
            net_pnl=net_pnl,
            holding_hours=holding_hours,
            funding_cost=state.funding_accumulated,
        )

        # Clear position state
        state.position = None
        state.funding_accumulated = 0.0
        state.last_funding_time = None

        return equity, trade

    def _build_contexts(
        self,
        symbol_states: dict[str, SymbolState],
        bars_by_symbol: dict[str, BarEvent],
        timestamp: datetime,
    ) -> list[StrategyContext]:
        """Build strategy contexts for symbols with sufficient data."""
        contexts: list[StrategyContext] = []

        for symbol, bar in bars_by_symbol.items():
            state = symbol_states[symbol]

            # Need enough bars for indicators
            if len(state.fourh_history) < self.warmup_bars:
                continue

            fourh_df = state.get_history_df()
            if fourh_df.empty:
                continue

            # Compute daily from 4h (same as existing backtester)
            daily_df = self._resample_daily(fourh_df)
            if len(daily_df) < self.warmup_bars:
                continue

            ctx = StrategyContext(
                symbol=symbol,
                current_bar=bar,
                fourh_history=fourh_df,
                daily_history=daily_df,
                funding_rate=0.0,  # Could be enhanced to use actual funding
                news_risk="LOW",
                open_position=state.position,
                current_time=timestamp,
            )
            contexts.append(ctx)

        return contexts

    def _resample_daily(self, fourh_df: pd.DataFrame) -> pd.DataFrame:
        """Resample 4h bars to daily."""
        if fourh_df.empty:
            return pd.DataFrame()

        # Shift by 1 second so 00:00 bars group with previous day
        shifted = fourh_df.copy()
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

        # Shift index to represent daily close (00:00 of next day)
        daily.index = daily.index + pd.Timedelta(days=1)
        return daily

    def _create_candidates(
        self,
        entry_signals: dict[str, Signal],
        symbol_states: dict[str, SymbolState],
        equity: float,
        timestamp: datetime,
    ) -> list[TradeCandidate]:
        """Create trade candidates from entry signals."""
        from src.strategy.signals import Signal

        candidates: list[TradeCandidate] = []

        for symbol, signal in entry_signals.items():
            if not isinstance(signal, Signal):
                continue

            state = symbol_states[symbol]
            if state.position:
                # Already have position in this symbol
                continue

            # Create proposal
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
                trade_id=signal.trade_id or str(uuid4()),
                created_at=timestamp,
                is_entry=True,
            )

            # Evaluate through risk engine
            risk_result = self.risk_engine.evaluate(
                state=TradingState(equity=equity, peak_equity=equity),
                proposal=proposal,
                symbol_filters=self.get_symbol_filters(symbol),
                now=timestamp,
            )

            if not risk_result.approved:
                continue

            if signal.score is None:
                continue

            candidate = TradeCandidate(
                symbol=symbol,
                proposal=proposal,
                risk_result=risk_result,
                score=signal.score,
                funding_rate=0.0,
                news_risk="LOW",
                candle_timestamp=timestamp,
            )
            candidates.append(candidate)

        return candidates

    @staticmethod
    def _calculate_win_rate(trades: list[Trade]) -> float:
        """Calculate win rate from trade list."""
        if not trades:
            return 0.0
        wins = sum(1 for t in trades if t.net_pnl > 0)
        return wins / len(trades)


def run_multi_symbol_backtest(
    symbols: list[str],
    data_path: str | Path,
    strategy_config: StrategyConfig | None = None,
    risk_config: RiskConfig | None = None,
    **kwargs,
) -> MultiSymbolResult:
    """Convenience function for running multi-symbol backtests.

    Args:
        symbols: List of symbols to trade
        data_path: Path to market data directory
        strategy_config: Optional strategy config (uses defaults if not provided)
        risk_config: Optional risk config (uses defaults if not provided)
        **kwargs: Additional arguments passed to EventDrivenBacktester

    Returns:
        MultiSymbolResult with backtest results
    """
    from src.config.settings import RiskConfig, StrategyConfig

    strat_cfg = strategy_config or StrategyConfig()
    risk_cfg = risk_config or RiskConfig()

    engine = EventDrivenBacktester(
        strategy_config=strat_cfg,
        risk_config=risk_cfg,
        symbols=symbols,
        data_path=data_path,
        **kwargs,
    )

    return engine.run()
