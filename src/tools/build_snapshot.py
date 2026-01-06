"""CLI tool to build an immutable snapshot for reproducible backtesting.

Usage:
    python -m src.tools.build_snapshot \
        --universe ./data/datasets/usdm/store/universe/universe_2024-01-01.json \
        --start 2024-01-01 \
        --end 2024-06-01 \
        --intervals 4h \
        --store-dir ./data/datasets/usdm/store \
        --output-dir ./data/datasets/usdm/snapshots

This tool creates a manifest.json and checksums.sha256 file that pins
the exact store files needed for a reproducible backtest run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from src.data.models import (
    ArtifactTimeRange,
    ChecksumEntry,
    ChecksumFile,
    DatasetManifest,
    ExecutionAssumptions,
    ManifestProvenance,
    StoreArtifact,
    TimeRange,
    UniverseSnapshot,
)


def parse_date(date_str: str) -> datetime:
    """Parse date string in YYYY-MM-DD format to datetime."""
    return datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def get_month_range(start_date: datetime, end_date: datetime) -> list[str]:
    """Get list of month keys (YYYY-MM) between start and end dates."""
    months = []
    current = start_date.replace(day=1)
    while current <= end_date:
        months.append(current.strftime("%Y-%m"))
        # Move to next month
        if current.month == 12:
            current = current.replace(year=current.year + 1, month=1)
        else:
            current = current.replace(month=current.month + 1)
    return months


def compute_sha256(file_path: Path) -> str:
    """Compute SHA256 hash of a file."""
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256_hash.update(chunk)
    return sha256_hash.hexdigest()


def count_csv_rows(file_path: Path) -> int:
    """Count rows in a CSV file (excluding header)."""
    with open(file_path) as f:
        # Subtract 1 for header
        return sum(1 for _ in f) - 1


def load_universe(universe_path: Path) -> UniverseSnapshot:
    """Load a universe snapshot from JSON file."""
    with open(universe_path) as f:
        data = json.load(f)
    return UniverseSnapshot.model_validate(data)


def find_store_files(
    store_dir: Path,
    symbols: list[str],
    intervals: list[str],
    start_date: datetime,
    end_date: datetime,
    include_funding: bool = True,
) -> dict[str, list[Path]]:
    """Find all relevant store files for the given parameters.

    Returns:
        Dict with keys 'klines', 'funding', 'metadata' mapping to file paths
    """
    months = get_month_range(start_date, end_date)

    files: dict[str, list[Path]] = {
        "klines": [],
        "funding": [],
        "metadata": [],
    }

    # Find kline files
    for symbol in symbols:
        for interval in intervals:
            for month in months:
                kline_path = (
                    store_dir
                    / "bars"
                    / "trade"
                    / f"interval={interval}"
                    / f"symbol={symbol}"
                    / f"month={month}.csv"
                )
                if kline_path.exists():
                    files["klines"].append(kline_path)

    # Find funding files
    if include_funding:
        for symbol in symbols:
            for month in months:
                funding_path = store_dir / "funding" / f"symbol={symbol}" / f"month={month}.csv"
                if funding_path.exists():
                    files["funding"].append(funding_path)

    # Find metadata files (symbol rules)
    rules_dir = store_dir / "metadata" / "symbol_rules"
    if rules_dir.exists():
        for rules_file in rules_dir.glob("*.json"):
            files["metadata"].append(rules_file)

    return files


def create_artifact_refs(
    files: list[Path],
    store_dir: Path,
) -> list[StoreArtifact]:
    """Create StoreArtifact references for a list of files."""
    artifacts = []
    for file_path in files:
        rel_path = file_path.relative_to(store_dir)
        sha256 = compute_sha256(file_path)
        size_bytes = file_path.stat().st_size

        row_count = None
        if file_path.suffix == ".csv":
            row_count = count_csv_rows(file_path)

        artifacts.append(
            StoreArtifact(
                path=str(rel_path),
                sha256=sha256,
                size_bytes=size_bytes,
                row_count=row_count,
            )
        )

    return artifacts


def build_snapshot(
    universe_path: Path,
    start_date: datetime,
    end_date: datetime,
    intervals: list[str],
    store_dir: Path,
    output_dir: Path,
    include_funding: bool = True,
    snapshot_id: str | None = None,
) -> Path:
    """Build an immutable snapshot with manifest and checksums.

    Args:
        universe_path: Path to universe snapshot JSON
        start_date: Start date for the snapshot
        end_date: End date for the snapshot
        intervals: List of kline intervals to include
        store_dir: Path to the shared store directory
        output_dir: Path to snapshots directory
        include_funding: Whether to include funding data
        snapshot_id: Optional snapshot ID (auto-generated if not provided)

    Returns:
        Path to the created snapshot directory
    """
    created_at = datetime.now(timezone.utc)

    # Load universe
    universe = load_universe(universe_path)
    symbols = [s.symbol for s in universe.symbols]

    print(f"Building snapshot for {len(symbols)} symbols: {symbols}")
    print(f"Time range: {start_date.date()} to {end_date.date()}")
    print(f"Intervals: {intervals}")

    # Generate snapshot ID
    if snapshot_id is None:
        date_range = f"{start_date.strftime('%Y%m%d')}_to_{end_date.strftime('%Y%m%d')}"
        snapshot_id = f"{date_range}_{uuid4().hex[:8]}"

    # Find all store files
    files = find_store_files(
        store_dir=store_dir,
        symbols=symbols,
        intervals=intervals,
        start_date=start_date,
        end_date=end_date,
        include_funding=include_funding,
    )

    print("\nFound store files:")
    print(f"  Klines: {len(files['klines'])}")
    print(f"  Funding: {len(files['funding'])}")
    print(f"  Metadata: {len(files['metadata'])}")

    # Check for missing data
    gaps_detected: list[str] = []
    expected_months = get_month_range(start_date, end_date)

    for symbol in symbols:
        for interval in intervals:
            for month in expected_months:
                expected_path = (
                    store_dir
                    / "bars"
                    / "trade"
                    / f"interval={interval}"
                    / f"symbol={symbol}"
                    / f"month={month}.csv"
                )
                if not expected_path.exists():
                    gaps_detected.append(f"Missing klines: {symbol} {interval} {month}")

        if include_funding:
            for month in expected_months:
                expected_path = store_dir / "funding" / f"symbol={symbol}" / f"month={month}.csv"
                if not expected_path.exists():
                    gaps_detected.append(f"Missing funding: {symbol} {month}")

    if gaps_detected:
        print(f"\nWarning: {len(gaps_detected)} data gaps detected:")
        for gap in gaps_detected[:5]:
            print(f"  - {gap}")
        if len(gaps_detected) > 5:
            print(f"  ... and {len(gaps_detected) - 5} more")

    # Create artifact references
    kline_artifacts = create_artifact_refs(files["klines"], store_dir)
    funding_artifacts = create_artifact_refs(files["funding"], store_dir)
    metadata_artifacts = create_artifact_refs(files["metadata"], store_dir)

    # Build artifact time ranges
    artifact_ranges: list[ArtifactTimeRange] = []

    # Group kline artifacts by symbol/interval
    for interval in intervals:
        for symbol in symbols:
            symbol_artifacts = [
                a
                for a in kline_artifacts
                if f"interval={interval}" in a.path and f"symbol={symbol}" in a.path
            ]
            if symbol_artifacts:
                artifact_ranges.append(
                    ArtifactTimeRange(
                        artifact_type="klines",
                        symbol=symbol,
                        interval=interval,
                        time_range=TimeRange(
                            start=start_date.strftime("%Y-%m-%d"),
                            end=end_date.strftime("%Y-%m-%d"),
                        ),
                        files=symbol_artifacts,
                    )
                )

    # Group funding artifacts by symbol
    if include_funding:
        for symbol in symbols:
            symbol_artifacts = [a for a in funding_artifacts if f"symbol={symbol}" in a.path]
            if symbol_artifacts:
                artifact_ranges.append(
                    ArtifactTimeRange(
                        artifact_type="funding",
                        symbol=symbol,
                        interval=None,
                        time_range=TimeRange(
                            start=start_date.strftime("%Y-%m-%d"),
                            end=end_date.strftime("%Y-%m-%d"),
                        ),
                        files=symbol_artifacts,
                    )
                )

    # Add metadata artifacts
    if metadata_artifacts:
        artifact_ranges.append(
            ArtifactTimeRange(
                artifact_type="exchange_info",
                symbol=None,
                interval=None,
                time_range=TimeRange(
                    start=start_date.strftime("%Y-%m-%d"),
                    end=end_date.strftime("%Y-%m-%d"),
                ),
                files=metadata_artifacts,
            )
        )

    # Get universe file path relative to store
    universe_rel_path = str(universe_path)
    try:
        universe_rel_path = str(universe_path.relative_to(store_dir))
    except ValueError:
        # Universe might not be in store dir
        pass

    # Create manifest
    manifest = DatasetManifest(
        snapshot_id=snapshot_id,
        created_at_utc=created_at,
        universe_file=universe_rel_path,
        universe_snapshot_id=universe.snapshot_id,
        time_range=TimeRange(
            start=start_date.strftime("%Y-%m-%d"),
            end=end_date.strftime("%Y-%m-%d"),
        ),
        intervals=intervals,
        symbols=symbols,
        artifacts=artifact_ranges,
        provenance=ManifestProvenance(
            build_timestamp_utc=created_at,
        ),
        assumptions=ExecutionAssumptions(),
        gaps_detected=gaps_detected,
        validation_passed=len(gaps_detected) == 0,
    )

    # Create checksum entries
    all_artifacts = kline_artifacts + funding_artifacts + metadata_artifacts
    checksum_entries = [
        ChecksumEntry(
            path=a.path,
            sha256=a.sha256,
            size_bytes=a.size_bytes,
        )
        for a in all_artifacts
    ]

    checksums = ChecksumFile(
        snapshot_id=snapshot_id,
        created_at_utc=created_at,
        entries=checksum_entries,
    )

    # Create snapshot directory
    snapshot_dir = output_dir / snapshot_id
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    # Write manifest
    manifest_path = snapshot_dir / "manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(manifest.model_dump(mode="json"), f, indent=2, default=str)
    print(f"\nCreated: {manifest_path}")

    # Write checksums (in standard sha256sum format)
    checksums_path = snapshot_dir / "checksums.sha256"
    with open(checksums_path, "w") as f:
        f.write(checksums.to_sha256_format())
    print(f"Created: {checksums_path}")

    # Summary
    print("\n=== Snapshot Summary ===")
    print(f"Snapshot ID: {snapshot_id}")
    print(f"Snapshot directory: {snapshot_dir}")
    print(f"Total files: {len(all_artifacts)}")
    print(f"Total size: {sum(a.size_bytes for a in all_artifacts) / 1024 / 1024:.2f} MB")
    print(f"Validation passed: {manifest.validation_passed}")

    return snapshot_dir


def parse_intervals(value: str) -> list[str]:
    """Parse comma-separated interval list."""
    return [i.strip() for i in value.split(",") if i.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build an immutable snapshot for reproducible backtesting."
    )
    parser.add_argument(
        "--universe",
        type=Path,
        required=True,
        help="Path to universe snapshot JSON file",
    )
    parser.add_argument(
        "--start",
        type=str,
        required=True,
        help="Start date in YYYY-MM-DD format",
    )
    parser.add_argument(
        "--end",
        type=str,
        default=None,
        help="End date in YYYY-MM-DD format. Default: today",
    )
    parser.add_argument(
        "--intervals",
        type=parse_intervals,
        default=["4h"],
        help="Comma-separated kline intervals. Default: 4h",
    )
    parser.add_argument(
        "--store-dir",
        type=Path,
        default=Path("./data/datasets/usdm/store"),
        help="Path to shared store directory. Default: ./data/datasets/usdm/store",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("./data/datasets/usdm/snapshots"),
        help="Output directory for snapshots. Default: ./data/datasets/usdm/snapshots",
    )
    parser.add_argument(
        "--snapshot-id",
        type=str,
        default=None,
        help="Custom snapshot ID. Default: auto-generated",
    )
    parser.add_argument(
        "--no-funding",
        action="store_true",
        help="Exclude funding data from snapshot",
    )
    args = parser.parse_args()

    start_date = parse_date(args.start)
    end_date = parse_date(args.end) if args.end else datetime.now(timezone.utc)

    if start_date >= end_date:
        print("Error: Start date must be before end date")
        return

    build_snapshot(
        universe_path=args.universe,
        start_date=start_date,
        end_date=end_date,
        intervals=args.intervals,
        store_dir=args.store_dir,
        output_dir=args.output_dir,
        include_funding=not args.no_funding,
        snapshot_id=args.snapshot_id,
    )


if __name__ == "__main__":
    main()
