from datetime import datetime, timedelta, timezone

from src.config.settings import NewsConfig, RiskConfig
from src.ledger.events import Event, EventType, utc_now
from src.ledger.state import NewsRisk, Position, StateManager


def test_news_risk_decay_and_blocking() -> None:
    now = datetime.now(timezone.utc)
    risk = NewsRisk(
        symbol="BTC",
        level="HIGH",
        reason="test",
        confidence=0.9,
        last_updated=now - timedelta(hours=13),
    )
    assert risk.level_with_decay(24, 6, now) == "MEDIUM"
    assert risk.blocks_entries(24, now)
    risk_old = NewsRisk(
        symbol="BTC",
        level="HIGH",
        reason="test",
        confidence=0.9,
        last_updated=now - timedelta(hours=25),
    )
    assert risk_old.level_with_decay(24, 6, now) == "LOW"
    assert not risk_old.blocks_entries(24, now)


def test_news_normalization_applies_to_pair() -> None:
    manager = StateManager(
        initial_equity=100.0,
        risk_config=RiskConfig(),
        news_config=NewsConfig(),
    )
    event = Event(
        event_id="n1",
        event_type=EventType.NEWS_CLASSIFIED,
        timestamp=utc_now(),
        sequence_num=1,
        payload={
            "symbols_mentioned": ["BTC"],
            "exchanges_mentioned": [],
            "risk_level": "HIGH",
            "risk_reason": "test",
            "matched_rules": ["hack_exploit"],
            "confidence": 0.9,
        },
    )
    manager.apply_event(event)
    assert manager.get_news_risk("BTCUSDT") == "HIGH"


def test_state_manager_daily_loss_sets_cooldown() -> None:
    risk = RiskConfig(max_daily_loss_pct=1.0, cooldown_after_max_daily_hours=12)
    manager = StateManager(initial_equity=100.0, risk_config=risk, news_config=NewsConfig())
    manager.state.positions["BTCUSDT"] = Position(
        symbol="BTCUSDT",
        side="LONG",
        quantity=0.1,
        entry_price=100.0,
        leverage=3,
        opened_at=utc_now(),
    )
    timestamp = utc_now()
    event = Event(
        event_id="1",
        event_type=EventType.POSITION_CLOSED,
        timestamp=timestamp,
        sequence_num=1,
        payload={"symbol": "BTCUSDT", "realized_pnl": -5.0},
    )
    manager.apply_event(event)
    assert manager.state.cooldown_until is not None
    assert manager.state.cooldown_until == timestamp + timedelta(hours=12)
    assert manager.state.equity == 95.0


def test_state_manager_manual_intervention_flag() -> None:
    manager = StateManager(
        initial_equity=100.0,
        risk_config=RiskConfig(),
        news_config=NewsConfig(),
    )
    manager.state.positions["BTCUSDT"] = Position(
        symbol="BTCUSDT",
        side="LONG",
        quantity=0.1,
        entry_price=100.0,
        leverage=3,
        opened_at=utc_now(),
    )
    event = Event(
        event_id="2",
        event_type=EventType.MANUAL_INTERVENTION,
        timestamp=utc_now(),
        sequence_num=2,
        payload={"action": "POSITION_CLOSED_EXTERNALLY", "symbol": "BTCUSDT"},
    )
    manager.apply_event(event)
    assert manager.state.requires_manual_review
    assert "BTCUSDT" not in manager.state.positions


def test_manual_review_ack_clears_flag() -> None:
    manager = StateManager(
        initial_equity=100.0,
        risk_config=RiskConfig(),
        news_config=NewsConfig(),
    )
    event = Event(
        event_id="3",
        event_type=EventType.MANUAL_INTERVENTION,
        timestamp=utc_now(),
        sequence_num=3,
        payload={"action": "ORDER_MISSING_ON_EXCHANGE", "client_order_id": "abc"},
    )
    manager.apply_event(event)
    assert manager.state.requires_manual_review
    ack_event = Event(
        event_id="4",
        event_type=EventType.MANUAL_REVIEW_ACKNOWLEDGED,
        timestamp=utc_now(),
        sequence_num=4,
        payload={"reason": "ack"},
    )
    manager.apply_event(ack_event)
    assert not manager.state.requires_manual_review
