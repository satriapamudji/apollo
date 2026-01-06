"""CLI tool to validate and normalize historical kline CSVs.

Usage:
    python -m src.tools.normalize_klines --input data/market/BTCUSDT_4h.csv --interval 4h
    python -m src.tools.normalize_klines --input-dir data/market --output-dir data/market_normalized

This tool validates kline data for:
- Required columns (open_time/close_time or legacy equivalents)
- Monotonic time ordering
- Consistent interval spacing
- Duplicate detection

Non-destructive: never overwrites original files.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

import pandas as pd


class ValidationIssue(NamedTuple):
    """A validation issue found in the data."""

    severity: str  # 'error', 'warning', 'info'
    message: str
    row_index: int | None = None


@dataclass
class ValidationResult:
    """Result of validating a kline CSV file."""

    path: Path
    is_valid: bool
    issues: list[ValidationIssue]
    row_count: int
    schema_type: str  # 'canonical', 'legacy_timestamp', 'legacy_open_time'

    def summary(self) -> str:
        """Return a human-readable summary."""
        errors = [i for i in self.issues if i.severity == "error"]
        warnings = [i for i in self.issues if i.severity == "warning"]
        status = "VALID" if self.is_valid else "INVALID"
        return (
            f"{self.path.name}: {status} ({self.row_count} rows, {self.schema_type})\n"
            f"  Errors: {len(errors)}, Warnings: {len(warnings)}"
        )


def _interval_to_ms(interval: str) -> int:
    """Convert interval string (e.g., '4h', '1d') to milliseconds."""
    multipliers = {
        "m": 60 * 1000,
        "h": 60 * 60 * 1000,
        "d": 24 * 60 * 60 * 1000,
        "w": 7 * 24 * 60 * 60 * 1000,
    }
    unit = interval[-1].lower()
    value = int(interval[:-1])
    if unit not in multipliers:
        raise ValueError(f"Unknown interval unit: {unit}")
    return value * multipliers[unit]


def _detect_schema(df: pd.DataFrame) -> str:
    """Detect the schema type of the DataFrame."""
    columns_lower = {c.lower() for c in df.columns}
    if "close_time" in columns_lower:
        return "canonical"
    elif "timestamp" in columns_lower:
        return "legacy_timestamp"
    elif "open_time" in columns_lower:
        return "legacy_open_time"
    else:
        return "unknown"


def validate_klines(
    path: Path,
    interval: str | None = None,
) -> ValidationResult:
    """Validate a kline CSV file.

    Args:
        path: Path to the CSV file
        interval: Expected interval (e.g., '4h'). If None, inferred from filename.

    Returns:
        ValidationResult with issues found
    """
    issues: list[ValidationIssue] = []

    # Try to infer interval from filename if not provided
    if interval is None:
        # Pattern: SYMBOL_INTERVAL.csv (e.g., BTCUSDT_4h.csv)
        stem = path.stem
        if "_" in stem:
            inferred_interval = stem.split("_")[-1]
            if inferred_interval[-1].lower() in "mhdw":
                interval = inferred_interval
                issues.append(
                    ValidationIssue("info", f"Inferred interval from filename: {interval}")
                )

    # Load data
    try:
        df = pd.read_csv(path)
    except Exception as e:
        return ValidationResult(
            path=path,
            is_valid=False,
            issues=[ValidationIssue("error", f"Failed to read CSV: {e}")],
            row_count=0,
            schema_type="unknown",
        )

    if df.empty:
        return ValidationResult(
            path=path,
            is_valid=False,
            issues=[ValidationIssue("error", "CSV is empty")],
            row_count=0,
            schema_type="unknown",
        )

    # Detect schema
    schema_type = _detect_schema(df)
    if schema_type == "unknown":
        issues.append(
            ValidationIssue(
                "error",
                "No timestamp column found. Expected: close_time, timestamp, or open_time",
            )
        )
        return ValidationResult(
            path=path,
            is_valid=False,
            issues=issues,
            row_count=len(df),
            schema_type=schema_type,
        )

    # Check required OHLCV columns
    columns_lower = {c.lower(): c for c in df.columns}
    required_ohlcv = ["open", "high", "low", "close", "volume"]
    missing_ohlcv = [c for c in required_ohlcv if c not in columns_lower]
    if missing_ohlcv:
        issues.append(ValidationIssue("error", f"Missing required columns: {missing_ohlcv}"))

    # Get timestamp column for validation
    if schema_type == "canonical":
        ts_col = columns_lower.get("close_time", "close_time")
    elif schema_type == "legacy_timestamp":
        ts_col = columns_lower.get("timestamp", "timestamp")
    else:
        ts_col = columns_lower.get("open_time", "open_time")

    # Convert timestamp to numeric for analysis
    ts_values = pd.to_numeric(df[ts_col], errors="coerce")
    ts_series = pd.Series(ts_values)

    # Check for non-numeric timestamps
    invalid_ts = ts_series.isna().sum()
    if invalid_ts > 0:
        issues.append(
            ValidationIssue("warning", f"{invalid_ts} rows have invalid/missing timestamps")
        )

    # Filter to valid timestamps
    valid_mask = ts_series.notna()
    ts_valid: pd.Series = pd.Series(ts_series[valid_mask])

    if len(ts_valid) < 2:
        issues.append(ValidationIssue("error", "Not enough valid timestamps for validation"))
        return ValidationResult(
            path=path,
            is_valid=len([i for i in issues if i.severity == "error"]) == 0,
            issues=issues,
            row_count=len(df),
            schema_type=schema_type,
        )

    # Check monotonic ordering
    if not ts_valid.is_monotonic_increasing:
        # Find first violation
        diffs: pd.Series = ts_valid.diff()
        non_increasing = diffs[diffs <= 0]
        if len(non_increasing) > 0:
            first_idx = int(non_increasing.index[0])
            issues.append(
                ValidationIssue(
                    "error",
                    f"Timestamps not monotonically increasing at row {first_idx}",
                    first_idx,
                )
            )

    # Check for duplicates
    duplicates: pd.Series = ts_valid[ts_valid.duplicated()]
    if len(duplicates) > 0:
        issues.append(ValidationIssue("warning", f"Found {len(duplicates)} duplicate timestamps"))

    # Check interval consistency if interval is known
    if interval is not None:
        expected_ms = _interval_to_ms(interval)
        diffs = ts_valid.diff().dropna()

        # Check for inconsistent spacing
        incorrect_spacing: pd.Series = diffs[diffs != expected_ms]
        if len(incorrect_spacing) > 0:
            # Allow for small tolerance (1ms for rounding)
            significant_gaps: pd.Series = incorrect_spacing[
                abs(incorrect_spacing - expected_ms) > 1
            ]
            if len(significant_gaps) > 0:
                gap_count = len(significant_gaps)
                first_gap_idx = int(significant_gaps.index[0])
                first_gap_ms = int(significant_gaps.iloc[0])
                issues.append(
                    ValidationIssue(
                        "warning",
                        f"{gap_count} intervals have unexpected spacing. "
                        f"First at row {first_gap_idx}: {first_gap_ms}ms "
                        f"(expected {expected_ms}ms)",
                        first_gap_idx,
                    )
                )

        # Detect missing bars (gaps > expected interval)
        gaps: pd.Series = diffs[diffs > expected_ms * 1.5]
        if len(gaps) > 0:
            gap_count = len(gaps)
            issues.append(
                ValidationIssue(
                    "warning",
                    f"Detected {gap_count} potential missing bars (large gaps in data)",
                )
            )

    # Check legacy schema compatibility
    if schema_type == "legacy_open_time" and interval is None:
        issues.append(
            ValidationIssue(
                "warning",
                "Legacy open_time schema detected. "
                "Provide --interval to enable close_time derivation.",
            )
        )

    is_valid = all(i.severity != "error" for i in issues)
    return ValidationResult(
        path=path,
        is_valid=is_valid,
        issues=issues,
        row_count=len(df),
        schema_type=schema_type,
    )


def normalize_klines(
    path: Path,
    output_path: Path,
    interval: str,
) -> tuple[bool, list[str]]:
    """Normalize a kline CSV to canonical schema.

    Args:
        path: Input CSV path
        output_path: Output CSV path
        interval: Candle interval (e.g., '4h')

    Returns:
        Tuple of (success, messages)
    """
    messages: list[str] = []
    interval_ms = _interval_to_ms(interval)

    try:
        df = pd.read_csv(path)
    except Exception as e:
        return False, [f"Failed to read CSV: {e}"]

    columns_lower = {c.lower(): c for c in df.columns}
    schema_type = _detect_schema(df)

    # Rename OHLCV to lowercase
    required_ohlcv = ["open", "high", "low", "close", "volume"]
    rename_map = {}
    for col in required_ohlcv:
        if col in columns_lower:
            rename_map[columns_lower[col]] = col

    if schema_type == "canonical":
        # Already canonical, just normalize column names
        rename_map[columns_lower["close_time"]] = "close_time"
        if "open_time" in columns_lower:
            rename_map[columns_lower["open_time"]] = "open_time"
        df = df.rename(columns=rename_map)

        # Derive open_time if missing
        if "open_time" not in df.columns:
            messages.append("Deriving open_time from close_time")
            close_time_ms = pd.to_numeric(df["close_time"], errors="coerce")
            df["open_time"] = close_time_ms - interval_ms + 1

    elif schema_type == "legacy_timestamp":
        # timestamp is close_time
        messages.append("Converting legacy 'timestamp' schema to canonical")
        rename_map[columns_lower["timestamp"]] = "close_time"
        df = df.rename(columns=rename_map)

        # Derive open_time
        close_time_ms = pd.to_numeric(df["close_time"], errors="coerce")
        df["open_time"] = close_time_ms - interval_ms + 1

    elif schema_type == "legacy_open_time":
        # Need to derive close_time
        messages.append("Converting legacy 'open_time' schema to canonical")
        rename_map[columns_lower["open_time"]] = "open_time"
        df = df.rename(columns=rename_map)

        # Derive close_time
        open_time_ms = pd.to_numeric(df["open_time"], errors="coerce")
        df["close_time"] = open_time_ms + interval_ms - 1

    else:
        return False, ["Unknown schema - cannot normalize"]

    # Convert numeric columns
    for col in required_ohlcv:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Ensure integer timestamps (ms)
    df["open_time"] = df["open_time"].astype("Int64")
    df["close_time"] = df["close_time"].astype("Int64")

    # Drop invalid rows
    original_len = len(df)
    df = df.dropna(subset=["close_time"] + required_ohlcv)
    dropped = original_len - len(df)
    if dropped > 0:
        messages.append(f"Dropped {dropped} invalid rows")

    # Sort by close_time
    df = df.sort_values("close_time").reset_index(drop=True)

    # Remove duplicates
    duplicates = df["close_time"].duplicated()
    if duplicates.any():
        dup_count = int(duplicates.sum())
        messages.append(f"Removed {dup_count} duplicate rows")
        df = df[~duplicates]

    # Select canonical columns
    output_cols = ["open_time", "close_time", "open", "high", "low", "close", "volume"]
    output_df = df[output_cols]

    # Write output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_df.to_csv(output_path, index=False)
    messages.append(f"Wrote {len(output_df)} rows to {output_path}")

    return True, messages


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate and normalize kline CSV files to canonical schema."
    )
    parser.add_argument(
        "--input",
        type=Path,
        help="Single input CSV file to process",
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        help="Directory of CSV files to process",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("./data/market_normalized"),
        help="Output directory for normalized files. Default: ./data/market_normalized",
    )
    parser.add_argument(
        "--interval",
        help="Candle interval (e.g., '4h', '1d'). If not provided, inferred from filename.",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Only validate, do not normalize",
    )
    args = parser.parse_args()

    if not args.input and not args.input_dir:
        parser.error("Either --input or --input-dir is required")

    # Collect files to process
    files: list[tuple[Path, str | None]] = []
    if args.input:
        files.append((args.input, args.interval))
    if args.input_dir:
        for csv_file in args.input_dir.glob("*.csv"):
            files.append((csv_file, args.interval))

    if not files:
        print("No CSV files found to process")
        return

    # Process files
    all_valid = True
    for file_path, interval in files:
        print(f"\n{'=' * 60}")
        print(f"Processing: {file_path}")

        # Validate
        result = validate_klines(file_path, interval)
        print(result.summary())

        for issue in result.issues:
            prefix = {"error": "ERROR", "warning": "WARN", "info": "INFO"}[issue.severity]
            row_info = f" (row {issue.row_index})" if issue.row_index is not None else ""
            print(f"  [{prefix}]{row_info}: {issue.message}")

        if not result.is_valid:
            all_valid = False
            continue

        # Normalize if requested
        if not args.validate_only:
            # Determine interval
            file_interval = interval
            if file_interval is None:
                # Try to infer from filename
                stem = file_path.stem
                if "_" in stem:
                    inferred = stem.split("_")[-1]
                    if inferred[-1].lower() in "mhdw":
                        file_interval = inferred

            if file_interval is None:
                print("  [SKIP] Cannot normalize: interval unknown. Use --interval.")
                continue

            output_path = args.output_dir / file_path.name
            success, messages = normalize_klines(file_path, output_path, file_interval)

            for msg in messages:
                print(f"  {msg}")

            if not success:
                all_valid = False

    print(f"\n{'=' * 60}")
    print(f"Summary: {'All files valid' if all_valid else 'Some files have issues'}")


if __name__ == "__main__":
    main()
