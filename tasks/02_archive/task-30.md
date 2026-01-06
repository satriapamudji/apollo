# Task 30 - Universe Builder + Offline Dataset Manifest/Checksums (Reproducible Builds)

## Goal
Create a reproducible dataset build pipeline that:
1) builds a universe snapshot (top-N by liquidity rules),
2) downloads required market data artifacts,
3) writes a `manifest.json` capturing provenance and assumptions,
4) generates `checksums.sha256` so backtests are verifiably offline-reproducible.

## Why
Current YAML contains universe settings (`universe.size`, `universe.min_quote_volume_usd`) but there is no standardized pipeline producing versioned artifacts. Without this, backtests are not reproducible and “same code, different results” becomes normal.

## Deliverables

### 1) Define storage vs snapshot semantics (shared store + pinned manifests)
We want **one shared dataset store** that any backtest can reuse, and **immutable snapshots** for reproducibility.

Adopt a two-layer convention under:
- Shared append-only store: `data/datasets/usdm/store/...`
- Pinned snapshots: `data/datasets/usdm/snapshots/<snapshot_id>/...`

Rules:
- The store is **append-only and partitioned** (e.g., by date/month) so existing files are not rewritten in-place.
- A snapshot is **immutable** once created (`manifest.json` + `checksums.sha256` define the exact inputs for a run).
- Adding a new interval later (e.g., `1h`) means adding new store partitions; old snapshots remain valid.

### 2) Universe builder tool
Create a tool that uses public USD-M endpoints to build a universe snapshot:
- Input: `as_of_date`, `min_quote_volume_usd`, `size`, optional allow/deny lists.
- Data sources:
  - `GET /fapi/v1/ticker/24hr` for liquidity ranking.
  - `GET /fapi/v1/exchangeInfo` for filtering to `status=TRADING` and `contractType=PERPETUAL`.

Output:
- `universe/universe_<date>.json` with symbols + selection metrics + selection parameters.

### 3) Store builder + snapshot builder (minimum viable)
Create tools that:

**(a) Store builder** (reusable, strategy-agnostic)
- Downloads and writes partitioned artifacts into `data/datasets/usdm/store/`:
  - trade klines for required intervals (at least `4h` for current strategy; extensible to `1h` later),
  - funding history,
  - exchangeInfo snapshots and normalized symbol rules.

**(b) Snapshot builder** (pins a run input set)
- Given a universe snapshot + time range + intervals, creates:
  - `data/datasets/usdm/snapshots/<snapshot_id>/manifest.json`
  - `data/datasets/usdm/snapshots/<snapshot_id>/checksums.sha256`
- The snapshot must list the exact store files included (or exact partition keys) so results are reproducible even as the store grows.

The manifest must record:
- endpoints, params, paging strategy (kline max limit), retry/backoff behavior
- time ranges per artifact
- schema versions
- execution assumptions (fees, slippage model, random seed policy)
- any partial failures / gaps detected

### 4) Validator tool
Create a dataset validator that:
- checks checksums match,
- validates schema requirements (bars + funding + symbol rules),
- reports missing data ranges/gaps.

## Acceptance Criteria
- A snapshot can be built and validated offline using `manifest.json` + `checksums.sha256`.
- Universe selection is captured as an artifact (not just YAML parameters).
- Store ingestion does not overwrite existing partitions and is safe to rerun.
- Validator provides clear PASS/FAIL output per artifact type.

## Files to Modify
- `src/tools/` (new dataset builder + validator tools)
- `src/tools/download_klines.py` (likely to share logic for paging/retries)
- `tasks/00_roadmap/02_Backtest.md` (only if dataset layout needs adjustment)
- `tests/` (if a test harness exists for tools)

## Notes
- Keep the first implementation CSV-friendly if needed; parquet can be added in a follow-up once dependencies are chosen.
- Rate-limit handling must be explicit and recorded in the manifest so “data quality” is auditable.
