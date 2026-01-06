"""Strategy runner protocol and adapters for event-driven backtesting.

Provides a clean interface for strategy execution within the replay engine,
decoupling the backtest engine from specific strategy implementations.
"""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Protocol

import pandas as pd

from src.backtester.events import BarEvent
from src.strategy.signals import Signal, SignalGenerator, SignalType

if TYPE_CHECKING:
    from src.config.settings import RegimeConfig, StrategyConfig
    from src.ledger.state import Position


@dataclass(frozen=True)
class StrategyContext:
    """Context for strategy signal generation.

    Provides the strategy with all necessary market data at a point in time.
    """

    symbol: str
    current_bar: BarEvent
    fourh_history: pd.DataFrame  # Historical 4h bars up to current time
    daily_history: pd.DataFrame  # Historical daily bars up to current time
    funding_rate: float
    news_risk: str
    open_position: Position | None
    current_time: datetime


class StrategyRunner(Protocol):
    """Protocol for strategy execution.

    Implementations generate signals from market context.
    This abstraction allows swapping strategy implementations
    without changing the backtest engine.
    """

    @abstractmethod
    def generate_signal(self, context: StrategyContext) -> Signal:
        """Generate a trading signal for the given context.

        Args:
            context: Market data and position context

        Returns:
            Signal indicating entry/exit/hold decision
        """
        ...


class TrendFollowingRunner:
    """Adapter wrapping SignalGenerator for the StrategyRunner protocol.

    This is the default strategy runner used by the backtest engine.
    It delegates to the existing SignalGenerator implementation.
    """

    def __init__(
        self,
        strategy_config: StrategyConfig,
        regime_config: RegimeConfig | None = None,
    ) -> None:
        """Initialize with strategy configuration.

        Args:
            strategy_config: Strategy parameters
            regime_config: Optional regime classification config
        """
        self.signal_generator = SignalGenerator(strategy_config, regime_config)

    def generate_signal(self, context: StrategyContext) -> Signal:
        """Generate signal using the trend-following strategy.

        Args:
            context: Market data and position context

        Returns:
            Trading signal
        """
        return self.signal_generator.generate(
            symbol=context.symbol,
            daily_df=context.daily_history,
            fourh_df=context.fourh_history,
            funding_rate=context.funding_rate,
            news_risk=context.news_risk,  # type: ignore[arg-type]
            open_position=context.open_position,
            current_time=context.current_time,
        )


@dataclass(frozen=True)
class SignalBatch:
    """Collection of signals generated at a single timestamp.

    Used for cross-sectional processing where signals from multiple
    symbols are generated and ranked together.
    """

    timestamp: datetime
    signals: dict[str, Signal]  # symbol -> Signal

    def get_entry_signals(self) -> dict[str, Signal]:
        """Get signals that are entry opportunities (LONG/SHORT)."""
        return {
            symbol: signal
            for symbol, signal in self.signals.items()
            if signal.signal_type in {SignalType.LONG, SignalType.SHORT}
        }

    def get_exit_signals(self) -> dict[str, Signal]:
        """Get signals that are exits."""
        return {
            symbol: signal
            for symbol, signal in self.signals.items()
            if signal.signal_type == SignalType.EXIT
        }


class MultiSymbolRunner:
    """Runs strategy across multiple symbols for cross-sectional analysis.

    Generates signals for all active symbols at each timestamp,
    enabling portfolio-level ranking and selection.
    """

    def __init__(self, runner: StrategyRunner) -> None:
        """Initialize with a strategy runner.

        Args:
            runner: Strategy implementation to use for each symbol
        """
        self.runner = runner

    def generate_signals(self, contexts: list[StrategyContext]) -> SignalBatch:
        """Generate signals for multiple symbols.

        Args:
            contexts: Strategy contexts for each symbol

        Returns:
            Batch of signals keyed by symbol
        """
        if not contexts:
            raise ValueError("At least one context required")

        timestamp = contexts[0].current_time
        signals: dict[str, Signal] = {}

        for ctx in contexts:
            signal = self.runner.generate_signal(ctx)
            signals[ctx.symbol] = signal

        return SignalBatch(timestamp=timestamp, signals=signals)
