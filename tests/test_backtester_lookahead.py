"""Tests to ensure backtester has no lookahead bias."""

import pandas as pd

from src.backtester.engine import Backtester
from src.config.settings import RiskConfig, StrategyConfig


def _create_4h_data(n_bars: int = 100, start_date: str = "2024-01-01 04:00:00") -> pd.DataFrame:
    """Create synthetic 4h OHLCV data indexed by close_time (UTC)."""
    dates = pd.date_range(
        start=start_date,
        periods=n_bars,
        freq="4h",
        tz="UTC",
    )
    return pd.DataFrame(
        {
            "open": [100.0 + i * 0.1 for i in range(n_bars)],
            "high": [101.0 + i * 0.1 for i in range(n_bars)],
            "low": [99.0 + i * 0.1 for i in range(n_bars)],
            "close": [100.5 + i * 0.1 for i in range(n_bars)],
            "volume": [1000.0] * n_bars,
        },
        index=dates,
    )


def test_daily_features_independent_of_future_4h_bars() -> None:
    """Verify that daily features at time T do not depend on 4h bars after T.

    Method: Run backtest twice on same data but with extra future rows appended
    in the second run. Signals at earlier timestamps must be identical.
    """
    base_data = _create_4h_data(200)

    # Extended data with 50 more future bars
    extended_data = _create_4h_data(250)

    backtester = Backtester(
        strategy_config=StrategyConfig(),
        risk_config=RiskConfig(),
    )

    result_base = backtester.run("TESTUSDT", base_data)
    result_extended = backtester.run("TESTUSDT", extended_data)

    # Compare trades that occur within the base data timeframe
    base_end = base_data.index[-1]

    base_trades = [
        t for t in result_base.trades if t.entry_time <= base_end.to_pydatetime()
    ]
    extended_trades = [
        t for t in result_extended.trades if t.entry_time <= base_end.to_pydatetime()
    ]

    # Same number of trades in the overlapping period
    assert len(base_trades) == len(extended_trades), (
        f"Trade count differs: {len(base_trades)} vs {len(extended_trades)}. "
        "This indicates lookahead bias - future data affected past signals."
    )

    # Each trade should have identical entry time and price
    for base_trade, ext_trade in zip(base_trades, extended_trades):
        assert base_trade.entry_time == ext_trade.entry_time, (
            f"Entry time mismatch: {base_trade.entry_time} vs {ext_trade.entry_time}"
        )
        assert base_trade.entry_price == ext_trade.entry_price, (
            f"Entry price mismatch: {base_trade.entry_price} vs {ext_trade.entry_price}"
        )


def test_daily_bar_excludes_future_4h_candles() -> None:
    """Directly test that _get_daily_at_time() excludes future 4h candles."""
    backtester = Backtester(
        strategy_config=StrategyConfig(),
        risk_config=RiskConfig(),
    )

    # Create data spanning 3 days (18 x 4h bars = 3 days)
    # 4h bars per day: 04:00, 08:00, 12:00, 16:00, 20:00, 00:00(next day)
    fourh = _create_4h_data(18, start_date="2024-01-01 04:00:00")

    # At 12:00 on day 1, daily bar should NOT include 4h candles from 16:00, 20:00, 00:00
    # Index 2 is the 12:00 UTC bar (04:00, 08:00, 12:00)
    midday_time = fourh.index[2]  # 2024-01-01 12:00:00 UTC

    daily = backtester._get_daily_at_time(fourh, midday_time)

    # At 12:00 on day 1, we have no complete daily bars yet
    # (day 1 is still in progress), so should be empty
    assert daily.empty, (
        "Daily bar should be empty at midday - no complete daily bar yet"
    )


