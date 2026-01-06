"""Crowding and regime data caching for Binance Futures public market data."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog


@dataclass(frozen=True)
class CrowdingData:
    """Snapshot of crowding and regime indicators for a symbol.

    All values are deterministic and based on public Binance data.
    """
    symbol: str
    timestamp: datetime

    # Open Interest
    open_interest: float = 0.0
    open_interest_value: float = 0.0
    oi_change_24h_pct: float = 0.0
    oi_expansion_score: float = 0.5

    # Long/Short Ratios (Global)
    long_short_ratio: float = 0.5
    long_account_ratio: float = 0.5
    short_account_ratio: float = 0.5

    # Top Trader Ratios
    top_long_short_ratio: float = 0.5
    top_long_account_ratio: float = 0.5
    top_short_account_ratio: float = 0.5

    # Taker Activity
    taker_buy_ratio: float = 0.5
    taker_sell_ratio: float = 0.5
    taker_buy_volume: float = 0.0
    taker_sell_volume: float = 0.0
    # 0.0 = selling pressure, 0.5 = balanced, 1.0 = buying pressure
    taker_imbalance_score: float = 0.5

    # Funding
    funding_rate: float = 0.0
    funding_rate_volatility: float = 0.0

    # Computed crowding scores (0.0 = crowded, 1.0 = not crowded)
    crowding_score: float = 0.5
    funding_volatility_score: float = 0.5

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for JSON storage."""
        return {
            "symbol": self.symbol,
            "timestamp": self.timestamp.isoformat(),
            "open_interest": self.open_interest,
            "open_interest_value": self.open_interest_value,
            "oi_change_24h_pct": self.oi_change_24h_pct,
            "oi_expansion_score": self.oi_expansion_score,
            "long_short_ratio": self.long_short_ratio,
            "long_account_ratio": self.long_account_ratio,
            "short_account_ratio": self.short_account_ratio,
            "top_long_short_ratio": self.top_long_short_ratio,
            "top_long_account_ratio": self.top_long_account_ratio,
            "top_short_account_ratio": self.top_short_account_ratio,
            "taker_buy_ratio": self.taker_buy_ratio,
            "taker_sell_ratio": self.taker_sell_ratio,
            "taker_buy_volume": self.taker_buy_volume,
            "taker_sell_volume": self.taker_sell_volume,
            "taker_imbalance_score": self.taker_imbalance_score,
            "funding_rate": self.funding_rate,
            "funding_rate_volatility": self.funding_rate_volatility,
            "crowding_score": self.crowding_score,
            "funding_volatility_score": self.funding_volatility_score,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CrowdingData:
        """Deserialize from dict."""
        ts = data["timestamp"]
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return cls(
            symbol=data["symbol"],
            timestamp=ts,
            open_interest=data.get("open_interest", 0.0),
            open_interest_value=data.get("open_interest_value", 0.0),
            oi_change_24h_pct=data.get("oi_change_24h_pct", 0.0),
            oi_expansion_score=data.get("oi_expansion_score", 0.5),
            long_short_ratio=data.get("long_short_ratio", 0.5),
            long_account_ratio=data.get("long_account_ratio", 0.5),
            short_account_ratio=data.get("short_account_ratio", 0.5),
            top_long_short_ratio=data.get("top_long_short_ratio", 0.5),
            top_long_account_ratio=data.get("top_long_account_ratio", 0.5),
            top_short_account_ratio=data.get("top_short_account_ratio", 0.5),
            taker_buy_ratio=data.get("taker_buy_ratio", 0.5),
            taker_sell_ratio=data.get("taker_sell_ratio", 0.5),
            taker_buy_volume=data.get("taker_buy_volume", 0.0),
            taker_sell_volume=data.get("taker_sell_volume", 0.0),
            taker_imbalance_score=data.get("taker_imbalance_score", 0.5),
            funding_rate=data.get("funding_rate", 0.0),
            funding_rate_volatility=data.get("funding_rate_volatility", 0.0),
            crowding_score=data.get("crowding_score", 0.5),
            funding_volatility_score=data.get("funding_volatility_score", 0.5),
        )


class CrowdingCache:
    """Cache and compute crowding data for symbols.

    Stores historical data to `data/crowding/{symbol}.jsonl` for:
    - OI trend detection (expansion vs decay)
    - Funding rate volatility computation
    - Long/short ratio history
    """

    def __init__(self, cache_dir: str = "./data/crowding") -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._log = structlog.get_logger(__name__)

    def _get_cache_path(self, symbol: str) -> Path:
        """Get the cache file path for a symbol."""
        return self.cache_dir / f"{symbol}.jsonl"

    def load_history(self, symbol: str, limit: int = 100) -> list[CrowdingData]:
        """Load historical crowding data for a symbol."""
        path = self._get_cache_path(symbol)
        if not path.exists():
            return []

        history: list[CrowdingData] = []
        with open(path) as f:
            for line in f:
                if line.strip():
                    data = json.loads(line)
                    history.append(CrowdingData.from_dict(data))

        return history[-limit:]

    def save(self, data: CrowdingData) -> None:
        """Append a data point to the cache."""
        path = self._get_cache_path(data.symbol)
        with open(path, "a") as f:
            f.write(json.dumps(data.to_dict()) + "\n")
        self._log.debug("crowding_data_cached", symbol=data.symbol)

    def compute_oi_expansion(
        self,
        history: list[CrowdingData],
        window: int = 3,
        threshold_pct: float = 0.1,
    ) -> float:
        """Compute OI expansion score based on recent trend.

        Args:
            history: Historical OI data
            window: Number of periods to compare
            threshold_pct: Threshold for expansion/contraction (10% default)

        Returns:
            Score from 0.0 (contraction) to 1.0 (expansion)
        """
        if len(history) < 2:
            return 0.5

        recent = history[-window:]
        if len(recent) < 2:
            return 0.5

        # Calculate average OI change
        changes: list[float] = []
        for i in range(1, len(recent)):
            prev_oi = recent[i - 1].open_interest
            curr_oi = recent[i].open_interest
            if prev_oi > 0:
                change = (curr_oi - prev_oi) / prev_oi
                changes.append(change)

        if not changes:
            return 0.5

        avg_change = sum(changes) / len(changes)

        # Score: 0.0 = strong contraction, 0.5 = neutral, 1.0 = strong expansion
        score = 0.5 + (avg_change / threshold_pct)
        return max(0.0, min(1.0, score))

    def compute_funding_volatility(
        self,
        history: list[CrowdingData],
        window: int = 8,
    ) -> float:
        """Compute funding rate volatility.

        Args:
            history: Historical funding data
            window: Number of periods to analyze

        Returns:
            Volatility score from 0.0 (stable) to 1.0 (volatile)
        """
        recent = history[-window:]
        if len(recent) < 2:
            return 0.0

        rates = [d.funding_rate for d in recent]
        if len(rates) < 2:
            return 0.0

        # Calculate standard deviation of funding rates
        mean = sum(rates) / len(rates)
        variance = sum((r - mean) ** 2 for r in rates) / len(rates)
        std = variance ** 0.5

        # Normalize: interpret funding rates as decimals (e.g., 0.0001 = 0.01%).
        # Map std of ~0.00001 (0.001%) -> low volatility; std of ~0.00005+ (0.005%) -> high volatility.
        scale = 0.00005
        volatility_score = min(std / scale, 1.0) if std > 0 else 0.0
        return min(volatility_score, 1.0)

    def compute_crowding_score(
        self,
        data: CrowdingData,
        history: list[CrowdingData],
        long_short_extreme: float = 0.8,
    ) -> float:
        """Compute overall crowding score.

        Penalize when:
        - Long/short ratio is extreme (everyone on one side)
        - Top traders are more extreme than global

        Args:
            data: Current crowding data
            history: Historical data for context
            long_short_extreme: Threshold for extreme ratio (default 80%)

        Returns:
            Score from 0.0 (crowded) to 1.0 (not crowded)
        """
        score = 1.0

        # Penalize extreme long/short ratio
        if data.long_short_ratio > long_short_extreme:
            # Too many longs
            penalty = (data.long_short_ratio - 0.5) / 0.5
            score -= penalty * 0.3
        elif data.long_short_ratio < (1 - long_short_extreme):
            # Too many shorts
            penalty = ((1 - long_short_extreme) - data.long_short_ratio) / 0.5
            score -= penalty * 0.3

        # Check if top traders are more extreme (crowding signal)
        if data.top_long_short_ratio > data.long_short_ratio * 1.1:
            score -= 0.2  # Top traders are more bullish
        elif data.top_long_short_ratio < data.long_short_ratio * 0.9:
            score -= 0.2  # Top traders are more bearish

        # Taker imbalance can indicate smart money positioning
        if data.taker_imbalance_score > 0.7:
            score -= 0.1  # Strong buying pressure might mean late
        elif data.taker_imbalance_score < 0.3:
            score -= 0.1

        return max(0.0, min(1.0, score))

    def compute_taker_imbalance_score(
        self,
        taker_buy_ratio: float,
        taker_sell_ratio: float,
        threshold: float = 0.6,
    ) -> float:
        """Compute taker imbalance score.

        Args:
            taker_buy_ratio: Percentage of buys
            taker_sell_ratio: Percentage of sells
            threshold: Imbalance threshold for penalty

        Returns:
            Score from 0.0 (selling pressure) to 1.0 (buying pressure)
        """
        # Normalize to 0-1 range
        total = taker_buy_ratio + taker_sell_ratio
        if total > 0:
            buy_ratio = taker_buy_ratio / total
        else:
            buy_ratio = 0.5

        return buy_ratio

    def build_from_api_response(
        self,
        symbol: str,
        oi_data: dict[str, Any],
        ratio_data: dict[str, Any],
        taker_data: dict[str, Any],
        funding_rate: float,
        history: list[CrowdingData],
        config: dict[str, Any] | None = None,
    ) -> CrowdingData:
        """Build CrowdingData from API responses.

        Args:
            symbol: Trading pair
            oi_data: Open interest API response
            ratio_data: Long/short ratio API response
            taker_data: Taker stats API response
            funding_rate: Current funding rate
            history: Historical data for trend computation
            config: Optional configuration dict
        """
        cfg = config or {}

        # Parse OI data
        open_interest = float(oi_data.get("openInterest", 0))
        open_interest_value = float(oi_data.get("turnover", 0))

        # Calculate OI change
        oi_change_24h_pct = 0.0
        if history:
            prev_oi = history[-1].open_interest
            if prev_oi > 0:
                oi_change_24h_pct = (open_interest - prev_oi) / prev_oi

        oi_expansion_score = self.compute_oi_expansion(
            history,
            window=cfg.get("oi_window", 3),
            threshold_pct=cfg.get("oi_threshold_pct", 0.1),
        )

        # Parse ratio data
        long_short_ratio = float(ratio_data.get("longShortRatio", 0.5))
        long_account_ratio = float(ratio_data.get("longAccount", 0.5))
        short_account_ratio = float(ratio_data.get("shortAccount", 0.5))

        # Parse taker data
        taker_buy_ratio = float(taker_data.get("buySellRatio", 0.5))
        taker_sell_ratio = float(taker_data.get("buySellVolRatio", 0.5))
        taker_buy_volume = float(taker_data.get("buyVol", 0))
        taker_sell_volume = float(taker_data.get("sellVol", 0))

        taker_imbalance_score = self.compute_taker_imbalance_score(
            taker_buy_ratio,
            taker_sell_ratio,
            threshold=cfg.get("taker_threshold", 0.6),
        )

        # Calculate funding volatility
        funding_volatility = self.compute_funding_volatility(
            history,
            window=cfg.get("funding_window", 8),
        )

        # Build history with current data included
        full_history = history + [{
            "open_interest": open_interest,
            "funding_rate": funding_rate,
        }]

        # Compute crowding score
        crowding_score = self.compute_crowding_score(
            CrowdingData(
                symbol=symbol,
                timestamp=datetime.now(timezone.utc),
                open_interest=open_interest,
                open_interest_value=open_interest_value,
                long_short_ratio=long_short_ratio,
                long_account_ratio=long_account_ratio,
                short_account_ratio=short_account_ratio,
                taker_buy_ratio=taker_buy_ratio,
                taker_sell_ratio=taker_sell_ratio,
                taker_buy_volume=taker_buy_volume,
                taker_sell_volume=taker_sell_volume,
            ),
            full_history,
            long_short_extreme=cfg.get("long_short_extreme", 0.8),
        )

        return CrowdingData(
            symbol=symbol,
            timestamp=datetime.now(timezone.utc),
            open_interest=open_interest,
            open_interest_value=open_interest_value,
            oi_change_24h_pct=oi_change_24h_pct,
            oi_expansion_score=oi_expansion_score,
            long_short_ratio=long_short_ratio,
            long_account_ratio=long_account_ratio,
            short_account_ratio=short_account_ratio,
            top_long_short_ratio=0.5,  # TODO: Add when API available
            top_long_account_ratio=0.5,
            top_short_account_ratio=0.5,
            taker_buy_ratio=taker_buy_ratio,
            taker_sell_ratio=taker_sell_ratio,
            taker_buy_volume=taker_buy_volume,
            taker_sell_volume=taker_sell_volume,
            taker_imbalance_score=taker_imbalance_score,
            funding_rate=funding_rate,
            funding_rate_volatility=funding_volatility,
            crowding_score=crowding_score,
            funding_volatility_score=1.0 - min(funding_volatility * 2, 1.0),
        )
