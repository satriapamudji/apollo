"""Trading strategy and scoring module."""

from src.strategy.package import (
    Assumptions,
    BarRequirement,
    DataRequirements,
    DerivedRequirement,
    ParameterSpec,
    StrategyMetadata,
    StrategyNotFoundError,
    StrategyParseError,
    StrategySpec,
    StrategyValidationError,
    compute_spec_hash,
    create_strategy_metadata,
    load_strategy_spec,
    validate_data_requirements,
    validate_parameter_overrides,
    validate_strategy_config,
)
from src.strategy.scoring import CompositeScore, ScoringEngine
from src.strategy.signals import Signal, SignalGenerator, SignalType
from src.strategy.universe import (
    FilteredSymbol,
    UniverseSelectionResult,
    UniverseSelector,
    UniverseSymbol,
)

__all__ = [
    # Package/spec system
    "ParameterSpec",
    "BarRequirement",
    "DerivedRequirement",
    "DataRequirements",
    "Assumptions",
    "StrategySpec",
    "StrategyMetadata",
    "StrategyNotFoundError",
    "StrategyParseError",
    "StrategyValidationError",
    "load_strategy_spec",
    "validate_data_requirements",
    "validate_parameter_overrides",
    "validate_strategy_config",
    "create_strategy_metadata",
    "compute_spec_hash",
    # Scoring
    "ScoringEngine",
    "CompositeScore",
    # Signals
    "SignalGenerator",
    "Signal",
    "SignalType",
    # Universe
    "UniverseSelector",
    "UniverseSymbol",
    "UniverseSelectionResult",
    "FilteredSymbol",
]
