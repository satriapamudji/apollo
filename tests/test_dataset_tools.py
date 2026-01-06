"""Tests for dataset tools (universe builder, store builder, snapshot builder, validator)."""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from src.data.models import (
    ArtifactTimeRange,
    ChecksumEntry,
    ChecksumFile,
    DatasetManifest,
    ExecutionAssumptions,
    ManifestProvenance,
    StoreArtifact,
    SymbolRules,
    SymbolRulesSnapshot,
    TimeRange,
    UniverseProvenance,
    UniverseSelectionParams,
    UniverseSnapshot,
    UniverseSymbol,
)
from src.tools.build_snapshot import (
    compute_sha256,
    count_csv_rows,
    find_store_files,
    get_month_range,
)
from src.tools.build_universe import build_universe, parse_exchange_info
from src.tools.validate_dataset import (
    validate_csv_schema,
    validate_manifest_consistency,
    validate_snapshot,
)


class TestDataModels:
    """Tests for Pydantic data models."""

    def test_universe_symbol_creation(self) -> None:
        symbol = UniverseSymbol(
            symbol="BTCUSDT",
            quote_volume_24h=1_000_000_000.0,
            last_price=50000.0,
            price_change_pct_24h=2.5,
            high_24h=51000.0,
            low_24h=49000.0,
            trades_24h=100000,
            status="TRADING",
            contract_type="PERPETUAL",
        )
        assert symbol.symbol == "BTCUSDT"
        assert symbol.quote_volume_24h == 1_000_000_000.0

    def test_universe_snapshot_serialization(self) -> None:
        now = datetime.now(timezone.utc)
        snapshot = UniverseSnapshot(
            snapshot_id="test_snapshot",
            as_of_date="2024-01-01",
            created_at_utc=now,
            selection_params=UniverseSelectionParams(
                min_quote_volume_usd=50_000_000,
                size=5,
            ),
            symbols=[
                UniverseSymbol(
                    symbol="BTCUSDT",
                    quote_volume_24h=1_000_000_000.0,
                    last_price=50000.0,
                    price_change_pct_24h=2.5,
                    high_24h=51000.0,
                    low_24h=49000.0,
                    trades_24h=100000,
                    status="TRADING",
                    contract_type="PERPETUAL",
                )
            ],
            provenance=UniverseProvenance(
                fetch_timestamp_utc=now,
                total_symbols_fetched=100,
                symbols_after_status_filter=80,
                symbols_after_volume_filter=20,
            ),
        )

        # Test JSON serialization
        json_data = snapshot.model_dump(mode="json")
        assert json_data["snapshot_id"] == "test_snapshot"
        assert len(json_data["symbols"]) == 1

        # Test round-trip
        restored = UniverseSnapshot.model_validate(json_data)
        assert restored.snapshot_id == snapshot.snapshot_id

    def test_symbol_rules_creation(self) -> None:
        rules = SymbolRules(
            symbol="BTCUSDT",
            tick_size=0.01,
            step_size=0.001,
            min_qty=0.001,
            max_qty=1000.0,
            min_notional=5.0,
            contract_type="PERPETUAL",
            status="TRADING",
            quote_asset="USDT",
            base_asset="BTC",
            effective_date="2024-01-01",
        )
        assert rules.symbol == "BTCUSDT"
        assert rules.tick_size == 0.01

    def test_checksum_file_to_sha256_format(self) -> None:
        checksums = ChecksumFile(
            snapshot_id="test",
            created_at_utc=datetime.now(timezone.utc),
            entries=[
                ChecksumEntry(path="file1.csv", sha256="abc123", size_bytes=100),
                ChecksumEntry(path="file2.csv", sha256="def456", size_bytes=200),
            ],
        )
        output = checksums.to_sha256_format()
        assert "abc123  file1.csv" in output
        assert "def456  file2.csv" in output

    def test_checksum_file_from_sha256_format(self) -> None:
        content = "abc123  file1.csv\ndef456  file2.csv\n"
        checksums = ChecksumFile.from_sha256_format(content, "test")
        assert len(checksums.entries) == 2
        assert checksums.entries[0].sha256 == "abc123"
        assert checksums.entries[0].path == "file1.csv"


