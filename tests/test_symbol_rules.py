"""Tests for symbol rules parsing and loading."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.backtester.symbol_rules import (
    SymbolRuleEntry,
    SymbolRulesError,
    SymbolRulesSnapshot,
    auto_load_rules,
    find_latest_rules,
    find_rules_for_date,
    load_symbol_rules,
)
from src.tools.download_exchange_info import parse_symbol_rules

# Realistic BTCUSDT exchange info payload (subset of actual Binance response)
BTCUSDT_EXCHANGE_INFO = {
    "symbols": [
        {
            "symbol": "BTCUSDT",
            "pair": "BTCUSDT",
            "contractType": "PERPETUAL",
            "deliveryDate": 4133404800000,
            "onboardDate": 1569398400000,
            "status": "TRADING",
            "maintMarginPercent": "2.5000",
            "requiredMarginPercent": "5.0000",
            "baseAsset": "BTC",
            "quoteAsset": "USDT",
            "marginAsset": "USDT",
            "pricePrecision": 2,
            "quantityPrecision": 3,
            "baseAssetPrecision": 8,
            "quotePrecision": 8,
            "filters": [
                {
                    "filterType": "PRICE_FILTER",
                    "minPrice": "556.80",
                    "maxPrice": "4529764",
                    "tickSize": "0.10",
                },
                {
                    "filterType": "LOT_SIZE",
                    "minQty": "0.001",
                    "maxQty": "1000",
                    "stepSize": "0.001",
                },
                {
                    "filterType": "MARKET_LOT_SIZE",
                    "minQty": "0.001",
                    "maxQty": "120",
                    "stepSize": "0.001",
                },
                {
                    "filterType": "MAX_NUM_ORDERS",
                    "limit": 200,
                },
                {
                    "filterType": "MAX_NUM_ALGO_ORDERS",
                    "limit": 10,
                },
                {
                    "filterType": "MIN_NOTIONAL",
                    "notional": "5",
                },
                {
                    "filterType": "PERCENT_PRICE",
                    "multiplierUp": "1.0500",
                    "multiplierDown": "0.9500",
                    "multiplierDecimal": "4",
                },
            ],
        }
    ]
}


# Minimal symbol with missing filters to test defaults
MINIMAL_SYMBOL_EXCHANGE_INFO = {
    "symbols": [
        {
            "symbol": "TESTUSDT",
            "contractType": "PERPETUAL",
            "status": "TRADING",
            "quoteAsset": "USDT",
            "baseAsset": "TEST",
            "filters": [],  # No filters - should apply defaults
        }
    ]
}


def test_parse_exchange_info_btcusdt() -> None:
    """Test parsing realistic BTCUSDT filter data."""
    rules = parse_symbol_rules(BTCUSDT_EXCHANGE_INFO)

    assert "BTCUSDT" in rules
    btc_rule = rules["BTCUSDT"]

    # Verify extracted values
    assert btc_rule["tick_size"] == 0.10
    assert btc_rule["step_size"] == 0.001
    assert btc_rule["min_qty"] == 0.001
    assert btc_rule["min_notional"] == 5.0

    # Verify metadata
    assert btc_rule["contract_type"] == "PERPETUAL"
    assert btc_rule["status"] == "TRADING"
    assert btc_rule["quote_asset"] == "USDT"
    assert btc_rule["base_asset"] == "BTC"
    assert btc_rule["price_precision"] == 2
    assert btc_rule["quantity_precision"] == 3

    # No defaults should have been applied
    assert btc_rule["defaults_applied"] == []


def test_parse_exchange_info_missing_filters() -> None:
    """Test that defaults are tracked when filters are missing."""
    rules = parse_symbol_rules(MINIMAL_SYMBOL_EXCHANGE_INFO)

    assert "TESTUSDT" in rules
    test_rule = rules["TESTUSDT"]

    # Should use defaults
    assert test_rule["tick_size"] == 0.01
    assert test_rule["step_size"] == 0.001
    assert test_rule["min_qty"] == 0.001
    assert test_rule["min_notional"] == 5.0

    # Defaults should be recorded
    assert "tick_size" in test_rule["defaults_applied"]
    assert "step_size" in test_rule["defaults_applied"]
    assert "min_qty" in test_rule["defaults_applied"]
    assert "min_notional" in test_rule["defaults_applied"]


def test_load_symbol_rules_from_file(workspace_tmp_path: Path) -> None:
    """Test loading symbol rules from a JSON file."""
    rules_data = {
        "effective_date": "2024-01-15T12:00:00+00:00",
        "source_file": "exchange_info_20240115_120000.json",
        "symbol_count": 1,
        "rules": {
            "BTCUSDT": {
                "symbol": "BTCUSDT",
                "tick_size": 0.10,
                "step_size": 0.001,
                "min_qty": 0.001,
                "min_notional": 5.0,
                "contract_type": "PERPETUAL",
                "status": "TRADING",
                "quote_asset": "USDT",
                "defaults_applied": [],
            }
        },
    }

    temp_path = workspace_tmp_path / "rules.json"
    temp_path.write_text(json.dumps(rules_data), encoding="utf-8")

    snapshot = load_symbol_rules(temp_path)

    assert snapshot.source_file == "exchange_info_20240115_120000.json"
    assert snapshot.effective_date.year == 2024
    assert snapshot.effective_date.month == 1
    assert snapshot.effective_date.day == 15

    assert snapshot.has_symbol("BTCUSDT")
    assert not snapshot.has_symbol("ETHUSDT")

    rule = snapshot.get_rule("BTCUSDT")
    assert rule.tick_size == 0.10
    assert rule.step_size == 0.001
    assert rule.min_qty == 0.001
    assert rule.min_notional == 5.0
    # file cleanup handled by workspace_tmp_path fixture


def test_get_symbol_filters_conversion() -> None:
    """Test conversion from SymbolRuleEntry to SymbolFilters."""
    entry = SymbolRuleEntry(
        symbol="BTCUSDT",
        tick_size=0.10,
        step_size=0.001,
        min_qty=0.001,
        min_notional=5.0,
    )

    filters = entry.to_symbol_filters()

    assert filters.tick_size == 0.10
    assert filters.step_size == 0.001
    assert filters.min_qty == 0.001
    assert filters.min_notional == 5.0


def test_snapshot_get_filters_not_found() -> None:
    """Test that KeyError is raised for missing symbol."""
    snapshot = SymbolRulesSnapshot(
        effective_date=datetime.now(timezone.utc),
        source_file="test.json",
        rules={
            "BTCUSDT": SymbolRuleEntry(
                symbol="BTCUSDT",
                tick_size=0.10,
                step_size=0.001,
                min_qty=0.001,
                min_notional=5.0,
            )
        },
    )

    # Should work for BTCUSDT
    filters = snapshot.get_filters("BTCUSDT")
    assert filters.tick_size == 0.10

    # Should raise for missing symbol
    with pytest.raises(KeyError, match="ETHUSDT.*not found"):
        snapshot.get_filters("ETHUSDT")


def test_load_symbol_rules_file_not_found() -> None:
    """Test that SymbolRulesError is raised for missing file."""
    with pytest.raises(SymbolRulesError, match="not found"):
        load_symbol_rules("/nonexistent/path/rules.json")


def test_find_latest_rules(workspace_tmp_path: Path) -> None:
    """Test finding latest rules file."""
    data_dir = workspace_tmp_path / "rules"
    data_dir.mkdir(parents=True, exist_ok=True)

    assert find_latest_rules(data_dir) is None

    latest_file = data_dir / "symbol_rules_latest.json"
    latest_file.write_text('{"rules": {}}', encoding="utf-8")

    assert find_latest_rules(data_dir) == latest_file


def test_find_rules_for_date(workspace_tmp_path: Path) -> None:
    """Test finding rules file for a specific date."""
    data_dir = workspace_tmp_path / "rules"
    data_dir.mkdir(parents=True, exist_ok=True)

    # Create dated files
    file1 = data_dir / "symbol_rules_20240101_120000.json"
    file2 = data_dir / "symbol_rules_20240115_120000.json"
    file3 = data_dir / "symbol_rules_20240201_120000.json"

    for f in [file1, file2, file3]:
        f.write_text('{"rules": {}}', encoding="utf-8")

    target = datetime(2024, 1, 20, tzinfo=timezone.utc)
    result = find_rules_for_date(data_dir, target)
    assert result == file2

    target = datetime(2024, 3, 1, tzinfo=timezone.utc)
    result = find_rules_for_date(data_dir, target)
    assert result == file3

    target = datetime(2023, 12, 1, tzinfo=timezone.utc)
    result = find_rules_for_date(data_dir, target)
    assert result == file1


def test_auto_load_rules_with_explicit_path(workspace_tmp_path: Path) -> None:
    """Test auto_load_rules with explicit path."""
    rules_data = {
        "effective_date": "2024-01-15T12:00:00+00:00",
        "source_file": "test.json",
        "rules": {
            "BTCUSDT": {
                "symbol": "BTCUSDT",
                "tick_size": 0.10,
                "step_size": 0.001,
                "min_qty": 0.001,
                "min_notional": 5.0,
            }
        },
    }

    temp_path = workspace_tmp_path / "explicit_rules.json"
    temp_path.write_text(json.dumps(rules_data), encoding="utf-8")

    snapshot = auto_load_rules(rules_path=temp_path)
    assert snapshot is not None
    assert snapshot.has_symbol("BTCUSDT")


def test_auto_load_rules_no_files(workspace_tmp_path: Path) -> None:
    """Test auto_load_rules returns None when no files exist."""
    data_dir = workspace_tmp_path / "empty_rules_dir"
    data_dir.mkdir(parents=True, exist_ok=True)
    result = auto_load_rules(data_dir=str(data_dir))
    assert result is None
