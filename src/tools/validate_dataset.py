"""CLI tool to validate a dataset snapshot for reproducible backtesting.

Usage:
    python -m src.tools.validate_dataset \
        --snapshot ./data/datasets/usdm/snapshots/20240101_to_20240601_abc12345

This tool verifies:
1. All checksums match the actual files
2. Required schema columns are present in CSV files
3. No missing data ranges/gaps
4. Symbol rules are present and valid
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from src.data.models import DatasetManifest


@dataclass
class ValidationResult:
    """Result of a single validation check."""

    check_name: str
    status: Literal["PASS", "FAIL", "WARN"]
    message: str
    details: list[str] | None = None


@dataclass
class ValidationReport:
    """Complete validation report for a snapshot."""

    snapshot_id: str
    validated_at: datetime
    overall_status: Literal["PASS", "FAIL"]
    results: list[ValidationResult]

    def summary(self) -> str:
        """Generate a summary string."""
        lines = [
            "=== Validation Report ===",
            f"Snapshot: {self.snapshot_id}",
            f"Validated at: {self.validated_at.isoformat()}",
            f"Overall status: {self.overall_status}",
            "",
        ]

        pass_count = sum(1 for r in self.results if r.status == "PASS")
        fail_count = sum(1 for r in self.results if r.status == "FAIL")
        warn_count = sum(1 for r in self.results if r.status == "WARN")

        lines.append(f"Results: {pass_count} PASS, {fail_count} FAIL, {warn_count} WARN")
        lines.append("")

        for result in self.results:
            status_icon = {"PASS": "✓", "FAIL": "✗", "WARN": "⚠"}[result.status]
            lines.append(f"[{status_icon}] {result.check_name}: {result.message}")
            if result.details:
                for detail in result.details[:5]:
                    lines.append(f"    - {detail}")
                if len(result.details) > 5:
                    lines.append(f"    ... and {len(result.details) - 5} more")

        return "\n".join(lines)


def compute_sha256(file_path: Path) -> str:
    """Compute SHA256 hash of a file."""
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256_hash.update(chunk)
    return sha256_hash.hexdigest()


def load_manifest(snapshot_dir: Path) -> DatasetManifest:
    """Load manifest from snapshot directory."""
    manifest_path = snapshot_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")

    with open(manifest_path) as f:
        data = json.load(f)
    return DatasetManifest.model_validate(data)


def load_checksums(snapshot_dir: Path) -> dict[str, str]:
    """Load checksums from snapshot directory.

    Returns:
        Dict mapping relative path to SHA256 hash
    """
    checksums_path = snapshot_dir / "checksums.sha256"
    if not checksums_path.exists():
        raise FileNotFoundError(f"Checksums file not found: {checksums_path}")

    checksums: dict[str, str] = {}
    with open(checksums_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            # Format: "hash  path" (two spaces)
            parts = line.split("  ", 1)
            if len(parts) == 2:
                checksums[parts[1]] = parts[0]

    return checksums


def validate_checksums(
    manifest: DatasetManifest,
    checksums: dict[str, str],
    store_dir: Path,
) -> ValidationResult:
    """Validate that all file checksums match."""
    mismatches: list[str] = []
    missing_files: list[str] = []
    verified_count = 0

    for artifact_range in manifest.artifacts:
        for artifact in artifact_range.files:
            file_path = store_dir / artifact.path
            expected_hash = checksums.get(artifact.path, artifact.sha256)

            if not file_path.exists():
                missing_files.append(artifact.path)
                continue

            actual_hash = compute_sha256(file_path)
            if actual_hash != expected_hash:
                mismatches.append(
                    f"{artifact.path}: expected {expected_hash[:12]}..., got {actual_hash[:12]}..."
                )
            else:
                verified_count += 1

    if missing_files or mismatches:
        details = []
        if missing_files:
            details.extend([f"Missing: {f}" for f in missing_files])
        if mismatches:
            details.extend(mismatches)

        return ValidationResult(
            check_name="Checksum Verification",
            status="FAIL",
            message=f"{len(missing_files)} missing, {len(mismatches)} mismatches",
            details=details,
        )

    return ValidationResult(
        check_name="Checksum Verification",
        status="PASS",
        message=f"All {verified_count} files verified",
    )


# Required columns for each file type
KLINE_REQUIRED_COLUMNS = {"open_time", "open", "high", "low", "close", "volume", "close_time"}
FUNDING_REQUIRED_COLUMNS = {"funding_time", "symbol", "funding_rate"}


def validate_csv_schema(file_path: Path, required_columns: set[str]) -> list[str]:
    """Validate that a CSV file has required columns.

    Returns:
        List of missing column names (empty if all present)
    """
    with open(file_path) as f:
        reader = csv.reader(f)
        try:
            header = next(reader)
        except StopIteration:
            return ["<empty file>"]

    actual_columns = set(header)
    missing = required_columns - actual_columns
    return list(missing)


def validate_schemas(
    manifest: DatasetManifest,
    store_dir: Path,
) -> ValidationResult:
    """Validate that all CSV files have required schema columns."""
    schema_errors: list[str] = []

    for artifact_range in manifest.artifacts:
        # Determine required columns based on artifact type
        if artifact_range.artifact_type == "klines":
            required = KLINE_REQUIRED_COLUMNS
        elif artifact_range.artifact_type == "funding":
            required = FUNDING_REQUIRED_COLUMNS
        else:
            continue  # Skip metadata files

        for artifact in artifact_range.files:
            file_path = store_dir / artifact.path
            if not file_path.exists():
                continue  # Already caught by checksum validation

            if file_path.suffix != ".csv":
                continue

            missing = validate_csv_schema(file_path, required)
            if missing:
                schema_errors.append(f"{artifact.path}: missing columns {missing}")

    if schema_errors:
        return ValidationResult(
            check_name="Schema Validation",
            status="FAIL",
            message=f"{len(schema_errors)} schema errors",
            details=schema_errors,
        )

    return ValidationResult(
        check_name="Schema Validation",
        status="PASS",
        message="All CSV files have required columns",
    )


def validate_data_gaps(manifest: DatasetManifest) -> ValidationResult:
    """Check for data gaps recorded in the manifest."""
    gaps = manifest.gaps_detected

    if gaps:
        return ValidationResult(
            check_name="Data Gap Detection",
            status="WARN",
            message=f"{len(gaps)} data gaps detected",
            details=gaps,
        )

    return ValidationResult(
        check_name="Data Gap Detection",
        status="PASS",
        message="No data gaps detected",
    )


def validate_symbol_rules(
    manifest: DatasetManifest,
    store_dir: Path,
) -> ValidationResult:
    """Validate that symbol rules are present and valid."""
    # Find exchange_info artifacts
    exchange_info_artifacts = [a for a in manifest.artifacts if a.artifact_type == "exchange_info"]

    if not exchange_info_artifacts:
        return ValidationResult(
            check_name="Symbol Rules Validation",
            status="FAIL",
            message="No exchange_info artifacts found",
        )

    rules_found = False
    errors: list[str] = []

    for artifact_range in exchange_info_artifacts:
        for artifact in artifact_range.files:
            if "symbol_rules" not in artifact.path:
                continue

            file_path = store_dir / artifact.path
            if not file_path.exists():
                errors.append(f"Symbol rules file missing: {artifact.path}")
                continue

            try:
                with open(file_path) as f:
                    data = json.load(f)
                rules = data.get("rules", [])
                if rules:
                    rules_found = True
                    # Validate required fields in first rule
                    sample_rule = rules[0]
                    required_fields = {
                        "symbol",
                        "tick_size",
                        "step_size",
                        "min_qty",
                        "min_notional",
                    }
                    missing = required_fields - set(sample_rule.keys())
                    if missing:
                        errors.append(f"Symbol rules missing fields: {missing}")
            except (json.JSONDecodeError, KeyError) as e:
                errors.append(f"Error parsing {artifact.path}: {e}")

    if errors:
        return ValidationResult(
            check_name="Symbol Rules Validation",
            status="FAIL",
            message=f"{len(errors)} errors found",
            details=errors,
        )

    if not rules_found:
        return ValidationResult(
            check_name="Symbol Rules Validation",
            status="WARN",
            message="No symbol rules files found",
        )

    return ValidationResult(
        check_name="Symbol Rules Validation",
        status="PASS",
        message="Symbol rules present and valid",
    )


def validate_manifest_consistency(manifest: DatasetManifest) -> ValidationResult:
    """Validate internal consistency of the manifest."""
    errors: list[str] = []

    # Check that symbols in artifacts match manifest symbols
    manifest_symbols = set(manifest.symbols)

    for artifact_range in manifest.artifacts:
        if artifact_range.symbol and artifact_range.symbol not in manifest_symbols:
            errors.append(f"Artifact symbol {artifact_range.symbol} not in manifest symbols")

    # Check that intervals in artifacts match manifest intervals
    manifest_intervals = set(manifest.intervals)

    for artifact_range in manifest.artifacts:
        if artifact_range.interval and artifact_range.interval not in manifest_intervals:
            errors.append(f"Artifact interval {artifact_range.interval} not in manifest intervals")

    if errors:
        return ValidationResult(
            check_name="Manifest Consistency",
            status="FAIL",
            message=f"{len(errors)} consistency errors",
            details=errors,
        )

    return ValidationResult(
        check_name="Manifest Consistency",
        status="PASS",
        message="Manifest is internally consistent",
    )


def validate_snapshot(
    snapshot_dir: Path,
    store_dir: Path | None = None,
) -> ValidationReport:
    """Run all validations on a snapshot.

    Args:
        snapshot_dir: Path to the snapshot directory
        store_dir: Path to the shared store directory (defaults to parent's parent/store)

    Returns:
        Complete validation report
    """
    validated_at = datetime.utcnow()
    results: list[ValidationResult] = []

    # Infer store_dir if not provided
    if store_dir is None:
        store_dir = snapshot_dir.parent.parent / "store"

    # Load manifest
    try:
        manifest = load_manifest(snapshot_dir)
    except FileNotFoundError as e:
        return ValidationReport(
            snapshot_id="<unknown>",
            validated_at=validated_at,
            overall_status="FAIL",
            results=[
                ValidationResult(
                    check_name="Manifest Load",
                    status="FAIL",
                    message=str(e),
                )
            ],
        )

    # Load checksums
    try:
        checksums = load_checksums(snapshot_dir)
    except FileNotFoundError as e:
        return ValidationReport(
            snapshot_id=manifest.snapshot_id,
            validated_at=validated_at,
            overall_status="FAIL",
            results=[
                ValidationResult(
                    check_name="Checksums Load",
                    status="FAIL",
                    message=str(e),
                )
            ],
        )

    # Run validations
    results.append(validate_manifest_consistency(manifest))
    results.append(validate_checksums(manifest, checksums, store_dir))
    results.append(validate_schemas(manifest, store_dir))
    results.append(validate_data_gaps(manifest))
    results.append(validate_symbol_rules(manifest, store_dir))

    # Determine overall status
    has_failures = any(r.status == "FAIL" for r in results)
    overall_status: Literal["PASS", "FAIL"] = "FAIL" if has_failures else "PASS"

    return ValidationReport(
        snapshot_id=manifest.snapshot_id,
        validated_at=validated_at,
        overall_status=overall_status,
        results=results,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate a dataset snapshot for reproducible backtesting."
    )
    parser.add_argument(
        "--snapshot",
        type=Path,
        required=True,
        help="Path to snapshot directory",
    )
    parser.add_argument(
        "--store-dir",
        type=Path,
        default=None,
        help="Path to shared store directory (default: inferred from snapshot path)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output report as JSON",
    )
    args = parser.parse_args()

    report = validate_snapshot(
        snapshot_dir=args.snapshot,
        store_dir=args.store_dir,
    )

    if args.json:
        # Output as JSON
        report_dict = {
            "snapshot_id": report.snapshot_id,
            "validated_at": report.validated_at.isoformat(),
            "overall_status": report.overall_status,
            "results": [
                {
                    "check_name": r.check_name,
                    "status": r.status,
                    "message": r.message,
                    "details": r.details,
                }
                for r in report.results
            ],
        }
        print(json.dumps(report_dict, indent=2))
    else:
        # Output as text
        print(report.summary())

    # Exit with appropriate code
    if report.overall_status == "FAIL":
        exit(1)
    exit(0)


if __name__ == "__main__":
    main()