class TestBuildUniverseHelpers:
    """Tests for build_universe helper functions."""

    def test_parse_exchange_info(self) -> None:
        exchange_info = {
            "symbols": [
                {
                    "symbol": "BTCUSDT",
                    "status": "TRADING",
                    "contractType": "PERPETUAL",
                    "quoteAsset": "USDT",
                    "baseAsset": "BTC",
                },
                {
                    "symbol": "ETHUSDT",
                    "status": "TRADING",
                    "contractType": "PERPETUAL",
                    "quoteAsset": "USDT",
                    "baseAsset": "ETH",
                },
            ]
        }
        result = parse_exchange_info(exchange_info)
        assert "BTCUSDT" in result
        assert result["BTCUSDT"]["status"] == "TRADING"
        assert result["BTCUSDT"]["contractType"] == "PERPETUAL"

    def test_build_universe_filters_correctly(self) -> None:
        tickers = [
            {
                "symbol": "BTCUSDT",
                "quoteVolume": "1000000000",
                "lastPrice": "50000",
                "priceChangePercent": "2.5",
                "highPrice": "51000",
                "lowPrice": "49000",
                "count": 100000,
            },
            {
                "symbol": "ETHUSDT",
                "quoteVolume": "500000000",
                "lastPrice": "3000",
                "priceChangePercent": "1.5",
                "highPrice": "3100",
                "lowPrice": "2900",
                "count": 50000,
            },
            {
                "symbol": "XRPUSDT",
                "quoteVolume": "10000000",
                "lastPrice": "0.5",
                "priceChangePercent": "0.5",
                "highPrice": "0.51",
                "lowPrice": "0.49",
                "count": 10000,
            },
        ]
        exchange_info_map = {
            "BTCUSDT": {"status": "TRADING", "contractType": "PERPETUAL", "quoteAsset": "USDT"},
            "ETHUSDT": {"status": "TRADING", "contractType": "PERPETUAL", "quoteAsset": "USDT"},
            "XRPUSDT": {"status": "TRADING", "contractType": "PERPETUAL", "quoteAsset": "USDT"},
        }

        # Should filter out XRPUSDT due to low volume
        symbols, stats = build_universe(
            tickers=tickers,
            exchange_info_map=exchange_info_map,
            min_quote_volume_usd=50_000_000,
            size=10,
        )

        assert len(symbols) == 2
        assert symbols[0].symbol == "BTCUSDT"  # Highest volume first
        assert symbols[1].symbol == "ETHUSDT"

    def test_build_universe_respects_deny_list(self) -> None:
        tickers = [
            {
                "symbol": "BTCUSDT",
                "quoteVolume": "1000000000",
                "lastPrice": "50000",
                "priceChangePercent": "2.5",
                "highPrice": "51000",
                "lowPrice": "49000",
                "count": 100000,
            },
            {
                "symbol": "ETHUSDT",
                "quoteVolume": "500000000",
                "lastPrice": "3000",
                "priceChangePercent": "1.5",
                "highPrice": "3100",
                "lowPrice": "2900",
                "count": 50000,
            },
        ]
        exchange_info_map = {
            "BTCUSDT": {"status": "TRADING", "contractType": "PERPETUAL", "quoteAsset": "USDT"},
            "ETHUSDT": {"status": "TRADING", "contractType": "PERPETUAL", "quoteAsset": "USDT"},
        }

        symbols, _ = build_universe(
            tickers=tickers,
            exchange_info_map=exchange_info_map,
            min_quote_volume_usd=0,
            size=10,
            deny_list=["BTCUSDT"],
        )

        assert len(symbols) == 1
        assert symbols[0].symbol == "ETHUSDT"

    def test_build_universe_filters_non_perpetual(self) -> None:
        tickers = [
            {
                "symbol": "BTCUSDT",
                "quoteVolume": "1000000000",
                "lastPrice": "50000",
                "priceChangePercent": "2.5",
                "highPrice": "51000",
                "lowPrice": "49000",
                "count": 100000,
            },
            {
                "symbol": "BTCUSDT_QUARTERLY",
                "quoteVolume": "500000000",
                "lastPrice": "50100",
                "priceChangePercent": "2.6",
                "highPrice": "51100",
                "lowPrice": "49100",
                "count": 50000,
            },
        ]
        exchange_info_map = {
            "BTCUSDT": {"status": "TRADING", "contractType": "PERPETUAL", "quoteAsset": "USDT"},
            "BTCUSDT_QUARTERLY": {
                "status": "TRADING",
                "contractType": "CURRENT_QUARTER",
                "quoteAsset": "USDT",
            },
        }

        symbols, _ = build_universe(
            tickers=tickers,
            exchange_info_map=exchange_info_map,
            min_quote_volume_usd=0,
            size=10,
        )

        assert len(symbols) == 1
        assert symbols[0].symbol == "BTCUSDT"


