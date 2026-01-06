"""Strategy packaging system for declarative strategy specifications.

Provides loading, validation, and metadata extraction for strategy.md files
that define strategy parameters, data requirements, and assumptions.

Each strategy is defined in strategies/<name>/strategy.md with YAML front matter.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator

# Type for parameter values
ParamType = Literal["int", "float", "str", "bool"]


class StrategyNotFoundError(Exception):
    """Raised when a strategy.md file cannot be found."""

    pass


class StrategyParseError(Exception):
    """Raised when strategy.md YAML front matter is invalid."""

    pass


class StrategyValidationError(Exception):
    """Raised when strategy configuration validation fails."""

    pass


class ParameterSpec(BaseModel):
    """Definition of a strategy parameter with type and constraints."""

    type: ParamType
    default: int | float | str | bool
    min: float | None = None
    max: float | None = None
    enum: list[str] | None = None
    description: str = ""

    @field_validator("min", "max", mode="before")
    @classmethod
    def coerce_numeric(cls, v: Any) -> float | None:
        if v is None:
            return None
        return float(v)


class BarRequirement(BaseModel):
    """Requirement for OHLCV bar data."""

    series: str = "trade"
    interval: str


class DerivedRequirement(BaseModel):
    """Requirement for derived data (resampled from source interval)."""

    interval: str
    from_interval: str = Field(alias="from")

    model_config = {"populate_by_name": True}


class DataRequirements(BaseModel):
    """All data requirements for the strategy."""

    bars: list[BarRequirement] = Field(default_factory=list)
    derived: list[DerivedRequirement] = Field(default_factory=list)
    funding: bool = False


class Assumptions(BaseModel):
    """Strategy assumptions about data interpretation and execution."""

    bar_time: str = "close_time"
    stop_model: str = "intrabar_high_low"
    funding_model: str = "discrete_settlement_events"


class StrategySpec(BaseModel):
    """Complete strategy specification parsed from strategy.md."""

    name: str
    version: int
    requires: DataRequirements = Field(default_factory=DataRequirements)
    parameters: dict[str, ParameterSpec] = Field(default_factory=dict)
    assumptions: Assumptions = Field(default_factory=Assumptions)
    spec_hash: str = ""
    spec_path: str | None = None


class StrategyMetadata(BaseModel):
    """Metadata for recording strategy info in backtest/run outputs."""

    name: str
    version: int
    spec_hash: str
    resolved_parameters: dict[str, Any] = Field(default_factory=dict)


def get_strategies_dir() -> Path:
    """Get the strategies directory.

    Checks STRATEGIES_DIR env var first, otherwise uses repo_root/strategies.
    """
    env_dir = os.environ.get("STRATEGIES_DIR")
    if env_dir:
        return Path(env_dir)

    # Derive repo root from this file's location
    # This file is at: repo_root/src/strategy/package.py
    repo_root = Path(__file__).resolve().parent.parent.parent
    return repo_root / "strategies"


def get_strategy_path(strategy_name: str) -> Path:
    """Get the path to a strategy's strategy.md file."""
    return get_strategies_dir() / strategy_name / "strategy.md"


def compute_spec_hash(spec_path: Path) -> str:
    """Compute SHA256 hash of strategy.md file content."""
    content = spec_path.read_bytes()
    return hashlib.sha256(content).hexdigest()


def _parse_yaml_front_matter(content: str) -> dict[str, Any]:
    """Parse YAML front matter from markdown content.

    Front matter is delimited by --- at start and end.
    """
    # Match YAML front matter between --- delimiters
    pattern = r"^---\s*\n(.*?)\n---"
    match = re.match(pattern, content, re.DOTALL)
    if not match:
        raise StrategyParseError("No YAML front matter found (must start with ---)")

    yaml_content = match.group(1)
    try:
        data = yaml.safe_load(yaml_content)
    except yaml.YAMLError as e:
        raise StrategyParseError(f"Invalid YAML front matter: {e}") from e

    if not isinstance(data, dict):
        raise StrategyParseError("YAML front matter must be a mapping")

    return data


