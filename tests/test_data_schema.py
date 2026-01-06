"""Tests for candle timestamp schema and data loading."""

import tempfile
from pathlib import Path

import pandas as pd
import pytest

from src.backtester.data import _interval_to_ms, load_ohlcv_csv, load_symbol_interval


class TestIntervalToMs:
    """Test interval string to milliseconds conversion."""

    def test_minutes(self) -> None:
        assert _interval_to_ms("1m") == 60 * 1000
        assert _interval_to_ms("15m") == 15 * 60 * 1000
        assert _interval_to_ms("30m") == 30 * 60 * 1000

    def test_hours(self) -> None:
        assert _interval_to_ms("1h") == 60 * 60 * 1000
        assert _interval_to_ms("4h") == 4 * 60 * 60 * 1000
        assert _interval_to_ms("12h") == 12 * 60 * 60 * 1000

    def test_days(self) -> None:
        assert _interval_to_ms("1d") == 24 * 60 * 60 * 1000
        assert _interval_to_ms("3d") == 3 * 24 * 60 * 60 * 1000

    def test_weeks(self) -> None:
        assert _interval_to_ms("1w") == 7 * 24 * 60 * 60 * 1000

    def test_unknown_unit_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown interval unit"):
            _interval_to_ms("1x")


class TestCanonicalSchema:
    """Test loading CSVs with canonical schema (open_time + close_time)."""

    def test_canonical_schema_loads_correctly(self) -> None:
        """Canonical schema with both open_time and close_time should load without warnings."""
        import warnings

        csv_content = """open_time,close_time,open,high,low,close,volume
1704067200000,1704081599999,42000.00,42500.00,41800.00,42300.00,100.0
1704081600000,1704095999999,42300.00,42800.00,42100.00,42600.00,150.0"""

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write(csv_content)
            csv_path = Path(f.name)

        try:
            # Capture all warnings
            with warnings.catch_warnings(record=True) as record:
                warnings.simplefilter("always")
                df = load_ohlcv_csv(csv_path)

            # Filter for UserWarning about legacy schema
            legacy_warnings = [w for w in record if "Legacy schema" in str(w.message)]
            assert len(legacy_warnings) == 0, "Canonical schema should not emit legacy warnings"

            assert len(df) == 2
            assert "open_time" in df.columns
            assert df.index.name == "close_time"

            # Verify values
            assert df.iloc[0]["open"] == 42000.00
            assert df.iloc[1]["close"] == 42600.00
        finally:
            csv_path.unlink()

    def test_canonical_schema_preserves_open_time(self) -> None:
        """open_time column should be preserved and converted to datetime."""
        csv_content = """open_time,close_time,open,high,low,close,volume
1704067200000,1704081599999,42000.00,42500.00,41800.00,42300.00,100.0"""

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write(csv_content)
            csv_path = Path(f.name)

        try:
            df = load_ohlcv_csv(csv_path)

            # open_time should be a datetime
            assert pd.api.types.is_datetime64_any_dtype(df["open_time"])
            # Verify the timestamp (1704067200000 ms = 2024-01-01 00:00:00 UTC)
            expected_open = pd.Timestamp("2024-01-01 00:00:00", tz="UTC")
            assert df.iloc[0]["open_time"] == expected_open
        finally:
            csv_path.unlink()

    def test_canonical_schema_close_time_as_index(self) -> None:
        """close_time should be the DataFrame index."""
        csv_content = """open_time,close_time,open,high,low,close,volume
1704067200000,1704081599999,42000.00,42500.00,41800.00,42300.00,100.0"""

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write(csv_content)
            csv_path = Path(f.name)

        try:
            df = load_ohlcv_csv(csv_path)

            assert df.index.name == "close_time"
            # close_time = 1704081599999 ms = 2024-01-01 03:59:59.999 UTC
            expected_close = pd.Timestamp("2024-01-01 03:59:59.999", tz="UTC")
            assert df.index[0] == expected_close
        finally:
            csv_path.unlink()


