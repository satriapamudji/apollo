"""Tests for backtester event-based funding model.

Tests cover:
- LONG + positive rate reduces equity; SHORT increases equity by same magnitude
- Negative rate flips the payer/receiver
- Funding applied exactly once per settlement even if multiple bars occur after settlement
- No leverage scaling in cashflow calculation
- Historical irregular settlement schedule handling
- Synthetic settlement schedule for constant rate mode
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from src.backtester.funding import FundingRateInfo, FundingRateProvider


class TestFundingCashflowDirection:
    """Tests for correct payer/receiver direction based on position side."""

    def test_long_positive_rate_pays(self) -> None:
        """LONG position with positive rate should pay (positive cashflow)."""
        provider = FundingRateProvider()
        cashflow = provider.calculate_funding_cashflow(
            notional=10000.0,
            funding_rate=0.0001,  # 0.01%
            position_side="LONG",
        )
        # LONG pays on positive rate: 10000 * 0.0001 = 1.0
        assert cashflow == pytest.approx(1.0)
        assert cashflow > 0  # Positive = pay

    def test_short_positive_rate_receives(self) -> None:
        """SHORT position with positive rate should receive (negative cashflow)."""
        provider = FundingRateProvider()
        cashflow = provider.calculate_funding_cashflow(
            notional=10000.0,
            funding_rate=0.0001,  # 0.01%
            position_side="SHORT",
        )
        # SHORT receives on positive rate: -10000 * 0.0001 = -1.0
        assert cashflow == pytest.approx(-1.0)
        assert cashflow < 0  # Negative = receive

    def test_long_negative_rate_receives(self) -> None:
        """LONG position with negative rate should receive (negative cashflow)."""
        provider = FundingRateProvider()
        cashflow = provider.calculate_funding_cashflow(
            notional=10000.0,
            funding_rate=-0.0001,  # -0.01%
            position_side="LONG",
        )
        # LONG receives on negative rate: 10000 * (-0.0001) = -1.0
        assert cashflow == pytest.approx(-1.0)
        assert cashflow < 0  # Negative = receive

    def test_short_negative_rate_pays(self) -> None:
        """SHORT position with negative rate should pay (positive cashflow)."""
        provider = FundingRateProvider()
        cashflow = provider.calculate_funding_cashflow(
            notional=10000.0,
            funding_rate=-0.0001,  # -0.01%
            position_side="SHORT",
        )
        # SHORT pays on negative rate: -10000 * (-0.0001) = 1.0
        assert cashflow == pytest.approx(1.0)
        assert cashflow > 0  # Positive = pay

    def test_symmetry_long_short(self) -> None:
        """LONG and SHORT should have equal and opposite cashflows."""
        provider = FundingRateProvider()
        notional = 50000.0
        rate = 0.0003

        long_cashflow = provider.calculate_funding_cashflow(
            notional=notional,
            funding_rate=rate,
            position_side="LONG",
        )
        short_cashflow = provider.calculate_funding_cashflow(
            notional=notional,
            funding_rate=rate,
            position_side="SHORT",
        )

        # Equal magnitudes, opposite signs
        assert abs(long_cashflow) == pytest.approx(abs(short_cashflow))
        assert long_cashflow == -short_cashflow


class TestNoLeverageMultiplier:
    """Tests to ensure no leverage multiplier is applied to funding."""

    def test_funding_based_on_notional_only(self) -> None:
        """Funding should be calculated on notional, not notional * leverage."""
        provider = FundingRateProvider()

        # Calculate funding for notional of 10000
        cashflow = provider.calculate_funding_cashflow(
            notional=10000.0,
            funding_rate=0.0001,
            position_side="LONG",
        )

        # Should be exactly notional * rate, with no leverage multiplier
        expected = 10000.0 * 0.0001
        assert cashflow == pytest.approx(expected)

        # Verify there's no hidden leverage (like 10x which was the old bug)
        assert cashflow != pytest.approx(expected * 10)
        assert cashflow != pytest.approx(expected * 5)


class TestIterFundingEventsHistorical:
    """Tests for iterating funding events from historical data."""

    @pytest.fixture
    def historical_funding_data(self) -> pd.DataFrame:
        """Create sample historical funding data."""
        times = [
            datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc),
            datetime(2024, 1, 1, 8, 0, tzinfo=timezone.utc),
            datetime(2024, 1, 1, 16, 0, tzinfo=timezone.utc),
            datetime(2024, 1, 2, 0, 0, tzinfo=timezone.utc),
            datetime(2024, 1, 2, 8, 0, tzinfo=timezone.utc),
        ]
        df = pd.DataFrame(
            {
                "funding_rate": [0.0001, 0.0002, -0.0001, 0.0003, 0.00015],
                "mark_price": [50000.0, 50100.0, 49900.0, 50200.0, 50150.0],
            },
            index=pd.DatetimeIndex(times),
        )
        return df

    def test_iter_events_half_open_interval(self, historical_funding_data: pd.DataFrame) -> None:
        """Events should be in (start, end] - exclusive start, inclusive end."""
        provider = FundingRateProvider(funding_data=historical_funding_data)

        start = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
        end = datetime(2024, 1, 1, 16, 0, tzinfo=timezone.utc)

        events = list(provider.iter_funding_events(start, end))

        # Should include 08:00 and 16:00, but NOT 00:00 (start is exclusive)
        assert len(events) == 2
        assert events[0].funding_time == datetime(2024, 1, 1, 8, 0, tzinfo=timezone.utc)
        assert events[1].funding_time == datetime(2024, 1, 1, 16, 0, tzinfo=timezone.utc)

    def test_iter_events_includes_mark_price(self, historical_funding_data: pd.DataFrame) -> None:
        """Events should include mark price from historical data."""
        provider = FundingRateProvider(funding_data=historical_funding_data)

        start = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
        end = datetime(2024, 1, 1, 8, 1, tzinfo=timezone.utc)

        events = list(provider.iter_funding_events(start, end))

        assert len(events) == 1
        assert events[0].mark_price == pytest.approx(50100.0)
        assert events[0].rate == pytest.approx(0.0002)

    def test_iter_events_empty_range(self, historical_funding_data: pd.DataFrame) -> None:
        """No events should be returned for empty range."""
        provider = FundingRateProvider(funding_data=historical_funding_data)

        start = datetime(2024, 1, 1, 9, 0, tzinfo=timezone.utc)
        end = datetime(2024, 1, 1, 15, 0, tzinfo=timezone.utc)

        events = list(provider.iter_funding_events(start, end))
        assert len(events) == 0

    def test_iter_events_single_event(self, historical_funding_data: pd.DataFrame) -> None:
        """Should correctly return a single event."""
        provider = FundingRateProvider(funding_data=historical_funding_data)

        start = datetime(2024, 1, 1, 15, 59, tzinfo=timezone.utc)
        end = datetime(2024, 1, 1, 16, 0, tzinfo=timezone.utc)

        events = list(provider.iter_funding_events(start, end))

        assert len(events) == 1
        assert events[0].funding_time == datetime(2024, 1, 1, 16, 0, tzinfo=timezone.utc)
        assert events[0].rate == pytest.approx(-0.0001)


class TestIterFundingEventsSynthetic:
    """Tests for synthetic settlement schedule in constant rate mode."""

    def test_synthetic_settlements_at_standard_times(self) -> None:
        """Synthetic settlements should occur at 00:00, 08:00, 16:00 UTC."""
        provider = FundingRateProvider(constant_rate=0.0001)

        start = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
        end = datetime(2024, 1, 1, 23, 59, tzinfo=timezone.utc)

        events = list(provider.iter_funding_events(start, end))

        # Should have 08:00 and 16:00 (00:00 is excluded as start is exclusive)
        assert len(events) == 2
        assert events[0].funding_time.hour == 8
        assert events[1].funding_time.hour == 16

    def test_synthetic_settlements_span_days(self) -> None:
        """Synthetic settlements should correctly span multiple days."""
        provider = FundingRateProvider(constant_rate=0.0001)

        start = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
        end = datetime(2024, 1, 2, 16, 0, tzinfo=timezone.utc)

        events = list(provider.iter_funding_events(start, end))

        # Day 1: 08:00, 16:00; Day 2: 00:00, 08:00, 16:00 = 5 events
        assert len(events) == 5

        expected_times = [
            datetime(2024, 1, 1, 8, 0, tzinfo=timezone.utc),
            datetime(2024, 1, 1, 16, 0, tzinfo=timezone.utc),
            datetime(2024, 1, 2, 0, 0, tzinfo=timezone.utc),
            datetime(2024, 1, 2, 8, 0, tzinfo=timezone.utc),
            datetime(2024, 1, 2, 16, 0, tzinfo=timezone.utc),
        ]

        for event, expected in zip(events, expected_times):
            assert event.funding_time == expected

    def test_synthetic_all_events_have_constant_rate(self) -> None:
        """All synthetic events should have the constant rate."""
        constant_rate = 0.00025
        provider = FundingRateProvider(constant_rate=constant_rate)

        start = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
        end = datetime(2024, 1, 2, 0, 0, tzinfo=timezone.utc)

        events = list(provider.iter_funding_events(start, end))

        for event in events:
            assert event.rate == pytest.approx(constant_rate)
            assert event.mark_price is None  # No mark price in synthetic mode

    def test_synthetic_partial_day_start(self) -> None:
        """Synthetic settlements should work with mid-day start times."""
        provider = FundingRateProvider(constant_rate=0.0001)

        start = datetime(2024, 1, 1, 10, 0, tzinfo=timezone.utc)
        end = datetime(2024, 1, 1, 20, 0, tzinfo=timezone.utc)

        events = list(provider.iter_funding_events(start, end))

        # Only 16:00 should be in range
        assert len(events) == 1
        assert events[0].funding_time == datetime(2024, 1, 1, 16, 0, tzinfo=timezone.utc)


class TestApplyOncePerSettlement:
    """Tests to verify funding is applied exactly once per settlement."""

    def test_multiple_bars_same_settlement(self) -> None:
        """Multiple bars after a settlement should not trigger multiple applications."""
        # Create funding data with a settlement at 08:00
        times = [
            datetime(2024, 1, 1, 8, 0, tzinfo=timezone.utc),
        ]
        funding_df = pd.DataFrame(
            {
                "funding_rate": [0.0001],
            },
            index=pd.DatetimeIndex(times),
        )

        provider = FundingRateProvider(funding_data=funding_df)

        # First bar at 08:00 - should get the event
        events1 = list(
            provider.iter_funding_events(
                datetime(2024, 1, 1, 4, 0, tzinfo=timezone.utc),
                datetime(2024, 1, 1, 8, 0, tzinfo=timezone.utc),
            )
        )
        assert len(events1) == 1

        # Simulate second bar at 12:00 - using 08:00 as start (last applied)
        # Should NOT get the same event again
        events2 = list(
            provider.iter_funding_events(
                datetime(2024, 1, 1, 8, 0, tzinfo=timezone.utc),  # Start at last settlement
                datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc),
            )
        )
        assert len(events2) == 0

    def test_irregular_settlement_schedule(self) -> None:
        """Handle non-standard settlement schedules (e.g., hourly during volatility)."""
        # Irregular schedule with multiple hourly settlements
        times = [
            datetime(2024, 1, 1, 8, 0, tzinfo=timezone.utc),
            datetime(2024, 1, 1, 9, 0, tzinfo=timezone.utc),  # Irregular
            datetime(2024, 1, 1, 10, 0, tzinfo=timezone.utc),  # Irregular
            datetime(2024, 1, 1, 16, 0, tzinfo=timezone.utc),
        ]
        funding_df = pd.DataFrame(
            {
                "funding_rate": [0.0001, 0.0002, 0.00015, 0.0001],
            },
            index=pd.DatetimeIndex(times),
        )

        provider = FundingRateProvider(funding_data=funding_df)

        # Get all events from 08:00 to 16:00
        events = list(
            provider.iter_funding_events(
                datetime(2024, 1, 1, 8, 0, tzinfo=timezone.utc),
                datetime(2024, 1, 1, 16, 0, tzinfo=timezone.utc),
            )
        )

        # Should get 09:00, 10:00, 16:00 (08:00 excluded as start is exclusive)
        assert len(events) == 3


class TestProviderModes:
    """Tests for provider mode detection."""

    def test_is_historical_mode(self) -> None:
        """Provider should detect historical mode correctly."""
        times = [datetime(2024, 1, 1, 8, 0, tzinfo=timezone.utc)]
        funding_df = pd.DataFrame(
            {
                "funding_rate": [0.0001],
            },
            index=pd.DatetimeIndex(times),
        )

        provider = FundingRateProvider(funding_data=funding_df)

        assert provider.is_historical_mode()
        assert not provider.is_constant_mode()
        assert not provider.is_no_data_mode()

    def test_is_constant_mode(self) -> None:
        """Provider should detect constant mode correctly."""
        provider = FundingRateProvider(constant_rate=0.0001)

        assert provider.is_constant_mode()
        assert not provider.is_historical_mode()
        assert not provider.is_no_data_mode()

    def test_is_no_data_mode(self) -> None:
        """Provider should detect no-data mode correctly."""
        provider = FundingRateProvider()

        assert provider.is_no_data_mode()
        assert not provider.is_historical_mode()
        assert not provider.is_constant_mode()

    def test_no_data_mode_zero_rate(self) -> None:
        """No-data mode should return zero rate from get_rate()."""
        provider = FundingRateProvider()

        info = provider.get_rate(datetime(2024, 1, 1, 8, 0, tzinfo=timezone.utc))
        assert info.rate == 0.0


class TestGetRate:
    """Tests for the get_rate convenience method."""

    def test_get_rate_historical(self) -> None:
        """get_rate should return the most recent applicable rate."""
        times = [
            datetime(2024, 1, 1, 8, 0, tzinfo=timezone.utc),
            datetime(2024, 1, 1, 16, 0, tzinfo=timezone.utc),
        ]
        funding_df = pd.DataFrame(
            {
                "funding_rate": [0.0001, 0.0002],
                "mark_price": [50000.0, 50100.0],
            },
            index=pd.DatetimeIndex(times),
        )

        provider = FundingRateProvider(funding_data=funding_df)

        # Query at 12:00 - should get the 08:00 rate
        info = provider.get_rate(datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc))
        assert info.rate == pytest.approx(0.0001)
        assert info.funding_time == datetime(2024, 1, 1, 8, 0, tzinfo=timezone.utc)

        # Query at 18:00 - should get the 16:00 rate
        info = provider.get_rate(datetime(2024, 1, 1, 18, 0, tzinfo=timezone.utc))
        assert info.rate == pytest.approx(0.0002)
        assert info.funding_time == datetime(2024, 1, 1, 16, 0, tzinfo=timezone.utc)

    def test_get_rate_constant(self) -> None:
        """get_rate in constant mode should always return the constant rate."""
        provider = FundingRateProvider(constant_rate=0.00015)

        info = provider.get_rate(datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc))
        assert info.rate == pytest.approx(0.00015)

        info = provider.get_rate(datetime(2024, 6, 15, 5, 30, tzinfo=timezone.utc))
        assert info.rate == pytest.approx(0.00015)


class TestFundingRateInfo:
    """Tests for FundingRateInfo dataclass."""

    def test_funding_rate_info_immutable(self) -> None:
        """FundingRateInfo should be immutable (frozen dataclass)."""
        info = FundingRateInfo(
            rate=0.0001,
            funding_time=datetime(2024, 1, 1, 8, 0, tzinfo=timezone.utc),
            mark_price=50000.0,
        )

        with pytest.raises(Exception):  # FrozenInstanceError
            info.rate = 0.0002  # type: ignore[misc]

    def test_funding_rate_info_values(self) -> None:
        """FundingRateInfo should correctly store all values."""
        funding_time = datetime(2024, 1, 1, 8, 0, tzinfo=timezone.utc)
        info = FundingRateInfo(
            rate=0.0001,
            funding_time=funding_time,
            mark_price=50000.0,
        )

        assert info.rate == 0.0001
        assert info.funding_time == funding_time
        assert info.mark_price == 50000.0