def load_strategy_spec(strategy_name: str) -> StrategySpec:
    """Load and parse strategy.md file for given strategy name.

    Args:
        strategy_name: Name of the strategy (e.g., "trend_following_v1")

    Returns:
        Parsed StrategySpec with all fields populated

    Raises:
        StrategyNotFoundError: If strategy.md doesn't exist
        StrategyParseError: If YAML front matter is invalid or missing required fields
    """
    spec_path = get_strategy_path(strategy_name)

    if not spec_path.exists():
        raise StrategyNotFoundError(
            f"Strategy spec not found: {spec_path}\n"
            f"Create {spec_path} with YAML front matter defining the strategy."
        )

    content = spec_path.read_text(encoding="utf-8")
    data = _parse_yaml_front_matter(content)

    # Validate required fields
    if "name" not in data:
        raise StrategyParseError("Missing required field: name")
    if "version" not in data:
        raise StrategyParseError("Missing required field: version")

    # Parse parameters - convert shorthand dict format to ParameterSpec
    if "parameters" in data and isinstance(data["parameters"], dict):
        params = {}
        for key, value in data["parameters"].items():
            if isinstance(value, dict):
                params[key] = ParameterSpec(**value)
            else:
                raise StrategyParseError(
                    f"Parameter '{key}' must be a dict with 'type' and 'default'"
                )
        data["parameters"] = params

    # Compute hash before parsing (for reproducibility)
    spec_hash = compute_spec_hash(spec_path)

    try:
        spec = StrategySpec(**data, spec_hash=spec_hash, spec_path=str(spec_path))
    except Exception as e:
        raise StrategyParseError(f"Invalid strategy spec: {e}") from e

    return spec


def validate_data_requirements(
    spec: StrategySpec,
    available_intervals: set[str],
    has_funding: bool = False,
) -> list[str]:
    """Validate that required data inputs are available.

    Args:
        spec: Strategy specification
        available_intervals: Set of available data intervals (e.g., {"4h", "1d"})
        has_funding: Whether funding data is available

    Returns:
        List of error messages (empty if all requirements satisfied)
    """
    errors: list[str] = []

    # Check bar requirements
    for bar in spec.requires.bars:
        if bar.interval not in available_intervals:
            errors.append(
                f"Missing required bar data: series={bar.series}, interval={bar.interval}"
            )

    # Check derived requirements - need the source interval
    for derived in spec.requires.derived:
        if derived.from_interval not in available_intervals:
            errors.append(
                f"Missing source data for derived interval {derived.interval}: "
                f"requires {derived.from_interval} data"
            )

    # Check funding requirement
    if spec.requires.funding and not has_funding:
        errors.append("Strategy requires funding data but none provided")

    return errors


def _validate_parameter_value(
    name: str,
    value: Any,
    param_spec: ParameterSpec,
) -> tuple[Any, list[str]]:
    """Validate a single parameter value against its spec.

    Returns (coerced_value, errors).
    """
    errors: list[str] = []

    # Type checking and coercion
    if param_spec.type == "int":
        if not isinstance(value, int) or isinstance(value, bool):
            # Allow float that is whole number
            if isinstance(value, float) and value == int(value):
                value = int(value)
            else:
                errors.append(f"Parameter '{name}' must be int, got {type(value).__name__}")
                return value, errors
    elif param_spec.type == "float":
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            errors.append(f"Parameter '{name}' must be float, got {type(value).__name__}")
            return value, errors
        value = float(value)
    elif param_spec.type == "str":
        if not isinstance(value, str):
            errors.append(f"Parameter '{name}' must be str, got {type(value).__name__}")
            return value, errors
    elif param_spec.type == "bool":
        if not isinstance(value, bool):
            errors.append(f"Parameter '{name}' must be bool, got {type(value).__name__}")
            return value, errors

    # Range validation for numeric types
    if param_spec.type in ("int", "float") and isinstance(value, (int, float)):
        if param_spec.min is not None and float(value) < param_spec.min:
            errors.append(f"Parameter '{name}' value {value} is below minimum {param_spec.min}")
        if param_spec.max is not None and float(value) > param_spec.max:
            errors.append(f"Parameter '{name}' value {value} is above maximum {param_spec.max}")

    # Enum validation for string types
    if param_spec.type == "str" and param_spec.enum is not None:
        if value not in param_spec.enum:
            errors.append(
                f"Parameter '{name}' value '{value}' not in allowed values: {param_spec.enum}"
            )

    return value, errors