class TestLegacyTimestampSchema:
    """Test loading CSVs with legacy timestamp column."""

    def test_timestamp_treated_as_close_time(self) -> None:
        """Legacy 'timestamp' column should be treated as close_time with warning."""
        csv_content = """timestamp,open,high,low,close,volume
1704081599999,42000.00,42500.00,41800.00,42300.00,100.0"""

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write(csv_content)
            csv_path = Path(f.name)

        try:
            with pytest.warns(UserWarning, match="Legacy schema.*timestamp"):
                df = load_ohlcv_csv(csv_path)

            assert df.index.name == "close_time"
            assert len(df) == 1
        finally:
            csv_path.unlink()

    def test_timestamp_without_interval_sets_open_time_nat(self) -> None:
        """Without interval, open_time should be NaT."""
        csv_content = """timestamp,open,high,low,close,volume
1704081599999,42000.00,42500.00,41800.00,42300.00,100.0"""

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write(csv_content)
            csv_path = Path(f.name)

        try:
            with pytest.warns(UserWarning):
                df = load_ohlcv_csv(csv_path)

            assert pd.isna(df.iloc[0]["open_time"])
        finally:
            csv_path.unlink()

    def test_timestamp_with_interval_derives_open_time(self) -> None:
        """With interval provided, open_time should be derived."""
        # close_time = 1704081599999 ms (2024-01-01 03:59:59.999 UTC)
        # For 4h interval: open_time = close_time - 4h + 1ms = 2024-01-01 00:00:00 UTC
        csv_content = """timestamp,open,high,low,close,volume
1704081599999,42000.00,42500.00,41800.00,42300.00,100.0"""

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write(csv_content)
            csv_path = Path(f.name)

        try:
            with pytest.warns(UserWarning):
                df = load_ohlcv_csv(csv_path, interval="4h")

            expected_open = pd.Timestamp("2024-01-01 00:00:00", tz="UTC")
            assert df.iloc[0]["open_time"] == expected_open
        finally:
            csv_path.unlink()


class TestLegacyOpenTimeSchema:
    """Test loading CSVs with only open_time column."""

    def test_open_time_requires_interval(self) -> None:
        """CSV with only open_time should require interval parameter."""
        csv_content = """open_time,open,high,low,close,volume
1704067200000,42000.00,42500.00,41800.00,42300.00,100.0"""

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write(csv_content)
            csv_path = Path(f.name)

        try:
            with pytest.raises(ValueError, match="interval.*required"):
                load_ohlcv_csv(csv_path)
        finally:
            csv_path.unlink()

    def test_open_time_with_interval_derives_close_time(self) -> None:
        """With interval provided, close_time should be derived from open_time."""
        # open_time = 1704067200000 ms (2024-01-01 00:00:00 UTC)
        # For 4h interval: close_time = open_time + 4h - 1ms = 2024-01-01 03:59:59.999 UTC
        csv_content = """open_time,open,high,low,close,volume
1704067200000,42000.00,42500.00,41800.00,42300.00,100.0"""

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write(csv_content)
            csv_path = Path(f.name)

        try:
            with pytest.warns(UserWarning, match="Legacy schema.*open_time"):
                df = load_ohlcv_csv(csv_path, interval="4h")

            assert df.index.name == "close_time"
            # close_time = open_time + 4h - 1ms
            expected_close = pd.Timestamp("2024-01-01 03:59:59.999", tz="UTC")
            assert df.index[0] == expected_close
        finally:
            csv_path.unlink()

    def test_open_time_preserves_original_value(self) -> None:
        """open_time column should preserve original value when deriving close_time."""
        csv_content = """open_time,open,high,low,close,volume
1704067200000,42000.00,42500.00,41800.00,42300.00,100.0"""

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write(csv_content)
            csv_path = Path(f.name)

        try:
            with pytest.warns(UserWarning):
                df = load_ohlcv_csv(csv_path, interval="4h")

            expected_open = pd.Timestamp("2024-01-01 00:00:00", tz="UTC")
            assert df.iloc[0]["open_time"] == expected_open
        finally:
            csv_path.unlink()


class TestMissingTimestampColumns:
    """Test error handling for missing timestamp columns."""

    def test_no_timestamp_column_raises(self) -> None:
        """CSV with no timestamp column should raise ValueError."""
        csv_content = """open,high,low,close,volume
42000.00,42500.00,41800.00,42300.00,100.0"""

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write(csv_content)
            csv_path = Path(f.name)

        try:
            with pytest.raises(ValueError, match="must contain at least one of"):
                load_ohlcv_csv(csv_path)
        finally:
            csv_path.unlink()

    def test_missing_ohlcv_columns_raises(self) -> None:
        """CSV missing OHLCV columns should raise ValueError."""
        csv_content = """close_time,open,high
1704081599999,42000.00,42500.00"""

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write(csv_content)
            csv_path = Path(f.name)

        try:
            with pytest.raises(ValueError, match="must contain open, high, low, close, volume"):
                load_ohlcv_csv(csv_path)
        finally:
            csv_path.unlink()


