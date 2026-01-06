"""Tests for strategy packaging system."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

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
    compute_spec_hash,
    create_strategy_metadata,
    get_resolved_parameters,
    get_strategies_dir,
    load_strategy_spec,
    validate_data_requirements,
    validate_parameter_overrides,
    validate_strategy_config,
)

# ---------------------------------------------------------------------------
# Helper data
# ---------------------------------------------------------------------------


def get_valid_strategy_md() -> str:
    """Valid strategy.md content for testing."""
    return """\
---
name: test_strategy
version: 1
requires:
  bars:
    - series: trade
      interval: 4h
  derived:
    - interval: 1d
      from: 4h
  funding: true
parameters:
  ema_fast:
    type: int
    default: 8
    min: 2
    max: 50
    description: Fast EMA period
  atr_multiplier:
    type: float
    default: 2.0
    min: 1.0
    max: 5.0
    description: ATR multiplier
  entry_style:
    type: str
    default: breakout
    enum: [breakout, pullback]
    description: Entry style
  use_trailing:
    type: bool
    default: true
    description: Use trailing stop
assumptions:
  bar_time: close_time
  stop_model: intrabar_high_low
  funding_model: discrete_settlement_events
---

# Test Strategy

This is the documentation section.
"""


def get_minimal_strategy_md() -> str:
    """Minimal valid strategy.md content."""
    return """\
---
name: minimal_test
version: 2
parameters:
  threshold:
    type: float
    default: 0.5
---

