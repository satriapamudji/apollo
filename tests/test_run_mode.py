from src.config.settings import Settings
from src.execution.engine import ExecutionEngine


class _DummyRest:
    pass


class _DummyBus:
    pass


def test_trading_gate_blocks_paper_mode() -> None:
    settings = Settings(run={"mode": "paper", "enable_trading": True}, _env_file=None)
    allowed, reasons = settings.trading_gate()
    assert not allowed
    assert "RUN_MODE_PAPER" in reasons


def test_trading_gate_requires_enable_trading() -> None:
    settings = Settings(
        run={"mode": "testnet", "enable_trading": False},
        binance_testnet_api_key="k",
        binance_testnet_secret_key="s",
        _env_file=None,
    )
    allowed, reasons = settings.trading_gate()
    assert not allowed
    assert "RUN_ENABLE_TRADING_FALSE" in reasons


def test_trading_gate_requires_live_confirm() -> None:
    settings = Settings(
        run={"mode": "live", "enable_trading": True, "live_confirm": "nope"},
        binance_api_key="k",
        binance_secret_key="s",
        _env_file=None,
    )
    allowed, reasons = settings.trading_gate()
    assert not allowed
    assert "RUN_LIVE_CONFIRM missing/invalid" in reasons


def test_execution_engine_simulates_when_disabled() -> None:
    settings = Settings(
        run={"mode": "testnet", "enable_trading": False},
        binance_testnet_api_key="k",
        binance_testnet_secret_key="s",
        _env_file=None,
    )
    engine = ExecutionEngine(settings, _DummyRest(), _DummyBus())
    assert engine.simulate
