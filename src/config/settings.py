"""
Configuration management with Pydantic validation.

Loads settings from YAML config file and environment variables.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings


class BinanceConfig(BaseModel):
    """Binance API configuration."""

    base_url: str = "https://fapi.binance.com"
    ws_url: str = "wss://fstream.binance.com"
    testnet_base_url: str = "https://testnet.binancefuture.com"
    testnet_ws_url: str = "wss://stream.binancefuture.com"
    recv_window: int = Field(default=5000, ge=1000, le=60000)
    user_stream_enabled: bool = True
    user_stream_keepalive_sec: int = Field(default=30 * 60, ge=60, le=60 * 60)
    use_production_market_data: bool = False
    market_data_base_url: str = "https://fapi.binance.com"
    market_data_ws_url: str = "wss://fstream.binance.com"
    time_sync_interval_sec: int = Field(default=60, ge=30, le=3600)


class RunConfig(BaseModel):
    """Runtime trading mode configuration."""

    mode: Literal["paper", "testnet", "live"] = Field(default="paper", validation_alias="RUN_MODE")
    enable_trading: bool = Field(default=False, validation_alias="RUN_ENABLE_TRADING")
    live_confirm: str = Field(default="", validation_alias="RUN_LIVE_CONFIRM")

    model_config = {
        "populate_by_name": True,
    }


class IndicatorConfig(BaseModel):
    """Technical indicator parameters."""

    ema_fast: int = Field(default=8, ge=2, le=50)
    ema_slow: int = Field(default=21, ge=5, le=100)
    atr_period: int = Field(default=14, ge=5, le=50)
    rsi_period: int = Field(default=14, ge=5, le=50)
    # Regime detection indicators
    adx_period: int = Field(default=14, ge=5, le=50)
    chop_period: int = Field(default=14, ge=5, le=50)
    # Volume indicators
    volume_sma_period: int = Field(default=20, ge=5, le=50)


class EntryConfig(BaseModel):
    """Entry signal configuration."""

    style: Literal["pullback", "breakout"] = "pullback"
    score_threshold: float = Field(default=0.65, ge=0.3, le=0.95)
    max_breakout_extension_atr: float = Field(default=1.5, ge=0.5, le=5.0)
    # Volume confirmation settings
    volume_breakout_threshold: float = Field(default=1.5, ge=1.0, le=5.0)
    volume_confirmation_enabled: bool = Field(default=True)


class ExitConfig(BaseModel):
    """Exit rules configuration."""

    atr_stop_multiplier: float = Field(default=2.0, ge=1.0, le=5.0)
    trailing_start_atr: float = Field(default=1.5, ge=0.5, le=3.0)
    trailing_distance_atr: float = Field(default=1.5, ge=0.5, le=3.0)
    time_stop_days: int = Field(default=7, ge=1, le=30)
    time_stop_min_profit_atr: float = Field(default=0.5, ge=0.0, le=2.0)


class FactorWeightsConfig(BaseModel):
    """Weight configuration for scoring factors."""

    trend: float = Field(default=0.35, ge=0.0, le=1.0)
    volatility: float = Field(default=0.15, ge=0.0, le=1.0)
    entry_quality: float = Field(default=0.25, ge=0.0, le=1.0)
    funding: float = Field(default=0.10, ge=0.0, le=1.0)
    news: float = Field(default=0.15, ge=0.0, le=1.0)
    liquidity: float = Field(default=0.0, ge=0.0, le=1.0)
    volume: float = Field(default=0.0, ge=0.0, le=1.0)

    @property
    def total(self) -> float:
        return (
            self.trend
            + self.volatility
            + self.entry_quality
            + self.funding
            + self.news
            + self.liquidity
            + self.volume
        )


class ScoringConfig(BaseModel):
    """Scoring engine configuration."""

    enabled: bool = True
    threshold: float = Field(default=0.55, ge=0.3, le=0.95)
    factors: FactorWeightsConfig = Field(default_factory=FactorWeightsConfig)


class StrategyConfig(BaseModel):
    """Strategy configuration."""

    name: str = "trend_following_v1"
    trend_timeframe: str = "1d"
    entry_timeframe: str = "4h"
    indicators: IndicatorConfig = Field(default_factory=IndicatorConfig)
    entry: EntryConfig = Field(default_factory=EntryConfig)
    exit: ExitConfig = Field(default_factory=ExitConfig)
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)


class RiskConfig(BaseModel):
    """Risk management configuration - contains hard limits."""

    risk_per_trade_pct: float = Field(default=1.0, ge=0.1, le=2.0)
    max_leverage: int = Field(default=5, ge=1, le=10)
    default_leverage: int = Field(default=3, ge=1, le=5)
    max_daily_loss_pct: float = Field(default=3.0, ge=1.0, le=10.0)
    max_drawdown_pct: float = Field(default=10.0, ge=5.0, le=25.0)
    max_consecutive_losses: int = Field(default=3, ge=2, le=10)
    max_loss_streak_24h: int = Field(default=5, ge=2, le=20)
    loss_streak_window_hours: int = Field(default=24, ge=1, le=72)
    cooldown_after_loss_hours: int = Field(default=4, ge=1, le=24)
    cooldown_after_max_daily_hours: int = Field(default=24, ge=12, le=48)
    max_positions: int = Field(default=1, ge=1, le=3)
    max_funding_rate_pct: float = Field(default=0.1, ge=0.01, le=0.5)

    @field_validator("default_leverage")
    @classmethod
    def validate_default_leverage(cls, v: int, info) -> int:
        max_lev = info.data.get("max_leverage", 5)
        if v > max_lev:
            raise ValueError(f"default_leverage ({v}) cannot exceed max_leverage ({max_lev})")
        return v


class UniverseConfig(BaseModel):
    """Universe selection configuration."""

    min_quote_volume_usd: float = Field(default=50_000_000, ge=1_000_000)
    max_min_notional_usd: float = Field(default=6.0, ge=1.0, le=100.0)
    max_funding_rate_pct: float = Field(default=0.1, ge=0.01, le=0.5)
    size: int = Field(default=5, ge=3, le=20)
    refresh_interval_hours: int = Field(default=24, ge=1, le=168)
    retry_interval_minutes: int = Field(default=5, ge=1, le=60)


class NewsSourceConfig(BaseModel):
    """Single news source configuration."""

    url: str
    name: str
    enabled: bool = True


class NewsConfig(BaseModel):
    """News ingestion + classification configuration."""

    enabled: bool = True
    # Deterministic by default. Use "llm" only when explicitly enabled.
    classifier_provider: Literal["rules", "llm"] = "rules"
    poll_interval_minutes: int = Field(default=15, ge=5, le=60)
    max_age_hours: int = Field(default=24, ge=1, le=72)
    high_risk_block_hours: int = Field(default=24, ge=6, le=72)
    medium_risk_block_hours: int = Field(default=6, ge=1, le=24)
    source_timeout_sec: int = Field(default=10, ge=1, le=60)
    sources: list[NewsSourceConfig] = Field(default_factory=list)


class RegimeConfig(BaseModel):
    """Market regime detection configuration."""

    enabled: bool = True
    adx_trending_threshold: float = Field(default=25.0, ge=10.0, le=50.0)
    adx_ranging_threshold: float = Field(default=20.0, ge=5.0, le=40.0)
    chop_trending_threshold: float = Field(default=50.0, ge=30.0, le=60.0)
    chop_ranging_threshold: float = Field(default=61.8, ge=50.0, le=80.0)
    transitional_size_multiplier: float = Field(default=0.5, ge=0.1, le=1.0)
    volatility_regime_enabled: bool = False
    volatility_contraction_threshold: float = Field(default=0.5, ge=0.1, le=1.0)
    volatility_expansion_threshold: float = Field(default=2.0, ge=1.0, le=5.0)


class CrowdingConfig(BaseModel):
    """Crowding and regime data configuration for free Binance public data."""

    enabled: bool = True
    # Thresholds for crowding detection
    long_short_extreme: float = Field(default=0.8, ge=0.5, le=0.99)
    oi_expansion_threshold: float = Field(default=0.1, ge=0.0, le=0.5)
    taker_imbalance_threshold: float = Field(default=0.6, ge=0.0, le=1.0)
    # Windows for volatility computation
    funding_volatility_window: int = Field(default=8, ge=2, le=50)
    oi_window: int = Field(default=3, ge=2, le=10)
    # Cache settings
    cache_dir: str = "./data/crowding"
    cache_max_entries: int = Field(default=1000, ge=100, le=10000)


class LLMConfig(BaseModel):
    """LLM integration configuration."""

    provider: Literal["openai", "anthropic", "local"] = "openai"
    model: str = "gpt-4-turbo-preview"
    max_tokens: int = Field(default=500, ge=100, le=2000)
    temperature: float = Field(default=0.1, ge=0.0, le=1.0)
    rate_limit_per_minute: int = Field(default=10, ge=1, le=60)
    request_timeout_sec: int = Field(default=20, ge=5, le=120)
    retry_attempts: int = Field(default=2, ge=0, le=5)
    retry_backoff_sec: float = Field(default=1.0, ge=0.1, le=10.0)


class ExecutionConfig(BaseModel):
    """Order execution configuration."""

    max_slippage_pct: float = Field(default=0.5, ge=0.1, le=2.0)
    limit_order_buffer_pct: float = Field(default=0.05, ge=0.01, le=0.2)
    max_spread_pct: float = Field(default=0.3, ge=0.05, le=1.0)
    retry_attempts: int = Field(default=3, ge=1, le=10)
    retry_initial_delay_ms: int = Field(default=1000, ge=100, le=5000)
    retry_max_delay_ms: int = Field(default=10000, ge=1000, le=60000)
    order_timeout_sec: int = Field(default=30, ge=10, le=300)
    margin_type: Literal["ISOLATED", "CROSSED"] = "ISOLATED"
    position_mode: Literal["ONE_WAY", "HEDGE"] = "ONE_WAY"
    # Dynamic spread threshold settings (ATR-based)
    use_dynamic_spread_threshold: bool = Field(
        default=True,
        description="Use ATR-based dynamic spread thresholds instead of fixed max_spread_pct",
    )
    spread_threshold_calm_pct: float = Field(
        default=0.05,
        ge=0.01,
        le=0.5,
        description="Max spread when ATR < atr_calm_threshold (calm market)",
    )
    spread_threshold_normal_pct: float = Field(
        default=0.10,
        ge=0.02,
        le=0.5,
        description="Max spread when ATR between calm and volatile thresholds",
    )
    spread_threshold_volatile_pct: float = Field(
        default=0.20,
        ge=0.05,
        le=1.0,
        description="Max spread when ATR > atr_volatile_threshold (volatile market)",
    )
    atr_calm_threshold: float = Field(
        default=2.0,
        ge=0.5,
        le=5.0,
        description="ATR percentage threshold below which market is considered calm",
    )
    atr_volatile_threshold: float = Field(
        default=4.0,
        ge=1.0,
        le=10.0,
        description="ATR percentage threshold above which market is considered volatile",
    )
    # Entry order lifecycle settings
    entry_timeout_mode: Literal["fixed", "timeframe", "unlimited"] = Field(
        default="timeframe",
        description="Timeout mode: 'fixed' (30s default), 'timeframe' (until next candle), 'unlimited' (GTC)",
    )
    entry_fallback_timeout_sec: int = Field(
        default=60,
        ge=10,
        le=600,
        description="Fallback timeout in seconds for 'timeframe' mode if next candle takes too long",
    )
    entry_max_duration_sec: int = Field(
        default=3600,
        ge=60,
        le=14400,
        description="Max duration for 'unlimited' mode before forcing expiration",
    )
    entry_expired_action: Literal["cancel", "convert_market", "convert_stop"] = Field(
        default="cancel",
        description="Action when order expires: 'cancel' (let signal retry next candle), 'convert_market' (market order), 'convert_stop' (stop-market entry)",
    )
    entry_lifecycle_enabled: bool = Field(
        default=True,
        description="Enable order lifecycle tracking across strategy cycles",
    )


class BacktestConfig(BaseModel):
    """Backtesting configuration."""

    execution_model: Literal["ideal", "realistic"] = Field(
        default="ideal",
        description="Execution model: 'ideal' (fixed slippage) or 'realistic' (variable slippage, fill probability)",
    )
    slippage_base_bps: float = Field(default=2.0, ge=0.0, le=20.0)
    slippage_atr_scale: float = Field(default=1.0, ge=0.0, le=5.0)
    fill_probability_model: bool = Field(default=True)
    random_seed: int | None = Field(default=None, ge=0)
    # Spread model settings
    spread_model: Literal["none", "conservative"] = Field(
        default="conservative",
        description="Spread model: 'none' (no spread gating) or 'conservative' (ATR-scaled model)",
    )
    spread_base_pct: float = Field(
        default=0.01,
        ge=0.0,
        le=0.5,
        description="Base spread percentage in calm market conditions",
    )
    spread_atr_scale: float = Field(
        default=0.5,
        ge=0.0,
        le=2.0,
        description="How much ATR affects spread (0.5 = spread increases by 0.5% per 1% ATR)",
    )
    max_spread_pct: float = Field(
        default=0.3,
        ge=0.05,
        le=1.0,
        description="Maximum allowed spread percentage for entry (rejects trades above this)",
    )


class PaperSimConfig(BaseModel):
    """Paper mode execution simulation configuration.

    Controls realistic fill behavior in paper mode including slippage,
    fill probability, and fee calculations.
    """

    enabled: bool = Field(
        default=True,
        description="Enable realistic execution simulation in paper mode",
    )
    slippage_base_bps: float = Field(
        default=2.0,
        ge=0.0,
        le=20.0,
        description="Base slippage in basis points (2 bps = 0.02%)",
    )
    slippage_atr_scale: float = Field(
        default=1.0,
        ge=0.0,
        le=5.0,
        description="Scaling factor for ATR-based slippage component",
    )
    maker_fee_pct: float = Field(
        default=0.02,
        ge=0.0,
        le=0.5,
        description="Maker fee percentage (0.02 = 0.02%)",
    )
    taker_fee_pct: float = Field(
        default=0.04,
        ge=0.0,
        le=0.5,
        description="Taker fee percentage (0.04 = 0.04%)",
    )
    random_seed: int | None = Field(
        default=None,
        ge=0,
        description="Random seed for reproducible simulation results",
    )
    partial_fill_rate: float = Field(
        default=0.20,
        ge=0.0,
        le=1.0,
        description="Probability of partial fills when order fills (0.20 = 20%)",
    )
    use_live_book_ticker: bool = Field(
        default=True,
        description="Fetch real bookTicker data for spread calculations",
    )
    book_ticker_cache_seconds: float = Field(
        default=1.0,
        ge=0.1,
        le=60.0,
        description="Cache duration for bookTicker data in seconds",
    )


class StorageConfig(BaseModel):
    """Storage paths configuration."""

    ledger_path: str = "./data/ledger"
    state_path: str = "./data/state"
    logs_path: str = "./logs"
    data_path: str = "./data/market"


class MonitoringConfig(BaseModel):
    """Monitoring and alerting configuration."""

    metrics_port: int = Field(default=9090, ge=1024, le=65535)
    api_port: int = Field(default=8000, ge=1024, le=65535)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    alert_webhooks: list[str] = Field(default_factory=list)
    log_http: bool = False
    log_http_responses: bool = False
    log_http_max_body_chars: int = Field(default=500, ge=0, le=5000)
    error_log_max_bytes: int = Field(default=5_000_000, ge=100_000, le=50_000_000)
    error_log_backup_count: int = Field(default=3, ge=1, le=20)


class ReconciliationConfig(BaseModel):
    """Continuous reconciliation loop configuration."""

    enabled: bool = True
    interval_minutes: int = Field(default=30, ge=5, le=1440)
    failure_threshold: int = Field(default=3, ge=1, le=10)
    allow_in_paper: bool = False


class WatchdogConfig(BaseModel):
    """Protective order watchdog configuration."""

    enabled: bool = Field(default=True)
    interval_sec: int = Field(default=300, ge=30, le=3600)
    auto_recover: bool = Field(default=True)


class Settings(BaseSettings):
    """Main application settings."""

    environment: Literal["testnet", "production"] = "testnet"
    run: RunConfig = Field(default_factory=RunConfig)

    # API credentials from environment
    binance_api_key: str = Field(default="", alias="BINANCE_API_KEY")
    binance_secret_key: str = Field(default="", alias="BINANCE_SECRET_KEY")
    binance_testnet_api_key: str = Field(default="", alias="BINANCE_TESTNET_FUTURE_API_KEY")
    binance_testnet_secret_key: str = Field(default="", alias="BINANCE_TESTNET_FUTURE_SECRET_KEY")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")

    # Sub-configurations
    binance: BinanceConfig = Field(default_factory=BinanceConfig)
    strategy: StrategyConfig = Field(default_factory=StrategyConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    universe: UniverseConfig = Field(default_factory=UniverseConfig)
    news: NewsConfig = Field(default_factory=NewsConfig)
    regime: RegimeConfig = Field(default_factory=RegimeConfig)
    crowding: CrowdingConfig = Field(default_factory=CrowdingConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    backtest: BacktestConfig = Field(default_factory=BacktestConfig)
    paper_sim: PaperSimConfig = Field(default_factory=PaperSimConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    monitoring: MonitoringConfig = Field(default_factory=MonitoringConfig)
    reconciliation: ReconciliationConfig = Field(default_factory=ReconciliationConfig)
    watchdog: WatchdogConfig = Field(default_factory=WatchdogConfig)

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
        "env_nested_delimiter": "__",
        "populate_by_name": True,
    }

    @property
    def binance_base_url(self) -> str:
        """Get the appropriate Binance base URL based on environment."""
        if self.run.mode == "testnet":
            return self.binance.testnet_base_url
        if self.run.mode == "live":
            return self.binance.base_url
        if self.environment == "testnet":
            return self.binance.testnet_base_url
        return self.binance.base_url

    @property
    def binance_ws_url(self) -> str:
        """Get the appropriate Binance WebSocket URL based on environment."""
        if self.run.mode == "testnet":
            return self.binance.testnet_ws_url
        if self.run.mode == "live":
            return self.binance.ws_url
        if self.environment == "testnet":
            return self.binance.testnet_ws_url
        return self.binance.ws_url

    @property
    def binance_market_data_base_url(self) -> str:
        """Get the appropriate market data base URL.

        Returns production market data URL if use_production_market_data is enabled,
        otherwise returns the trading URL for the current environment.
        """
        if self.binance.use_production_market_data:
            return self.binance.market_data_base_url
        return self.binance_base_url

    @property
    def active_binance_api_key(self) -> str:
        """Return the API key for the active environment."""
        if self.run.mode == "testnet" and self.binance_testnet_api_key:
            return self.binance_testnet_api_key
        if self.run.mode == "live" and self.binance_api_key:
            return self.binance_api_key
        if self.environment == "testnet" and self.binance_testnet_api_key:
            return self.binance_testnet_api_key
        return self.binance_api_key

    @property
    def active_binance_secret_key(self) -> str:
        """Return the API secret for the active environment."""
        if self.run.mode == "testnet" and self.binance_testnet_secret_key:
            return self.binance_testnet_secret_key
        if self.run.mode == "live" and self.binance_secret_key:
            return self.binance_secret_key
        if self.environment == "testnet" and self.binance_testnet_secret_key:
            return self.binance_testnet_secret_key
        return self.binance_secret_key

    def trading_gate(self) -> tuple[bool, list[str]]:
        """Return whether trading is allowed along with blocking reasons."""
        reasons: list[str] = []
        if self.run.mode == "paper":
            reasons.append("RUN_MODE_PAPER")
        if not self.run.enable_trading:
            reasons.append("RUN_ENABLE_TRADING_FALSE")
        if self.run.mode == "testnet":
            if not self.binance_testnet_api_key:
                reasons.append("BINANCE_TESTNET_FUTURE_API_KEY not set")
            if not self.binance_testnet_secret_key:
                reasons.append("BINANCE_TESTNET_FUTURE_SECRET_KEY not set")
        if self.run.mode == "live":
            if not self.binance_api_key:
                reasons.append("BINANCE_API_KEY not set")
            if not self.binance_secret_key:
                reasons.append("BINANCE_SECRET_KEY not set")
            if self.run.live_confirm != "YES_I_UNDERSTAND":
                reasons.append("RUN_LIVE_CONFIRM missing/invalid")
        return (len(reasons) == 0, reasons)

    def validate_for_trading(self) -> list[str]:
        """Validate settings are suitable for live trading. Returns list of errors."""
        errors = []

        if self.run.mode == "testnet":
            if not self.binance_testnet_api_key:
                errors.append("BINANCE_TESTNET_FUTURE_API_KEY not set")
            if not self.binance_testnet_secret_key:
                errors.append("BINANCE_TESTNET_FUTURE_SECRET_KEY not set")
        if self.run.mode == "live":
            if not self.binance_api_key:
                errors.append("BINANCE_API_KEY not set")
            if not self.binance_secret_key:
                errors.append("BINANCE_SECRET_KEY not set")
            if self.run.live_confirm != "YES_I_UNDERSTAND":
                errors.append("RUN_LIVE_CONFIRM missing/invalid")

        if self.risk.max_leverage > 10:
            errors.append("max_leverage exceeds safe limit of 10x")

        if self.risk.risk_per_trade_pct > 2.0:
            errors.append("risk_per_trade_pct exceeds safe limit of 2%")

        if self.run.mode == "live" and self.risk.max_positions > 2:
            errors.append("max_positions > 2 not recommended for live mode")

        return errors


def load_settings(config_path: str | Path | None = None) -> Settings:
    """
    Load settings from YAML config file and environment variables.

    Priority (highest to lowest):
    1. Environment variables
    2. Config file values
    3. Default values
    """
    config_data = {}

    # Try to load from config file
    if config_path is None:
        config_path = os.environ.get("CONFIG_PATH", "config.yaml")

    config_file = Path(config_path)
    if config_file.exists():
        with open(config_file) as f:
            config_data = yaml.safe_load(f) or {}

    run_overrides = {}
    env_run_mode = os.environ.get("RUN_MODE")
    env_run_enable = os.environ.get("RUN_ENABLE_TRADING")
    env_run_confirm = os.environ.get("RUN_LIVE_CONFIRM")
    if env_run_mode:
        run_overrides["mode"] = env_run_mode
    if env_run_enable is not None:
        run_overrides["enable_trading"] = env_run_enable
    if env_run_confirm is not None:
        run_overrides["live_confirm"] = env_run_confirm
    if run_overrides:
        config_data.setdefault("run", {}).update(run_overrides)

    # Build settings with config data as defaults
    env_path = config_file.parent / ".env"
    settings = Settings(**config_data, _env_file=env_path)

    return settings


def create_default_config(path: str | Path = "config.yaml") -> None:
    """Create a default configuration file."""
    default_config = {
        "environment": "testnet",
        "run": {
            "mode": "paper",
            "enable_trading": False,
            "live_confirm": "",
        },
        "binance": {
            "base_url": "https://fapi.binance.com",
            "ws_url": "wss://fstream.binance.com",
            "recv_window": 5000,
        },
        "strategy": {
            "name": "trend_following_v1",
            "trend_timeframe": "1d",
            "entry_timeframe": "4h",
            "indicators": {
                "ema_fast": 8,
                "ema_slow": 21,
                "atr_period": 14,
            },
            "entry": {
                "style": "pullback",
                "score_threshold": 0.65,
            },
            "exit": {
                "atr_stop_multiplier": 2.0,
                "trailing_start_atr": 1.5,
                "trailing_distance_atr": 1.5,
                "time_stop_days": 7,
            },
        },
        "risk": {
            "risk_per_trade_pct": 1.0,
            "max_leverage": 5,
            "default_leverage": 3,
            "max_daily_loss_pct": 3.0,
            "max_drawdown_pct": 10.0,
            "max_consecutive_losses": 3,
            "max_loss_streak_24h": 5,
            "loss_streak_window_hours": 24,
            "max_positions": 1,
        },
        "universe": {
            "min_quote_volume_usd": 50000000,
            "size": 5,
            "refresh_interval_hours": 24,
            "retry_interval_minutes": 5,
        },
        "news": {
            "enabled": True,
            "classifier_provider": "rules",
            "poll_interval_minutes": 15,
            "max_age_hours": 24,
            "high_risk_block_hours": 24,
            "medium_risk_block_hours": 6,
            "source_timeout_sec": 10,
            "sources": [
                {
                    "url": "https://www.coindesk.com/arc/outboundfeeds/rss/",
                    "name": "coindesk",
                    "enabled": True,
                }
            ],
        },
        "llm": {
            "provider": "openai",
            "model": "gpt-4-turbo-preview",
            "max_tokens": 500,
            "temperature": 0.1,
            "rate_limit_per_minute": 10,
            "request_timeout_sec": 20,
            "retry_attempts": 2,
            "retry_backoff_sec": 1.0,
        },
        "execution": {
            "max_slippage_pct": 0.5,
            "limit_order_buffer_pct": 0.05,
            "max_spread_pct": 0.3,
            "retry_attempts": 3,
            "retry_initial_delay_ms": 1000,
            "retry_max_delay_ms": 10000,
            "order_timeout_sec": 30,
            "margin_type": "ISOLATED",
            "position_mode": "ONE_WAY",
        },
        "storage": {
            "ledger_path": "./data/ledger",
            "state_path": "./data/state",
            "logs_path": "./logs",
            "data_path": "./data/market",
        },
        "monitoring": {
            "metrics_port": 9090,
            "log_level": "INFO",
            "alert_webhooks": [],
            "log_http": False,
            "log_http_responses": False,
            "log_http_max_body_chars": 500,
            "error_log_max_bytes": 5000000,
            "error_log_backup_count": 3,
        },
        "reconciliation": {
            "enabled": True,
            "interval_minutes": 30,
            "failure_threshold": 3,
            "allow_in_paper": False,
        },
    }

    with open(path, "w") as f:
        yaml.dump(default_config, f, default_flow_style=False, sort_keys=False)