class TestDataSorting:
    """Test that data is sorted correctly by close_time."""

    def test_unsorted_data_gets_sorted(self) -> None:
        """Data should be sorted by close_time regardless of input order."""
        # Rows in reverse order
        csv_content = """open_time,close_time,open,high,low,close,volume
1704081600000,1704095999999,42300.00,42800.00,42100.00,42600.00,150.0
1704067200000,1704081599999,42000.00,42500.00,41800.00,42300.00,100.0"""

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write(csv_content)
            csv_path = Path(f.name)

        try:
            df = load_ohlcv_csv(csv_path)

            # First row should be earlier timestamp
            assert df.iloc[0]["open"] == 42000.00
            assert df.iloc[1]["open"] == 42300.00
        finally:
            csv_path.unlink()


class TestLoadSymbolInterval:
    """Test the load_symbol_interval convenience function."""

    def test_load_symbol_interval_passes_interval(self) -> None:
        """load_symbol_interval should pass interval to loader for legacy support."""
        # Create a legacy open_time only CSV
        csv_content = """open_time,open,high,low,close,volume
1704067200000,42000.00,42500.00,41800.00,42300.00,100.0"""

        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "BTCUSDT_4h.csv"
            csv_path.write_text(csv_content)

            with pytest.warns(UserWarning, match="Legacy schema"):
                df = load_symbol_interval(tmpdir, "BTCUSDT", "4h")

            # Should successfully derive close_time
            assert df.index.name == "close_time"
            assert len(df) == 1

    def test_load_symbol_interval_file_not_found(self) -> None:
        """Should raise error if file doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with pytest.raises(FileNotFoundError):
                load_symbol_interval(tmpdir, "NONEXISTENT", "4h")


class TestNaHandling:
    """Test handling of missing/invalid data."""

    def test_rows_with_missing_ohlcv_dropped(self) -> None:
        """Rows with missing OHLCV values should be dropped."""
        csv_content = """open_time,close_time,open,high,low,close,volume
1704067200000,1704081599999,42000.00,42500.00,41800.00,42300.00,100.0
1704081600000,1704095999999,,42800.00,42100.00,42600.00,150.0
1704096000000,1704109599999,42600.00,43000.00,42400.00,42800.00,200.0"""

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write(csv_content)
            csv_path = Path(f.name)

        try:
            df = load_ohlcv_csv(csv_path)

            # Middle row should be dropped due to missing open
            assert len(df) == 2
            assert df.iloc[0]["open"] == 42000.00
            assert df.iloc[1]["open"] == 42600.00
        finally:
            csv_path.unlink()

    def test_rows_with_invalid_close_time_dropped(self) -> None:
        """Rows with invalid close_time should be dropped."""
        csv_content = """open_time,close_time,open,high,low,close,volume
1704067200000,1704081599999,42000.00,42500.00,41800.00,42300.00,100.0
1704081600000,invalid,42300.00,42800.00,42100.00,42600.00,150.0"""

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write(csv_content)
            csv_path = Path(f.name)

        try:
            df = load_ohlcv_csv(csv_path)

            # Second row should be dropped due to invalid close_time
            assert len(df) == 1
        finally:
            csv_path.unlink()


class TestCaseInsensitiveColumns:
    """Test that column names are case-insensitive."""

    def test_uppercase_columns(self) -> None:
        """Uppercase column names should work."""
        csv_content = """OPEN_TIME,CLOSE_TIME,OPEN,HIGH,LOW,CLOSE,VOLUME
1704067200000,1704081599999,42000.00,42500.00,41800.00,42300.00,100.0"""

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write(csv_content)
            csv_path = Path(f.name)

        try:
            df = load_ohlcv_csv(csv_path)

            assert len(df) == 1
            assert df.iloc[0]["open"] == 42000.00
        finally:
            csv_path.unlink()

    def test_mixed_case_columns(self) -> None:
        """Mixed case column names should work."""
        csv_content = """Open_Time,Close_Time,Open,High,Low,Close,Volume
1704067200000,1704081599999,42000.00,42500.00,41800.00,42300.00,100.0"""

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write(csv_content)
            csv_path = Path(f.name)

        try:
            df = load_ohlcv_csv(csv_path)

            assert len(df) == 1
        finally:
            csv_path.unlink()