class TestBuildSnapshotHelpers:
    """Tests for build_snapshot helper functions."""

    def test_get_month_range(self) -> None:
        start = datetime(2024, 1, 15, tzinfo=timezone.utc)
        end = datetime(2024, 4, 20, tzinfo=timezone.utc)
        months = get_month_range(start, end)
        assert months == ["2024-01", "2024-02", "2024-03", "2024-04"]

    def test_get_month_range_single_month(self) -> None:
        start = datetime(2024, 3, 1, tzinfo=timezone.utc)
        end = datetime(2024, 3, 31, tzinfo=timezone.utc)
        months = get_month_range(start, end)
        assert months == ["2024-03"]

    def test_compute_sha256(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
            f.write("test content")
            temp_path = Path(f.name)

        try:
            hash_value = compute_sha256(temp_path)
            # SHA256 of "test content"
            assert len(hash_value) == 64  # SHA256 hex length
            assert hash_value == "6ae8a75555209fd6c44157c0aed8016e763ff435a19cf186f76863140143ff72"
        finally:
            temp_path.unlink()

    def test_count_csv_rows(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".csv") as f:
            f.write("col1,col2\n")
            f.write("a,1\n")
            f.write("b,2\n")
            f.write("c,3\n")
            temp_path = Path(f.name)

        try:
            count = count_csv_rows(temp_path)
            assert count == 3  # Excludes header
        finally:
            temp_path.unlink()

    def test_find_store_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store_dir = Path(tmpdir)

            # Create kline file structure
            kline_dir = store_dir / "bars" / "trade" / "interval=4h" / "symbol=BTCUSDT"
            kline_dir.mkdir(parents=True)
            (kline_dir / "month=2024-01.csv").write_text("header\ndata")
            (kline_dir / "month=2024-02.csv").write_text("header\ndata")

            # Create funding file structure
            funding_dir = store_dir / "funding" / "symbol=BTCUSDT"
            funding_dir.mkdir(parents=True)
            (funding_dir / "month=2024-01.csv").write_text("header\ndata")

            start = datetime(2024, 1, 1, tzinfo=timezone.utc)
            end = datetime(2024, 2, 28, tzinfo=timezone.utc)

            files = find_store_files(
                store_dir=store_dir,
                symbols=["BTCUSDT"],
                intervals=["4h"],
                start_date=start,
                end_date=end,
            )

            assert len(files["klines"]) == 2
            assert len(files["funding"]) == 1


class TestValidateDataset:
    """Tests for validate_dataset functions."""

    def test_validate_csv_schema_pass(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".csv") as f:
            f.write("open_time,open,high,low,close,volume,close_time\n")
            f.write("1704067200000,50000,51000,49000,50500,100,1704081600000\n")
            temp_path = Path(f.name)

        try:
            required = {"open_time", "open", "high", "low", "close", "volume", "close_time"}
            missing = validate_csv_schema(temp_path, required)
            assert missing == []
        finally:
            temp_path.unlink()

    def test_validate_csv_schema_fail(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".csv") as f:
            f.write("timestamp,open,high,low,close\n")  # Missing volume and close_time
            f.write("1704067200000,50000,51000,49000,50500\n")
            temp_path = Path(f.name)

        try:
            required = {"open_time", "open", "high", "low", "close", "volume", "close_time"}
            missing = validate_csv_schema(temp_path, required)
            assert "volume" in missing
            assert "close_time" in missing
            assert "open_time" in missing  # timestamp != open_time
        finally:
            temp_path.unlink()

    def test_validate_manifest_consistency_pass(self) -> None:
        manifest = DatasetManifest(
            snapshot_id="test",
            created_at_utc=datetime.now(timezone.utc),
            universe_file="universe/test.json",
            universe_snapshot_id="universe_test",
            time_range=TimeRange(start="2024-01-01", end="2024-06-01"),
            intervals=["4h"],
            symbols=["BTCUSDT"],
            artifacts=[
                ArtifactTimeRange(
                    artifact_type="klines",
                    symbol="BTCUSDT",
                    interval="4h",
                    time_range=TimeRange(start="2024-01-01", end="2024-06-01"),
                    files=[],
                )
            ],
            provenance=ManifestProvenance(build_timestamp_utc=datetime.now(timezone.utc)),
            assumptions=ExecutionAssumptions(),
        )

        result = validate_manifest_consistency(manifest)
        assert result.status == "PASS"

    def test_validate_manifest_consistency_fail_symbol_mismatch(self) -> None:
        manifest = DatasetManifest(
            snapshot_id="test",
            created_at_utc=datetime.now(timezone.utc),
            universe_file="universe/test.json",
            universe_snapshot_id="universe_test",
            time_range=TimeRange(start="2024-01-01", end="2024-06-01"),
            intervals=["4h"],
            symbols=["BTCUSDT"],
            artifacts=[
                ArtifactTimeRange(
                    artifact_type="klines",
                    symbol="ETHUSDT",  # Not in manifest.symbols
                    interval="4h",
                    time_range=TimeRange(start="2024-01-01", end="2024-06-01"),
                    files=[],
                )
            ],
            provenance=ManifestProvenance(build_timestamp_utc=datetime.now(timezone.utc)),
            assumptions=ExecutionAssumptions(),
        )

        result = validate_manifest_consistency(manifest)
        assert result.status == "FAIL"

    def test_full_validation_with_valid_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            store_dir = base_dir / "store"
            snapshot_dir = base_dir / "snapshots" / "test_snapshot"
            snapshot_dir.mkdir(parents=True)

            # Create a minimal valid store structure
            kline_dir = store_dir / "bars" / "trade" / "interval=4h" / "symbol=BTCUSDT"
            kline_dir.mkdir(parents=True)
            kline_file = kline_dir / "month=2024-01.csv"
            kline_file.write_text(
                "open_time,open,high,low,close,volume,close_time,quote_volume,trades,"
                "taker_buy_volume,taker_buy_quote_volume\n"
                "1704067200000,50000,51000,49000,50500,100,1704081600000,5000000,1000,50,2500000\n"
            )

            # Create symbol rules
            rules_dir = store_dir / "metadata" / "symbol_rules"
            rules_dir.mkdir(parents=True)
            rules_snapshot = SymbolRulesSnapshot(
                snapshot_id="rules_2024-01-01",
                fetched_at_utc=datetime.now(timezone.utc),
                rules=[
                    SymbolRules(
                        symbol="BTCUSDT",
                        tick_size=0.01,
                        step_size=0.001,
                        min_qty=0.001,
                        max_qty=1000.0,
                        min_notional=5.0,
                        contract_type="PERPETUAL",
                        status="TRADING",
                        quote_asset="USDT",
                        base_asset="BTC",
                        effective_date="2024-01-01",
                    )
                ],
            )
            rules_file = rules_dir / "2024-01-01.json"
            with open(rules_file, "w") as f:
                json.dump(rules_snapshot.model_dump(mode="json"), f, default=str)

            # Compute checksum for the kline file
            kline_hash = compute_sha256(kline_file)
            rules_hash = compute_sha256(rules_file)

            # Create manifest
            manifest = DatasetManifest(
                snapshot_id="test_snapshot",
                created_at_utc=datetime.now(timezone.utc),
                universe_file="universe/test.json",
                universe_snapshot_id="universe_test",
                time_range=TimeRange(start="2024-01-01", end="2024-01-31"),
                intervals=["4h"],
                symbols=["BTCUSDT"],
                artifacts=[
                    ArtifactTimeRange(
                        artifact_type="klines",
                        symbol="BTCUSDT",
                        interval="4h",
                        time_range=TimeRange(start="2024-01-01", end="2024-01-31"),
                        files=[
                            StoreArtifact(
                                path="bars/trade/interval=4h/symbol=BTCUSDT/month=2024-01.csv",
                                sha256=kline_hash,
                                size_bytes=kline_file.stat().st_size,
                                row_count=1,
                            )
                        ],
                    ),
                    ArtifactTimeRange(
                        artifact_type="exchange_info",
                        symbol=None,
                        interval=None,
                        time_range=TimeRange(start="2024-01-01", end="2024-01-31"),
                        files=[
                            StoreArtifact(
                                path="metadata/symbol_rules/2024-01-01.json",
                                sha256=rules_hash,
                                size_bytes=rules_file.stat().st_size,
                                row_count=None,
                            )
                        ],
                    ),
                ],
                provenance=ManifestProvenance(build_timestamp_utc=datetime.now(timezone.utc)),
                assumptions=ExecutionAssumptions(),
            )

            manifest_path = snapshot_dir / "manifest.json"
            with open(manifest_path, "w") as f:
                json.dump(manifest.model_dump(mode="json"), f, default=str)

            # Create checksums file
            checksums_content = (
                f"{kline_hash}  bars/trade/interval=4h/symbol=BTCUSDT/month=2024-01.csv\n"
                f"{rules_hash}  metadata/symbol_rules/2024-01-01.json\n"
            )
            checksums_path = snapshot_dir / "checksums.sha256"
            checksums_path.write_text(checksums_content)

            # Run validation
            report = validate_snapshot(snapshot_dir, store_dir)

            assert report.overall_status == "PASS"
            assert all(r.status in ("PASS", "WARN") for r in report.results)
