"""Backtesting module."""

from src.backtester.engine import Backtester, BacktestResult, EquityPoint, Trade
from src.backtester.events import (
    BacktestEvent,
    BarEvent,
    EventPriority,
    FundingEvent,
    SpreadEvent,
    UniverseEvent,
)
from src.backtester.event_mux import EventMux, group_events_by_timestamp
from src.backtester.execution_model import (
    ExecutionModel,
    FillResult,
    IdealExecution,
    RealisticExecution,
    create_execution_model,
)
from src.backtester.replay_engine import (
    EventDrivenBacktester,
    MultiSymbolResult,
    SymbolState,
    run_multi_symbol_backtest,
)

__all__ = [
    # Legacy single-symbol
    "Backtester",
    "BacktestResult",
    "EquityPoint",
    "Trade",
    # Event types
    "BacktestEvent",
    "BarEvent",
    "EventPriority",
    "FundingEvent",
    "SpreadEvent",
    "UniverseEvent",
    # Event mux
    "EventMux",
    "group_events_by_timestamp",
    # Execution models
    "ExecutionModel",
    "FillResult",
    "IdealExecution",
    "RealisticExecution",
    "create_execution_model",
    # Multi-symbol engine
    "EventDrivenBacktester",
    "MultiSymbolResult",
    "SymbolState",
    "run_multi_symbol_backtest",
]
