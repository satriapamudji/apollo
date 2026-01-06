"""Symbol rules loading and management for backtesting.

This module provides utilities to load symbol trading rules (tick_size, step_size,
min_qty, min_notional) from exchangeInfo snapshots for reproducible backtesting.

Symbol rules are versioned with an effective_date so backtests can be pinned to
specific rule snapshots for reproducibility.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.risk.sizing import SymbolFilters


@dataclass(frozen=True)
class SymbolRuleEntry:
    """Trading rules for a single symbol extracted from exchangeInfo."""

    symbol: str
    tick_size: float
    step_size: float
    min_qty: float
    min_notional: float
    contract_type: str = "PERPETUAL"
    status: str = "TRADING"
    quote_asset: str = "USDT"
    base_asset: str = ""
    margin_asset: str = ""
    price_precision: int = 2
    quantity_precision: int = 3
    defaults_applied: tuple[str, ...] = field(default_factory=tuple)

    def to_symbol_filters(self) -> SymbolFilters:
        """Convert to SymbolFilters for use with risk/sizing modules."""
        return SymbolFilters(
            min_notional=self.min_notional,
            min_qty=self.min_qty,
            step_size=self.step_size,
            tick_size=self.tick_size,
        )


@dataclass
class SymbolRulesSnapshot:
    """A versioned snapshot of symbol trading rules."""

    effective_date: datetime
    source_file: str
    rules: dict[str, SymbolRuleEntry]
    server_time: int | None = None
    timezone: str = "UTC"

    def get_filters(self, symbol: str) -> SymbolFilters:
        """Get SymbolFilters for a symbol, raises KeyError if not found."""
        if symbol not in self.rules:
            available = sorted(self.rules.keys())[:10]
            raise KeyError(
                f"Symbol '{symbol}' not found in rules snapshot. "
                f"Available symbols (first 10): {available}"
            )
        return self.rules[symbol].to_symbol_filters()

    def get_rule(self, symbol: str) -> SymbolRuleEntry:
        """Get full rule entry for a symbol, raises KeyError if not found."""
        if symbol not in self.rules:
            available = sorted(self.rules.keys())[:10]
            raise KeyError(
                f"Symbol '{symbol}' not found in rules snapshot. "
                f"Available symbols (first 10): {available}"
            )
        return self.rules[symbol]

    def has_symbol(self, symbol: str) -> bool:
        """Check if symbol exists in this snapshot."""
        return symbol in self.rules


class SymbolRulesError(Exception):
    """Error loading or parsing symbol rules."""

    pass


def load_symbol_rules(path: Path | str) -> SymbolRulesSnapshot:
    """Load symbol rules from a JSON file.

    Args:
        path: Path to symbol_rules_*.json file

    Returns:
        SymbolRulesSnapshot with parsed rules

    Raises:
        SymbolRulesError: If file cannot be loaded or parsed
    """
    path = Path(path)

    if not path.exists():
        raise SymbolRulesError(f"Symbol rules file not found: {path}")

    try:
        with open(path) as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise SymbolRulesError(f"Invalid JSON in symbol rules file: {e}") from e

    return _parse_rules_data(data, source_file=path.name)


def _parse_rules_data(data: dict[str, Any], source_file: str = "") -> SymbolRulesSnapshot:
    """Parse rules data dict into SymbolRulesSnapshot."""
    # Parse effective date
    effective_date_str = data.get("effective_date")
    if effective_date_str:
        try:
            effective_date = datetime.fromisoformat(effective_date_str.replace("Z", "+00:00"))
        except ValueError:
            effective_date = datetime.now(timezone.utc)
    else:
        effective_date = datetime.now(timezone.utc)

    # Parse rules
    rules_data = data.get("rules", {})
    rules: dict[str, SymbolRuleEntry] = {}

    for symbol, rule_dict in rules_data.items():
        defaults_applied = rule_dict.get("defaults_applied", [])
        if isinstance(defaults_applied, list):
            defaults_applied = tuple(defaults_applied)
        else:
            defaults_applied = ()

        rules[symbol] = SymbolRuleEntry(
            symbol=rule_dict.get("symbol", symbol),
            tick_size=float(rule_dict.get("tick_size", 0.01)),
            step_size=float(rule_dict.get("step_size", 0.001)),
            min_qty=float(rule_dict.get("min_qty", 0.001)),
            min_notional=float(rule_dict.get("min_notional", 5.0)),
            contract_type=rule_dict.get("contract_type", "PERPETUAL"),
            status=rule_dict.get("status", "TRADING"),
            quote_asset=rule_dict.get("quote_asset", "USDT"),
            base_asset=rule_dict.get("base_asset", ""),
            margin_asset=rule_dict.get("margin_asset", ""),
            price_precision=int(rule_dict.get("price_precision", 2)),
            quantity_precision=int(rule_dict.get("quantity_precision", 3)),
            defaults_applied=defaults_applied,
        )

    # Prefer source_file from JSON data if present, fallback to provided path
    resolved_source_file = data.get("source_file", "") or source_file

    return SymbolRulesSnapshot(
        effective_date=effective_date,
        source_file=resolved_source_file,
        rules=rules,
        server_time=data.get("server_time"),
        timezone=data.get("timezone", "UTC"),
    )


def find_latest_rules(data_dir: Path | str) -> Path | None:
    """Find the latest symbol rules file in a directory.

    Looks for symbol_rules_latest.json first, then falls back to
    the most recently dated symbol_rules_*.json file.

    Args:
        data_dir: Directory containing symbol rules files

    Returns:
        Path to the latest rules file, or None if not found
    """
    data_dir = Path(data_dir)

    if not data_dir.exists():
        return None

    # Check for 'latest' file first
    latest_file = data_dir / "symbol_rules_latest.json"
    if latest_file.exists():
        return latest_file

    # Find most recent dated file
    pattern = "symbol_rules_*.json"
    rules_files = sorted(data_dir.glob(pattern), reverse=True)

    # Filter out 'latest' if it somehow got included
    rules_files = [f for f in rules_files if "latest" not in f.name]

    return rules_files[0] if rules_files else None


def find_rules_for_date(
    data_dir: Path | str,
    target_date: datetime,
) -> Path | None:
    """Find the appropriate rules file for a specific date.

    Finds the rules file with effective_date closest to but not after
    the target date. This ensures backtests use rules that were valid
    at the time of the historical data.

    Args:
        data_dir: Directory containing symbol rules files
        target_date: Target date for rule selection

    Returns:
        Path to the appropriate rules file, or None if not found
    """
    data_dir = Path(data_dir)

    if not data_dir.exists():
        return None

    # Find all rules files (excluding 'latest')
    pattern = "symbol_rules_*.json"
    rules_files = [f for f in data_dir.glob(pattern) if "latest" not in f.name]

    if not rules_files:
        return None

    # Parse dates from filenames (format: symbol_rules_YYYYMMDD_HHMMSS.json)
    dated_files: list[tuple[datetime, Path]] = []
    for path in rules_files:
        try:
            # Extract date part from filename
            name = path.stem  # symbol_rules_20240115_120000
            date_part = name.replace("symbol_rules_", "")
            file_date = datetime.strptime(date_part, "%Y%m%d_%H%M%S").replace(tzinfo=timezone.utc)
            dated_files.append((file_date, path))
        except ValueError:
            continue

    if not dated_files:
        return None

    # Sort by date descending
    dated_files.sort(key=lambda x: x[0], reverse=True)

    # Find the most recent file that is not after target_date
    target_date_utc = (
        target_date.replace(tzinfo=timezone.utc) if target_date.tzinfo is None else target_date
    )

    for file_date, path in dated_files:
        if file_date <= target_date_utc:
            return path

    # If all files are after target_date, return the oldest one
    return dated_files[-1][1]


def auto_load_rules(
    rules_path: Path | str | None = None,
    data_dir: Path | str = "./data/exchange_info",
) -> SymbolRulesSnapshot | None:
    """Automatically load symbol rules from path or default location.

    Args:
        rules_path: Explicit path to rules file (takes precedence)
        data_dir: Directory to search for rules if path not specified

    Returns:
        SymbolRulesSnapshot if found, None otherwise

    Raises:
        SymbolRulesError: If explicit path is provided but cannot be loaded
    """
    if rules_path is not None:
        # Explicit path - must succeed or raise
        return load_symbol_rules(rules_path)

    # Try to find rules in default location
    latest = find_latest_rules(data_dir)
    if latest is not None:
        return load_symbol_rules(latest)

    return None
