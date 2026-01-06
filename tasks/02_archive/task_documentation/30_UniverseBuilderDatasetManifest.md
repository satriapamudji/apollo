# Task 30 - Universe Builder + Offline Dataset Manifest/Checksums

## Overview

Created a reproducible dataset build pipeline for Binance USD-M perpetual futures backtesting. The pipeline builds universe snapshots, downloads market data artifacts to a shared store, creates immutable snapshots with manifests and checksums, and validates datasets for reproducibility.

## What Was Before

- Universe selection was only defined in `config.yaml` parameters (`universe.size`, `universe.min_quote_volume_usd`)
- No standardized pipeline for producing versioned dataset artifacts
- Backtests were not reproducible - "same code, different results" was normal
- No checksums or manifests to verify dataset integrity
- No separation between mutable store and immutable snapshots

## What Changed

### 1. Data Models: `src/data/models.py`

New Pydantic models for the entire dataset pipeline:

**Universe Models:**
```python
class UniverseSymbol:
    symbol: str
    quote_volume_24h: float
    rank: int

class UniverseSelectionParams:
    min_quote_volume_usd: float
    size: int
    allow_list: list[str] | None
    deny_list: list[str] | None

class UniverseSnapshot:
    as_of_date: str
    symbols: list[UniverseSymbol]
    selection_params: UniverseSelectionParams
    provenance: UniverseProvenance
```

**Symbol Rules Models:**
```python
class SymbolRules:
    symbol: str
    tick_size: float
    step_size: float
    min_qty: float
    min_notional: float
    contract_type: str
    status: str
    # ... additional fields
```

**Manifest Models:**
```python
class DatasetManifest:
    manifest_version: str
    snapshot_id: str
    created_at: datetime
    universe_file: str
    time_range: TimeRange
    intervals: list[str]
    artifacts: list[ArtifactTimeRange]
    provenance: ManifestProvenance
    assumptions: ExecutionAssumptions

class ChecksumEntry:
    sha256: str
    path: str

class ChecksumFile:
    entries: list[ChecksumEntry]
```

### 2. Universe Builder: `src/tools/build_universe.py`

CLI tool to build universe snapshots from public Binance endpoints:

```bash
# Build universe with default parameters
python -m src.tools.build_universe

# Custom parameters
python -m src.tools.build_universe \
    --as-of-date 2024-06-01 \
    --size 20 \
    --min-volume 50000000 \
    --allow BTCUSDT,ETHUSDT
```

Features:
- Fetches `/fapi/v1/ticker/24hr` for liquidity ranking
- Fetches `/fapi/v1/exchangeInfo` for filtering (TRADING status, PERPETUAL type, USDT quote)
- Ranks by 24h quote volume, applies allow/deny lists
- Outputs `data/datasets/usdm/store/universe/universe_<date>.json`

### 3. Store Builder: `src/tools/build_store.py`

CLI tool to download market data artifacts to the shared store:

```bash
# Download data for universe symbols
python -m src.tools.build_store \
    --universe data/datasets/usdm/store/universe/universe_2024-06-01.json \
    --start 2024-01-01 \
    --end 2024-06-01 \
    --intervals 4h
```

Features:
- Downloads klines (OHLCV) partitioned by month: `store/bars/trade/interval=4h/symbol=BTCUSDT/month=2024-01.csv`
- Downloads funding history partitioned by month: `store/funding/symbol=BTCUSDT/month=2024-01.csv`
- Downloads exchangeInfo and saves normalized symbol rules: `store/metadata/symbol_rules/symbol_rules_<date>.json`
- **Append-only**: Never overwrites existing partition files
- Retry logic for rate limits and transient errors

### 4. Snapshot Builder: `src/tools/build_snapshot.py`

CLI tool to create immutable snapshots with manifests and checksums:

```bash
# Create a snapshot
python -m src.tools.build_snapshot \
    --universe data/datasets/usdm/store/universe/universe_2024-06-01.json \
    --start 2024-01-01 \
    --end 2024-06-01 \
    --intervals 4h
```

Outputs to `data/datasets/usdm/snapshots/<snapshot_id>/`:
- `manifest.json` - Full provenance, time ranges, assumptions
- `checksums.sha256` - Standard sha256sum format for all referenced files

Manifest records:
- Endpoints, params, paging strategy
- Time ranges per artifact type
- Schema versions
- Execution assumptions (fees, slippage model, random seed policy)
- Gap/warning information

### 5. Validator: `src/tools/validate_dataset.py`

CLI tool to validate dataset snapshots:

```bash
# Validate a snapshot
python -m src.tools.validate_dataset \
    --snapshot data/datasets/usdm/snapshots/20240101_to_20240601_abc12345
```