def validate_parameter_overrides(
    spec: StrategySpec,
    overrides: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Validate and merge parameter overrides with spec defaults.

    Args:
        spec: Strategy specification with parameter definitions
        overrides: Dict of parameter overrides from config

    Returns:
        Tuple of (resolved_parameters, errors).
        resolved_parameters contains spec defaults merged with valid overrides.
        errors contains validation error messages.
    """
    errors: list[str] = []
    resolved: dict[str, Any] = {}

    # Start with defaults from spec
    for name, param_spec in spec.parameters.items():
        resolved[name] = param_spec.default

    # Check for unknown keys in overrides
    unknown_keys = set(overrides.keys()) - set(spec.parameters.keys())
    for key in unknown_keys:
        errors.append(f"Unknown parameter '{key}' not defined in strategy spec")

    # Validate and apply overrides
    for name, value in overrides.items():
        if name not in spec.parameters:
            continue  # Already reported as unknown

        param_spec = spec.parameters[name]
        validated_value, param_errors = _validate_parameter_value(name, value, param_spec)
        errors.extend(param_errors)

        if not param_errors:
            resolved[name] = validated_value

    return resolved, errors


def get_resolved_parameters(
    spec: StrategySpec,
    overrides: dict[str, Any],
) -> dict[str, Any]:
    """Get resolved parameters (defaults + overrides) without validation errors.

    This is a convenience wrapper that returns just the resolved dict.
    Use validate_parameter_overrides() if you need error messages.
    """
    resolved, _ = validate_parameter_overrides(spec, overrides)
    return resolved


def _extract_config_overrides(strategy_config: Any) -> dict[str, Any]:
    """Extract parameter overrides from a StrategyConfig object.

    Maps the nested config structure to flat parameter names matching strategy.md.
    """
    overrides: dict[str, Any] = {}

    # Map StrategyConfig fields to spec parameter names
    # Indicators
    if hasattr(strategy_config, "indicators"):
        ind = strategy_config.indicators
        if hasattr(ind, "ema_fast"):
            overrides["ema_fast"] = ind.ema_fast
        if hasattr(ind, "ema_slow"):
            overrides["ema_slow"] = ind.ema_slow
        if hasattr(ind, "atr_period"):
            overrides["atr_period"] = ind.atr_period
        if hasattr(ind, "rsi_period"):
            overrides["rsi_period"] = ind.rsi_period
        if hasattr(ind, "adx_period"):
            overrides["adx_period"] = ind.adx_period
        if hasattr(ind, "chop_period"):
            overrides["chop_period"] = ind.chop_period
        if hasattr(ind, "volume_sma_period"):
            overrides["volume_sma_period"] = ind.volume_sma_period

    # Entry config
    if hasattr(strategy_config, "entry"):
        entry = strategy_config.entry
        if hasattr(entry, "style"):
            overrides["entry_style"] = entry.style
        if hasattr(entry, "score_threshold"):
            overrides["score_threshold"] = entry.score_threshold
        if hasattr(entry, "max_breakout_extension_atr"):
            overrides["max_breakout_extension_atr"] = entry.max_breakout_extension_atr
        if hasattr(entry, "volume_breakout_threshold"):
            overrides["volume_breakout_threshold"] = entry.volume_breakout_threshold
        if hasattr(entry, "volume_confirmation_enabled"):
            overrides["volume_confirmation_enabled"] = entry.volume_confirmation_enabled

    # Exit config
    if hasattr(strategy_config, "exit"):
        exit_cfg = strategy_config.exit
        if hasattr(exit_cfg, "atr_stop_multiplier"):
            overrides["atr_stop_multiplier"] = exit_cfg.atr_stop_multiplier
        if hasattr(exit_cfg, "trailing_start_atr"):
            overrides["trailing_start_atr"] = exit_cfg.trailing_start_atr
        if hasattr(exit_cfg, "trailing_distance_atr"):
            overrides["trailing_distance_atr"] = exit_cfg.trailing_distance_atr
        if hasattr(exit_cfg, "time_stop_days"):
            overrides["time_stop_days"] = exit_cfg.time_stop_days

    # Scoring config
    if hasattr(strategy_config, "scoring"):
        scoring = strategy_config.scoring
        if hasattr(scoring, "enabled"):
            overrides["scoring_enabled"] = scoring.enabled
        if hasattr(scoring, "threshold"):
            # Use scoring threshold as the primary threshold
            overrides["score_threshold"] = scoring.threshold
        if hasattr(scoring, "factors"):
            factors = scoring.factors
            if hasattr(factors, "trend"):
                overrides["factor_trend"] = factors.trend
            if hasattr(factors, "volatility"):
                overrides["factor_volatility"] = factors.volatility
            if hasattr(factors, "entry_quality"):
                overrides["factor_entry_quality"] = factors.entry_quality
            if hasattr(factors, "funding"):
                overrides["factor_funding"] = factors.funding
            if hasattr(factors, "news"):
                overrides["factor_news"] = factors.news

    return overrides


def validate_strategy_config(
    spec: StrategySpec,
    strategy_config: Any,
) -> list[str]:
    """Validate a StrategyConfig from settings against the strategy spec.

    Args:
        spec: Strategy specification
        strategy_config: StrategyConfig object from settings

    Returns:
        List of validation error messages (empty if valid)
    """
    # Extract overrides from the config object
    overrides = _extract_config_overrides(strategy_config)

    # Validate the extracted overrides
    _, errors = validate_parameter_overrides(spec, overrides)

    return errors


def create_strategy_metadata(
    spec: StrategySpec,
    strategy_config: Any,
) -> StrategyMetadata:
    """Create strategy metadata for recording in outputs.

    Args:
        spec: Strategy specification
        strategy_config: StrategyConfig object from settings

    Returns:
        StrategyMetadata with resolved parameters
    """
    overrides = _extract_config_overrides(strategy_config)
    resolved = get_resolved_parameters(spec, overrides)

    return StrategyMetadata(
        name=spec.name,
        version=spec.version,
        spec_hash=spec.spec_hash,
        resolved_parameters=resolved,
    )
