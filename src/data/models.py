"""Data models for dataset management and reproducible backtesting.

This module defines Pydantic models for:
- Universe snapshots (symbol selection with metrics)
- Symbol rules (normalized trading filters from exchangeInfo)
- Dataset manifests (provenance, assumptions, artifact references)
- Store artifacts (file references with checksums)
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class UniverseSymbol(BaseModel):
    """A symbol selected for the trading universe with selection metrics."""

    symbol: str
    quote_volume_24h: float = Field(description="24h quote volume in USD")
    last_price: float
    price_change_pct_24h: float
    high_24h: float
    low_24h: float
    trades_24h: int
    status: str = Field(description="Trading status from exchangeInfo")
    contract_type: str = Field(description="Contract type (PERPETUAL)")
    quote_asset: str = Field(default="USDT")


class UniverseSelectionParams(BaseModel):
    """Parameters used for universe selection."""

    min_quote_volume_usd: float
    size: int
    allow_list: list[str] | None = None
    deny_list: list[str] | None = None


class UniverseSnapshot(BaseModel):
    """A complete universe snapshot with metadata."""

    snapshot_id: str
    as_of_date: str = Field(description="Date in YYYY-MM-DD format")
    created_at_utc: datetime
    schema_version: str = "1.0"
    product: Literal["binance_usdm_perp"] = "binance_usdm_perp"
    environment: Literal["production", "testnet"] = "production"
    selection_params: UniverseSelectionParams
    symbols: list[UniverseSymbol]
    provenance: UniverseProvenance


class UniverseProvenance(BaseModel):
    """Provenance information for universe selection."""

    ticker_endpoint: str = "/fapi/v1/ticker/24hr"
    exchange_info_endpoint: str = "/fapi/v1/exchangeInfo"
    fetch_timestamp_utc: datetime
    total_symbols_fetched: int
    symbols_after_status_filter: int
    symbols_after_volume_filter: int
    errors: list[str] = Field(default_factory=list)


class SymbolRules(BaseModel):
    """Normalized symbol trading rules from exchangeInfo."""

    symbol: str
    tick_size: float = Field(description="Price increment")
    step_size: float = Field(description="Quantity increment")
    min_qty: float = Field(description="Minimum order quantity")
    max_qty: float = Field(description="Maximum order quantity")
    min_notional: float = Field(description="Minimum notional value")
    contract_type: str
    status: str
    quote_asset: str
    base_asset: str
    effective_date: str = Field(description="Date this snapshot was taken (YYYY-MM-DD)")


class SymbolRulesSnapshot(BaseModel):
    """Collection of symbol rules from a single exchangeInfo fetch."""

    snapshot_id: str
    fetched_at_utc: datetime
    schema_version: str = "1.0"
    rules: list[SymbolRules]


class StoreArtifact(BaseModel):
    """Reference to a file in the shared store."""

    path: str = Field(description="Relative path from store root")
    sha256: str
    size_bytes: int
    row_count: int | None = Field(default=None, description="Row count for CSV files")


class TimeRange(BaseModel):
    """A time range specification."""

    start: str = Field(description="Start date in YYYY-MM-DD format")
    end: str = Field(description="End date in YYYY-MM-DD format")


class ArtifactTimeRange(BaseModel):
    """Time range for a specific artifact type."""

    artifact_type: Literal["klines", "funding", "exchange_info"]
    symbol: str | None = None
    interval: str | None = None
    time_range: TimeRange
    files: list[StoreArtifact]


class ManifestProvenance(BaseModel):
    """Detailed provenance for a dataset build."""

    build_timestamp_utc: datetime
    binance_base_url: str = "https://fapi.binance.com"
    kline_max_limit: int = 1500
    funding_max_limit: int = 1000
    retry_attempts: int = 5
    retry_backoff_strategy: str = "exponential"
    rate_limit_weight_per_minute: int = 1200
    errors_encountered: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ExecutionAssumptions(BaseModel):
    """Execution model assumptions for backtesting."""

    maker_fee_pct: float = Field(default=0.02, description="Maker fee in percent")
    taker_fee_pct: float = Field(default=0.04, description="Taker fee in percent")
    slippage_model: Literal["fixed", "atr_scaled"] = "fixed"
    slippage_base_bps: float = 2.0
    slippage_atr_scale: float = 1.0
    funding_application: Literal["discrete", "continuous"] = "discrete"
    funding_cadence_hours: int = 8
    random_seed: int | None = None


class DatasetManifest(BaseModel):
    """Complete manifest for a reproducible dataset snapshot."""

    snapshot_id: str
    created_at_utc: datetime
    schema_version: str = "1.0"
    product: Literal["binance_usdm_perp"] = "binance_usdm_perp"
    environment: Literal["production_market_data", "testnet_market_data"] = "production_market_data"

    # Universe reference
    universe_file: str = Field(description="Path to universe JSON file")
    universe_snapshot_id: str

    # Time range coverage
    time_range: TimeRange
    intervals: list[str] = Field(description="Kline intervals included (e.g., ['4h', '1d'])")
    symbols: list[str] = Field(description="Symbols included in this snapshot")

    # Artifact references
    artifacts: list[ArtifactTimeRange]

    # Provenance and assumptions
    provenance: ManifestProvenance
    assumptions: ExecutionAssumptions

    # Data quality
    gaps_detected: list[str] = Field(
        default_factory=list, description="Description of any data gaps found"
    )
    validation_passed: bool = True


class ChecksumEntry(BaseModel):
    """A single checksum entry."""

    path: str
    sha256: str
    size_bytes: int


class ChecksumFile(BaseModel):
    """Complete checksums file for a snapshot."""

    snapshot_id: str
    created_at_utc: datetime
    entries: list[ChecksumEntry]

    def to_sha256_format(self) -> str:
        """Export in standard sha256sum format."""
        lines = [f"{e.sha256}  {e.path}" for e in self.entries]
        return "\n".join(lines) + "\n"

    @classmethod
    def from_sha256_format(cls, content: str, snapshot_id: str) -> ChecksumFile:
        """Parse from standard sha256sum format."""
        entries = []
        for line in content.strip().split("\n"):
            if not line.strip():
                continue
            # Format: "hash  path" (two spaces)
            parts = line.split("  ", 1)
            if len(parts) == 2:
                entries.append(ChecksumEntry(path=parts[1], sha256=parts[0], size_bytes=0))
        return cls(
            snapshot_id=snapshot_id,
            created_at_utc=datetime.utcnow(),
            entries=entries,
        )
