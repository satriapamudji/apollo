from src.strategy.scoring import ScoringEngine


def test_scoring_news_modifier_high_blocks() -> None:
    scoring = ScoringEngine()
    score_high = scoring.compute(
        direction="LONG",
        price=100.0,
        ema_fast=105.0,
        ema_slow=100.0,
        ema_fast_3bars_ago=102.0,
        atr=3.0,
        entry_distance_atr=0.8,
        funding_rate=0.0,
        news_risk="HIGH",
    )
    score_low = scoring.compute(
        direction="LONG",
        price=100.0,
        ema_fast=105.0,
        ema_slow=100.0,
        ema_fast_3bars_ago=102.0,
        atr=3.0,
        entry_distance_atr=0.8,
        funding_rate=0.0,
        news_risk="LOW",
    )
    assert score_high.news_modifier == 0.0
    assert score_low.news_modifier == 1.0
    assert 0.0 <= score_high.composite <= 1.0
    assert score_high.composite <= score_low.composite