def test_daily_bar_at_daily_close() -> None:
    """At 00:00 UTC (daily close), the daily bar for the previous day should be complete."""
    backtester = Backtester(
        strategy_config=StrategyConfig(),
        risk_config=RiskConfig(),
    )

    # Create data with 2 complete days (12 x 4h bars)
    # Day 1: 04:00, 08:00, 12:00, 16:00, 20:00, 00:00(close)
    # Day 2: 04:00, 08:00, 12:00, 16:00, 20:00, 00:00(close)
    fourh = _create_4h_data(12, start_date="2024-01-01 04:00:00")

    # At 00:00 Jan 2 (6th bar, index 5) - this is the daily close for Jan 1
    daily_close_time = fourh.index[5]  # 2024-01-02 00:00:00 UTC

    daily = backtester._get_daily_at_time(fourh, daily_close_time)

    # Should have exactly 1 complete daily bar for 2024-01-01
    # The index is the close_time (00:00 Jan 2) not the open date
    assert len(daily) == 1, f"Expected 1 daily bar, got {len(daily)}"
    assert daily.index[0] == pd.Timestamp("2024-01-02 00:00:00", tz="UTC")


def test_daily_bar_uses_correct_4h_candles() -> None:
    """Verify the daily bar aggregation uses only appropriate 4h candles."""
    backtester = Backtester(
        strategy_config=StrategyConfig(),
        risk_config=RiskConfig(),
    )

    # Create specific data where we can verify aggregation
    dates = pd.date_range(
        start="2024-01-01 04:00:00",
        periods=12,
        freq="4h",
        tz="UTC",
    )

    # Day 1 bars (indices 0-5): highs are 101-106
    # Day 2 bars (indices 6-11): highs are 107-112
    fourh = pd.DataFrame(
        {
            "open": [100.0] * 12,
            "high": [101.0 + i for i in range(12)],  # 101, 102, ..., 112
            "low": [99.0] * 12,
            "close": [100.0] * 12,
            "volume": [1000.0] * 12,
        },
        index=dates,
    )

    # At the daily close of Jan 2 (index 5: 2024-01-02 00:00)
    daily_close = fourh.index[5]
    daily = backtester._get_daily_at_time(fourh, daily_close)

    # Day 1's high should be max of indices 0-5 = 106 (bar at 00:00 has high 106)
    assert len(daily) == 1
    assert daily.iloc[0]["high"] == 106.0, (
        f"Expected high 106.0 (max of 4h bars 0-5), got {daily.iloc[0]['high']}"
    )

    # At midday on Jan 2 (index 8: 2024-01-02 12:00)
    # Only day 1 is complete, day 2 is in progress
    midday_jan2 = fourh.index[8]
    daily_at_midday = backtester._get_daily_at_time(fourh, midday_jan2)

    # Should still only have 1 bar (day 1), day 2 is incomplete
    assert len(daily_at_midday) == 1
    assert daily_at_midday.iloc[0]["high"] == 106.0


def test_no_lookahead_with_varying_data_lengths() -> None:
    """Test that signals are consistent regardless of how much future data exists."""
    backtester = Backtester(
        strategy_config=StrategyConfig(),
        risk_config=RiskConfig(),
    )

    # Create datasets of varying lengths, all starting from the same point
    data_100 = _create_4h_data(100)
    data_150 = _create_4h_data(150)
    data_200 = _create_4h_data(200)

    result_100 = backtester.run("TESTUSDT", data_100)
    result_150 = backtester.run("TESTUSDT", data_150)
    result_200 = backtester.run("TESTUSDT", data_200)

    # Get trades up to bar 100
    cutoff = data_100.index[-1].to_pydatetime()

    trades_100 = [t for t in result_100.trades if t.entry_time <= cutoff]
    trades_150 = [t for t in result_150.trades if t.entry_time <= cutoff]
    trades_200 = [t for t in result_200.trades if t.entry_time <= cutoff]

    # All should have same number of trades in the overlapping period
    assert len(trades_100) == len(trades_150) == len(trades_200), (
        f"Trade counts differ: 100={len(trades_100)}, "
        f"150={len(trades_150)}, 200={len(trades_200)}"
    )