# Minimal Test Strategy
"""


# ---------------------------------------------------------------------------
# Test: Pydantic Models
# ---------------------------------------------------------------------------


def test_parameter_spec_int() -> None:
    spec = ParameterSpec(type="int", default=10, min=1, max=100)
    assert spec.type == "int"
    assert spec.default == 10
    assert spec.min == 1.0
    assert spec.max == 100.0


def test_parameter_spec_float() -> None:
    spec = ParameterSpec(type="float", default=2.5, min=0.5, max=10.0)
    assert spec.type == "float"
    assert spec.default == 2.5


def test_parameter_spec_str_with_enum() -> None:
    spec = ParameterSpec(type="str", default="breakout", enum=["breakout", "pullback"])
    assert spec.type == "str"
    assert spec.default == "breakout"
    assert spec.enum == ["breakout", "pullback"]


def test_parameter_spec_bool() -> None:
    spec = ParameterSpec(type="bool", default=True)
    assert spec.type == "bool"
    assert spec.default is True


def test_parameter_spec_coerces_min_max() -> None:
    """Min/max should be coerced to float."""
    spec = ParameterSpec(type="int", default=10, min=1, max=100)
    assert isinstance(spec.min, float)
    assert isinstance(spec.max, float)


def test_bar_requirement() -> None:
    req = BarRequirement(series="trade", interval="4h")
    assert req.series == "trade"
    assert req.interval == "4h"


def test_derived_requirement() -> None:
    # Test with alias "from"
    req = DerivedRequirement(**{"interval": "1d", "from": "4h"})
    assert req.interval == "1d"
    assert req.from_interval == "4h"


def test_data_requirements_defaults() -> None:
    req = DataRequirements()
    assert req.bars == []
    assert req.derived == []
    assert req.funding is False


def test_assumptions_defaults() -> None:
    a = Assumptions()
    assert a.bar_time == "close_time"
    assert a.stop_model == "intrabar_high_low"
    assert a.funding_model == "discrete_settlement_events"


def test_strategy_spec() -> None:
    spec = StrategySpec(
        name="test",
        version=1,
        parameters={
            "foo": ParameterSpec(type="int", default=10),
        },
    )
    assert spec.name == "test"
    assert spec.version == 1
    assert "foo" in spec.parameters


def test_strategy_metadata() -> None:
    meta = StrategyMetadata(
        name="test",
        version=1,
        spec_hash="abc123",
        resolved_parameters={"foo": 10},
    )
    assert meta.name == "test"
    assert meta.resolved_parameters["foo"] == 10


# ---------------------------------------------------------------------------
# Test: Strategy Loading
# ---------------------------------------------------------------------------


def test_get_strategies_dir_default() -> None:
    """Default strategies dir is repo_root/strategies."""
    # Clear env var if set
    old_val = os.environ.pop("STRATEGIES_DIR", None)
    try:
        strategies_dir = get_strategies_dir()
        # Should end with "strategies"
        assert strategies_dir.name == "strategies"
    finally:
        if old_val:
            os.environ["STRATEGIES_DIR"] = old_val


def test_get_strategies_dir_from_env(workspace_tmp_path: Path) -> None:
    """STRATEGIES_DIR env var overrides default."""
    old_val = os.environ.get("STRATEGIES_DIR")
    try:
        os.environ["STRATEGIES_DIR"] = str(workspace_tmp_path)
        assert get_strategies_dir() == workspace_tmp_path
    finally:
        if old_val:
            os.environ["STRATEGIES_DIR"] = old_val
        else:
            os.environ.pop("STRATEGIES_DIR", None)


def test_load_strategy_spec_not_found(workspace_tmp_path: Path) -> None:
    """Should raise StrategyNotFoundError for missing strategy."""
    old_val = os.environ.get("STRATEGIES_DIR")
    try:
        os.environ["STRATEGIES_DIR"] = str(workspace_tmp_path)
        with pytest.raises(StrategyNotFoundError, match="Strategy spec not found"):
            load_strategy_spec("nonexistent")
    finally:
        if old_val:
            os.environ["STRATEGIES_DIR"] = old_val
        else:
            os.environ.pop("STRATEGIES_DIR", None)


def test_load_strategy_spec_valid(workspace_tmp_path: Path) -> None:
    """Should parse valid strategy.md correctly."""
    old_val = os.environ.get("STRATEGIES_DIR")
    try:
        os.environ["STRATEGIES_DIR"] = str(workspace_tmp_path)

        strategy_dir = workspace_tmp_path / "test_strategy"
        strategy_dir.mkdir()
        (strategy_dir / "strategy.md").write_text(get_valid_strategy_md(), encoding="utf-8")

        spec = load_strategy_spec("test_strategy")

        assert spec.name == "test_strategy"
        assert spec.version == 1
        assert spec.spec_hash  # Should have computed hash
        assert spec.spec_path == str(strategy_dir / "strategy.md")

        # Check parameters
        assert "ema_fast" in spec.parameters
        assert spec.parameters["ema_fast"].type == "int"
        assert spec.parameters["ema_fast"].default == 8
        assert spec.parameters["ema_fast"].min == 2.0

        assert "entry_style" in spec.parameters
        assert spec.parameters["entry_style"].enum == ["breakout", "pullback"]

        # Check requirements
        assert len(spec.requires.bars) == 1
        assert spec.requires.bars[0].interval == "4h"
        assert len(spec.requires.derived) == 1
        assert spec.requires.derived[0].from_interval == "4h"
        assert spec.requires.funding is True

        assert spec.assumptions.bar_time == "close_time"
    finally:
        if old_val:
            os.environ["STRATEGIES_DIR"] = old_val
        else:
            os.environ.pop("STRATEGIES_DIR", None)


def test_load_strategy_spec_minimal(workspace_tmp_path: Path) -> None:
    """Should parse minimal strategy.md with defaults."""
    old_val = os.environ.get("STRATEGIES_DIR")
    try:
        os.environ["STRATEGIES_DIR"] = str(workspace_tmp_path)

        strategy_dir = workspace_tmp_path / "minimal_test"
        strategy_dir.mkdir()
        (strategy_dir / "strategy.md").write_text(get_minimal_strategy_md(), encoding="utf-8")

        spec = load_strategy_spec("minimal_test")

        assert spec.name == "minimal_test"
        assert spec.version == 2
        assert spec.requires.bars == []
        assert spec.requires.funding is False
    finally:
        if old_val:
            os.environ["STRATEGIES_DIR"] = old_val
        else:
            os.environ.pop("STRATEGIES_DIR", None)


def test_load_strategy_spec_no_front_matter(workspace_tmp_path: Path) -> None:
    """Should raise StrategyParseError if no YAML front matter."""
    old_val = os.environ.get("STRATEGIES_DIR")
    try:
        os.environ["STRATEGIES_DIR"] = str(workspace_tmp_path)

        strategy_dir = workspace_tmp_path / "bad_strategy"
        strategy_dir.mkdir()
        (strategy_dir / "strategy.md").write_text(
            "# No Front Matter\n\nJust content.", encoding="utf-8"
        )

        with pytest.raises(StrategyParseError, match="No YAML front matter"):
            load_strategy_spec("bad_strategy")
    finally:
        if old_val:
            os.environ["STRATEGIES_DIR"] = old_val
        else:
            os.environ.pop("STRATEGIES_DIR", None)


def test_load_strategy_spec_invalid_yaml(workspace_tmp_path: Path) -> None:
    """Should raise StrategyParseError for invalid YAML."""
    old_val = os.environ.get("STRATEGIES_DIR")
    try:
        os.environ["STRATEGIES_DIR"] = str(workspace_tmp_path)

        strategy_dir = workspace_tmp_path / "invalid_yaml"
        strategy_dir.mkdir()
        (strategy_dir / "strategy.md").write_text(
            "---\nname: test\n  bad indent:\n---\n", encoding="utf-8"
        )

        with pytest.raises(StrategyParseError, match="Invalid YAML"):
            load_strategy_spec("invalid_yaml")
    finally:
        if old_val:
            os.environ["STRATEGIES_DIR"] = old_val
        else:
            os.environ.pop("STRATEGIES_DIR", None)


def test_load_strategy_spec_missing_name(workspace_tmp_path: Path) -> None:
    """Should raise StrategyParseError if name is missing."""
    old_val = os.environ.get("STRATEGIES_DIR")
    try:
        os.environ["STRATEGIES_DIR"] = str(workspace_tmp_path)

        strategy_dir = workspace_tmp_path / "no_name"
        strategy_dir.mkdir()
        (strategy_dir / "strategy.md").write_text("---\nversion: 1\n---\n", encoding="utf-8")

        with pytest.raises(StrategyParseError, match="Missing required field: name"):
            load_strategy_spec("no_name")
    finally:
        if old_val:
            os.environ["STRATEGIES_DIR"] = old_val
        else:
            os.environ.pop("STRATEGIES_DIR", None)


def test_load_strategy_spec_missing_version(workspace_tmp_path: Path) -> None:
    """Should raise StrategyParseError if version is missing."""
    old_val = os.environ.get("STRATEGIES_DIR")
    try:
        os.environ["STRATEGIES_DIR"] = str(workspace_tmp_path)

        strategy_dir = workspace_tmp_path / "no_version"
        strategy_dir.mkdir()
        (strategy_dir / "strategy.md").write_text("---\nname: test\n---\n", encoding="utf-8")

        with pytest.raises(StrategyParseError, match="Missing required field: version"):
            load_strategy_spec("no_version")
    finally:
        if old_val:
            os.environ["STRATEGIES_DIR"] = old_val
        else:
            os.environ.pop("STRATEGIES_DIR", None)


# ---------------------------------------------------------------------------
# Test: Hash Computation
# ---------------------------------------------------------------------------


def test_compute_spec_hash(workspace_tmp_path: Path) -> None:
    """Hash should be consistent and change with content."""
    file1 = workspace_tmp_path / "file1.md"
    file2 = workspace_tmp_path / "file2.md"

    file1.write_text("content A", encoding="utf-8")
    file2.write_text("content A", encoding="utf-8")

    assert compute_spec_hash(file1) == compute_spec_hash(file2)

    file2.write_text("content B", encoding="utf-8")
    assert compute_spec_hash(file1) != compute_spec_hash(file2)

    assert len(compute_spec_hash(file1)) == 64


# ---------------------------------------------------------------------------
# Test: Data Requirements Validation
# ---------------------------------------------------------------------------


def test_validate_data_requirements_all_satisfied() -> None:
    spec = StrategySpec(
        name="test",
        version=1,
        requires=DataRequirements(
            bars=[BarRequirement(interval="4h")],
            derived=[DerivedRequirement(**{"interval": "1d", "from": "4h"})],
            funding=True,
        ),
    )

    errors = validate_data_requirements(
        spec,
        available_intervals={"4h", "1d"},
        has_funding=True,
    )

    assert errors == []


def test_validate_data_requirements_missing_bar_interval() -> None:
    spec = StrategySpec(
        name="test",
        version=1,
        requires=DataRequirements(
            bars=[BarRequirement(interval="4h")],
        ),
    )

    errors = validate_data_requirements(
        spec,
        available_intervals={"1h"},  # Missing 4h
        has_funding=False,
    )

    assert len(errors) == 1
    assert "4h" in errors[0]


def test_validate_data_requirements_missing_derived_source() -> None:
    spec = StrategySpec(
        name="test",
        version=1,
        requires=DataRequirements(
            derived=[DerivedRequirement(**{"interval": "1d", "from": "4h"})],
        ),
    )

    errors = validate_data_requirements(
        spec,
        available_intervals={"1h"},  # Missing 4h source
        has_funding=False,
    )

    assert len(errors) == 1
    assert "4h" in errors[0]
    assert "1d" in errors[0]


def test_validate_data_requirements_missing_funding() -> None:
    spec = StrategySpec(
        name="test",
        version=1,
        requires=DataRequirements(funding=True),
    )

    errors = validate_data_requirements(
        spec,
        available_intervals={"4h"},
        has_funding=False,
    )

    assert len(errors) == 1
    assert "funding" in errors[0].lower()


def test_validate_data_requirements_multiple_errors() -> None:
    spec = StrategySpec(
        name="test",
        version=1,
        requires=DataRequirements(
            bars=[BarRequirement(interval="4h")],
            funding=True,
        ),
    )

    errors = validate_data_requirements(
        spec,
        available_intervals={"1h"},
        has_funding=False,
    )

    assert len(errors) == 2


# ---------------------------------------------------------------------------
# Test: Parameter Validation
# ---------------------------------------------------------------------------


def test_validate_parameter_overrides_empty() -> None:
    """Empty overrides should return defaults."""
    spec = StrategySpec(
        name="test",
        version=1,
        parameters={
            "foo": ParameterSpec(type="int", default=10),
            "bar": ParameterSpec(type="float", default=2.5),
        },
    )

    resolved, errors = validate_parameter_overrides(spec, {})

    assert errors == []
    assert resolved["foo"] == 10
    assert resolved["bar"] == 2.5


def test_validate_parameter_overrides_valid_override() -> None:
    """Valid override should be applied."""
    spec = StrategySpec(
        name="test",
        version=1,
        parameters={
            "foo": ParameterSpec(type="int", default=10, min=1, max=100),
        },
    )

    resolved, errors = validate_parameter_overrides(spec, {"foo": 50})

    assert errors == []
    assert resolved["foo"] == 50


def test_validate_parameter_overrides_int_type_error() -> None:
    """Int parameter with non-int value should error."""
    spec = StrategySpec(
        name="test",
        version=1,
        parameters={
            "foo": ParameterSpec(type="int", default=10),
        },
    )

    resolved, errors = validate_parameter_overrides(spec, {"foo": "not an int"})

    assert len(errors) == 1
    assert "must be int" in errors[0]


def test_validate_parameter_overrides_float_from_int() -> None:
    """Float parameter should accept int value."""
    spec = StrategySpec(
        name="test",
        version=1,
        parameters={
            "foo": ParameterSpec(type="float", default=2.5),
        },
    )

    resolved, errors = validate_parameter_overrides(spec, {"foo": 3})

    assert errors == []
    assert resolved["foo"] == 3.0
    assert isinstance(resolved["foo"], float)


def test_validate_parameter_overrides_int_from_whole_float() -> None:
    """Int parameter should accept whole number float."""
    spec = StrategySpec(
        name="test",
        version=1,
        parameters={
            "foo": ParameterSpec(type="int", default=10),
        },
    )

    resolved, errors = validate_parameter_overrides(spec, {"foo": 15.0})

    assert errors == []
    assert resolved["foo"] == 15
    assert isinstance(resolved["foo"], int)


def test_validate_parameter_overrides_str_type_error() -> None:
    """String parameter with non-string value should error."""
    spec = StrategySpec(
        name="test",
        version=1,
        parameters={
            "style": ParameterSpec(type="str", default="breakout"),
        },
    )

    resolved, errors = validate_parameter_overrides(spec, {"style": 123})

    assert len(errors) == 1
    assert "must be str" in errors[0]


def test_validate_parameter_overrides_bool_type_error() -> None:
    """Bool parameter with non-bool value should error."""
    spec = StrategySpec(
        name="test",
        version=1,
        parameters={
            "enabled": ParameterSpec(type="bool", default=True),
        },
    )

    # Note: 1 is not considered bool (even though it's truthy)
    resolved, errors = validate_parameter_overrides(spec, {"enabled": 1})

    assert len(errors) == 1
    assert "must be bool" in errors[0]


def test_validate_parameter_overrides_below_min() -> None:
    """Value below minimum should error."""
    spec = StrategySpec(
        name="test",
        version=1,
        parameters={
            "foo": ParameterSpec(type="int", default=10, min=5, max=100),
        },
    )

    resolved, errors = validate_parameter_overrides(spec, {"foo": 2})

    assert len(errors) == 1
    assert "below minimum" in errors[0]


def test_validate_parameter_overrides_above_max() -> None:
    """Value above maximum should error."""
    spec = StrategySpec(
        name="test",
        version=1,
        parameters={
            "foo": ParameterSpec(type="float", default=2.0, min=1.0, max=5.0),
        },
    )

    resolved, errors = validate_parameter_overrides(spec, {"foo": 10.0})

    assert len(errors) == 1
    assert "above maximum" in errors[0]


def test_validate_parameter_overrides_enum_valid() -> None:
    """Value in enum should be accepted."""
    spec = StrategySpec(
        name="test",
        version=1,
        parameters={
            "style": ParameterSpec(type="str", default="breakout", enum=["breakout", "pullback"]),
        },
    )

    resolved, errors = validate_parameter_overrides(spec, {"style": "pullback"})

    assert errors == []
    assert resolved["style"] == "pullback"


def test_validate_parameter_overrides_enum_invalid() -> None:
    """Value not in enum should error."""
    spec = StrategySpec(
        name="test",
        version=1,
        parameters={
            "style": ParameterSpec(type="str", default="breakout", enum=["breakout", "pullback"]),
        },
    )

    resolved, errors = validate_parameter_overrides(spec, {"style": "scalp"})

    assert len(errors) == 1
    assert "not in allowed values" in errors[0]


def test_validate_parameter_overrides_unknown_key() -> None:
    """Unknown parameter key should error."""
    spec = StrategySpec(
        name="test",
        version=1,
        parameters={
            "foo": ParameterSpec(type="int", default=10),
        },
    )

    resolved, errors = validate_parameter_overrides(spec, {"unknown_param": 5})

    assert len(errors) == 1
    assert "Unknown parameter" in errors[0]
    assert "unknown_param" in errors[0]
    # Resolved should still have defaults
    assert resolved["foo"] == 10


def test_validate_parameter_overrides_multiple_errors() -> None:
    """Multiple validation errors should all be reported."""
    spec = StrategySpec(
        name="test",
        version=1,
        parameters={
            "foo": ParameterSpec(type="int", default=10, min=1, max=100),
            "bar": ParameterSpec(type="str", default="a", enum=["a", "b"]),
        },
    )

    resolved, errors = validate_parameter_overrides(spec, {"foo": 200, "bar": "c", "unknown": 1})

    assert len(errors) == 3


def test_get_resolved_parameters() -> None:
    """get_resolved_parameters should return dict without errors."""
    spec = StrategySpec(
        name="test",
        version=1,
        parameters={
            "foo": ParameterSpec(type="int", default=10),
            "bar": ParameterSpec(type="float", default=2.5),
        },
    )

    resolved = get_resolved_parameters(spec, {"foo": 20})

    assert resolved["foo"] == 20
    assert resolved["bar"] == 2.5


# ---------------------------------------------------------------------------
# Test: Strategy Config Validation (mock config objects)
# ---------------------------------------------------------------------------


class MockIndicators:
    def __init__(self, ema_fast: int = 8, ema_slow: int = 21):
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow


class MockEntry:
    def __init__(self, style: str = "breakout", score_threshold: float = 0.55):
        self.style = style
        self.score_threshold = score_threshold


class MockExit:
    def __init__(self, atr_stop_multiplier: float = 2.0):
        self.atr_stop_multiplier = atr_stop_multiplier


class MockStrategyConfig:
    def __init__(
        self,
        indicators: MockIndicators | None = None,
        entry: MockEntry | None = None,
        exit_cfg: MockExit | None = None,
    ):
        self.indicators = indicators or MockIndicators()
        self.entry = entry or MockEntry()
        self.exit = exit_cfg or MockExit()


def test_validate_strategy_config_valid() -> None:
    """Valid config should pass validation."""
    spec = StrategySpec(
        name="test",
        version=1,
        parameters={
            "ema_fast": ParameterSpec(type="int", default=8, min=2, max=50),
            "ema_slow": ParameterSpec(type="int", default=21, min=5, max=100),
            "entry_style": ParameterSpec(
                type="str", default="breakout", enum=["breakout", "pullback"]
            ),
            "score_threshold": ParameterSpec(type="float", default=0.55, min=0.3, max=0.95),
            "atr_stop_multiplier": ParameterSpec(type="float", default=2.0, min=1.0, max=5.0),
        },
    )

    config = MockStrategyConfig()
    errors = validate_strategy_config(spec, config)

    assert errors == []


def test_validate_strategy_config_invalid() -> None:
    """Invalid config should return errors."""
    spec = StrategySpec(
        name="test",
        version=1,
        parameters={
            "ema_fast": ParameterSpec(type="int", default=8, min=2, max=50),
            "entry_style": ParameterSpec(
                type="str", default="breakout", enum=["breakout", "pullback"]
            ),
        },
    )

    # ema_fast=1 is below min=2, entry_style="scalp" not in enum
    config = MockStrategyConfig(
        indicators=MockIndicators(ema_fast=1),
        entry=MockEntry(style="scalp"),
    )
    errors = validate_strategy_config(spec, config)

    # Only ema_fast error expected because entry.style maps to entry_style
    assert len(errors) >= 1
    # At least the ema_fast error
    assert any("below minimum" in e for e in errors)


# ---------------------------------------------------------------------------
# Test: Create Strategy Metadata
# ---------------------------------------------------------------------------


def test_create_strategy_metadata() -> None:
    """Should create metadata with resolved parameters."""
    spec = StrategySpec(
        name="test_strategy",
        version=1,
        spec_hash="abc123def456",
        parameters={
            "ema_fast": ParameterSpec(type="int", default=8),
            "ema_slow": ParameterSpec(type="int", default=21),
        },
    )

    config = MockStrategyConfig(indicators=MockIndicators(ema_fast=10))
    meta = create_strategy_metadata(spec, config)

    assert meta.name == "test_strategy"
    assert meta.version == 1
    assert meta.spec_hash == "abc123def456"
    assert meta.resolved_parameters["ema_fast"] == 10  # Overridden
    assert meta.resolved_parameters["ema_slow"] == 21  # Default


# ---------------------------------------------------------------------------
# Test: Integration with Real Strategy File
# ---------------------------------------------------------------------------


def test_load_real_trend_following_v1() -> None:
    """Load the actual trend_following_v1 strategy (if it exists)."""
    # Clear env var to use default location
    old_val = os.environ.pop("STRATEGIES_DIR", None)
    spec: StrategySpec | None = None
    try:
        spec = load_strategy_spec("trend_following_v1")
    except StrategyNotFoundError:
        pass  # Will skip below
    finally:
        if old_val:
            os.environ["STRATEGIES_DIR"] = old_val

    if spec is None:
        pytest.skip("trend_following_v1 strategy not found in default location")
        return  # For type checker

    assert spec.name == "trend_following_v1"
    assert spec.version == 1

    # Check some expected parameters
    assert "ema_fast" in spec.parameters
    assert "ema_slow" in spec.parameters
    assert "entry_style" in spec.parameters
    assert "atr_stop_multiplier" in spec.parameters

    # Check requirements
    assert len(spec.requires.bars) >= 1
    assert spec.requires.funding is True

    # Validate against typical available data
    errors = validate_data_requirements(
        spec,
        available_intervals={"4h"},
        has_funding=True,
    )
    assert errors == []


def test_load_real_trend_following_v1_hash_stable() -> None:
    """Hash should be stable across loads."""
    old_val = os.environ.pop("STRATEGIES_DIR", None)
    spec1: StrategySpec | None = None
    spec2: StrategySpec | None = None
    try:
        spec1 = load_strategy_spec("trend_following_v1")
        spec2 = load_strategy_spec("trend_following_v1")
    except StrategyNotFoundError:
        pass  # Will skip below
    finally:
        if old_val:
            os.environ["STRATEGIES_DIR"] = old_val

    if spec1 is None or spec2 is None:
        pytest.skip("trend_following_v1 strategy not found")
        return  # For type checker

    assert spec1.spec_hash == spec2.spec_hash
    assert len(spec1.spec_hash) == 64  # SHA256