Validation checks:
- **Checksum verification**: All files match checksums.sha256
- **Schema validation**: Required columns present in CSV files
- **Data gap detection**: Reports missing time ranges
- **Symbol rules validation**: All universe symbols have rules
- **Manifest consistency**: Cross-references are valid

Output: Clear PASS/FAIL per check with detailed report.

### 6. Tests: `tests/test_dataset_tools.py`

19 comprehensive tests covering:
- Pydantic model serialization/deserialization
- Universe builder filtering and ranking logic
- Snapshot helper functions (month range generation, SHA256 hashing, CSV row counting)
- Validation functions (checksum verification, schema validation)
- End-to-end validation workflow

## Directory Structure

```
data/datasets/usdm/
├── store/                              # Shared append-only store
│   ├── metadata/
│   │   ├── exchangeInfo/               # Raw exchangeInfo snapshots
│   │   │   └── exchange_info_2024-06-01T12-00-00.json
│   │   └── symbol_rules/               # Normalized symbol rules
│   │       └── symbol_rules_2024-06-01.json
│   ├── universe/                       # Universe snapshots by date
│   │   └── universe_2024-06-01.json
│   ├── bars/trade/                     # OHLCV klines
│   │   └── interval=4h/
│   │       └── symbol=BTCUSDT/
│   │           ├── month=2024-01.csv
│   │           ├── month=2024-02.csv
│   │           └── ...
│   └── funding/                        # Funding rate history
│       └── symbol=BTCUSDT/
│           ├── month=2024-01.csv
│           └── ...
└── snapshots/                          # Immutable pinned snapshots
    └── 20240101_to_20240601_abc12345/
        ├── manifest.json
        └── checksums.sha256
```

## Design Decisions

### Two-Layer Architecture (Store + Snapshots)

1. **Shared Store**: Append-only, partitioned by month. Safe to rerun - existing files never overwritten. Multiple snapshots can reference the same store files.

2. **Immutable Snapshots**: Pin exact inputs for a backtest run. `manifest.json` + `checksums.sha256` define reproducible inputs. Adding new data to store doesn't affect existing snapshots.

### Monthly Partitioning

Balance between:
- File count (not too many small files)
- Append-only safety (never rewrite existing months)
- Incremental updates (add new months without touching old)

### CSV Format

Per task requirements, kept CSV format for initial implementation. Parquet can be added later for performance.

### close_time as Canonical Timestamp

Per roadmap spec, bars use `close_time` as the canonical timestamp for alignment.

### Public Endpoints Only

All data fetching uses public Binance endpoints - no API key required for market data.

## Usage Examples

### Full Pipeline Run

```bash
# 1. Build universe (top 10 by volume)
python -m src.tools.build_universe --size 10 --as-of-date 2024-06-01

# 2. Download market data to store
python -m src.tools.build_store \
    --universe data/datasets/usdm/store/universe/universe_2024-06-01.json \
    --start 2024-01-01 \
    --end 2024-06-01 \
    --intervals 4h

# 3. Create immutable snapshot
python -m src.tools.build_snapshot \
    --universe data/datasets/usdm/store/universe/universe_2024-06-01.json \
    --start 2024-01-01 \
    --end 2024-06-01 \
    --intervals 4h

# 4. Validate snapshot
python -m src.tools.validate_dataset \
    --snapshot data/datasets/usdm/snapshots/20240101_to_20240601_abc12345
```

### Programmatic Usage

```python
from src.data.models import DatasetManifest, ChecksumFile
from pathlib import Path
import json

# Load and inspect manifest
manifest_path = Path("data/datasets/usdm/snapshots/my_snapshot/manifest.json")
manifest = DatasetManifest.model_validate_json(manifest_path.read_text())

print(f"Snapshot: {manifest.snapshot_id}")
print(f"Time range: {manifest.time_range.start} to {manifest.time_range.end}")
print(f"Symbols: {len(manifest.artifacts)} artifacts")

# Load checksums
checksums_path = manifest_path.parent / "checksums.sha256"
checksum_file = ChecksumFile.from_sha256sum_format(checksums_path.read_text())
print(f"Files tracked: {len(checksum_file.entries)}")
```

## Files Created

- `src/data/models.py` - Pydantic data models
- `src/tools/build_universe.py` - Universe builder CLI
- `src/tools/build_store.py` - Store builder CLI
- `src/tools/build_snapshot.py` - Snapshot builder CLI
- `src/tools/validate_dataset.py` - Validator CLI
- `tests/test_dataset_tools.py` - Unit tests

## Acceptance Criteria Met

- [x] Snapshot can be built and validated offline using `manifest.json` + `checksums.sha256`
- [x] Universe selection is captured as an artifact (not just YAML parameters)
- [x] Store ingestion does not overwrite existing partitions and is safe to rerun
- [x] Validator provides clear PASS/FAIL output per artifact type
