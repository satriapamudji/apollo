"""Main runtime loop for the trading bot."""

from __future__ import annotations

import atexit
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import structlog

from src.config.settings import load_settings
from src.connectors import BinanceRestClient, LLMClassifier, NewsIngester
from src.execution import ExecutionEngine
from src.execution.user_stream import UserDataStream
from src.ledger import EventBus, EventLedger, EventType, StateManager
from src.models import TradeProposal
from src.monitoring import (
    EventConsoleLogger,
    Metrics,
    OrderLogger,
    ThinkingLogger,
    TradeLogger,
    configure_logging,
)
from src.risk.engine import RiskEngine
from src.strategy import SignalGenerator, SignalType, UniverseSelector
from src.utils import parse_symbol_filters
from src.utils.single_instance import SingleInstanceAlreadyRunning, SingleInstanceLock


log = structlog.get_logger(__name__)


def _klines_to_df(klines: list[list[Any]]) -> pd.DataFrame:
    rows = []
    for kline in klines:
        rows.append(
            {
                "open_time": kline[0],
                "open": float(kline[1]),
                "high": float(kline[2]),
                "low": float(kline[3]),
                "close": float(kline[4]),
                "volume": float(kline[5]),
                "close_time": kline[6] if len(kline) > 6 else kline[0],
            }
        )
    df = pd.DataFrame(rows)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms", utc=True)
    now = datetime.now(timezone.utc)
    df = df[df["close_time"] <= now]
    if df.empty:
        return df
    df = df.set_index("close_time")
    return df


async def main_async() -> None:
    settings = load_settings()
    configure_logging(settings.monitoring.log_level)
    errors = settings.validate_for_trading()
    if errors:
        log.error("settings_validation_failed", errors=errors, run_mode=settings.run.mode)
        return

    lock_path = Path(settings.storage.logs_path) / f"bot.{settings.run.mode}.lock"
    instance_lock = SingleInstanceLock(lock_path)
    try:
        instance_lock.acquire()
    except SingleInstanceAlreadyRunning as exc:
        log.error("another_instance_running", lock_path=exc.lock_path, pid=exc.pid)
        return
    atexit.register(instance_lock.release)

    ledger = EventLedger(settings.storage.ledger_path)
    event_bus = EventBus(ledger)
    state_manager = StateManager(
        initial_equity=100.0,
        risk_config=settings.risk,
        news_config=settings.news,
    )
    state_manager.rebuild(ledger.load_all())

    async def apply_event(event):
        state_manager.apply_event(event)

    for event_type in EventType:
        event_bus.register(event_type, apply_event)

    event_console = EventConsoleLogger()
    for event_type in event_console.include:
        event_bus.register(event_type, event_console.handle_event)

    rest = BinanceRestClient(settings)
    news_ingester = NewsIngester(settings.news)
    llm_key = settings.openai_api_key or settings.anthropic_api_key
    llm = LLMClassifier(settings.llm, llm_key)
    risk_engine = RiskEngine(settings.risk)
    signal_generator = SignalGenerator(settings.strategy)
    universe_selector = UniverseSelector(rest, settings.universe)
    execution_engine = ExecutionEngine(settings, rest, event_bus)
    event_bus.register(EventType.ORDER_FILLED, execution_engine.handle_order_filled)
    event_bus.register(EventType.ORDER_CANCELLED, execution_engine.handle_order_cancelled)

    log.info(
        "runtime_config",
        run_mode=settings.run.mode,
        enable_trading=settings.run.enable_trading,
        environment=settings.environment,
        simulate=execution_engine.simulate,
        trading_block_reasons=execution_engine.trading_block_reasons,
    )
    if execution_engine.simulate and execution_engine.trading_block_reasons:
        log.warning(
            "trading_disabled",
            reasons=execution_engine.trading_block_reasons,
        )

    metrics = Metrics()
    metrics.start(settings.monitoring.metrics_port)
    trade_logger = TradeLogger(f"{settings.storage.logs_path}/trades.csv")
    event_bus.register(EventType.POSITION_OPENED, trade_logger.handle_event)
    event_bus.register(EventType.POSITION_CLOSED, trade_logger.handle_event)
    order_logger = OrderLogger(f"{settings.storage.logs_path}/orders.csv")
    event_bus.register(EventType.ORDER_PLACED, order_logger.handle_event)
    event_bus.register(EventType.ORDER_PARTIAL_FILL, order_logger.handle_event)
    event_bus.register(EventType.ORDER_FILLED, order_logger.handle_event)
    event_bus.register(EventType.ORDER_CANCELLED, order_logger.handle_event)
    thinking_logger = ThinkingLogger(f"{settings.storage.logs_path}/thinking.jsonl")

    await event_bus.publish(EventType.SYSTEM_STARTED, {"version": "0.1.0"})

    exchange_info: dict[str, Any] = {}
    symbol_filters_map: dict[str, Any] = {}
    try:
        exchange_info = await rest.get_exchange_info()
        for sym in exchange_info.get("symbols", []):
            symbol_filters_map[sym["symbol"]] = sym.get("filters", [])
    except Exception as exc:
        log.warning("exchange_info_failed", error=str(exc))

    last_processed_candles: dict[str, pd.Timestamp] = {}

    await _reconcile(state_manager, event_bus, rest)
    user_stream = UserDataStream(settings, rest, event_bus, state_manager)

    async def universe_loop() -> None:
        while True:
            try:
                universe = await universe_selector.select()
                symbols = [u.symbol for u in universe]
                await event_bus.publish(
                    EventType.UNIVERSE_UPDATED,
                    {"symbols": symbols},
                    {"source": "universe_selector"},
                )
                log.info("universe_selected", count=len(symbols), symbols=symbols)
            except Exception as exc:
                log.warning("universe_selection_failed", error=str(exc))
            await asyncio.sleep(60 * 60 * 24)

    async def news_loop() -> None:
        while True:
            if not settings.news.enabled:
                await asyncio.sleep(60)
                continue
            for item in news_ingester.fetch():
                await event_bus.publish(
                    EventType.NEWS_INGESTED,
                    {
                        "source": item.source,
                        "title": item.title,
                        "link": item.link,
                        "published": item.published.isoformat(),
                        "summary": item.summary,
                        "content": item.content,
                    },
                    {"source": "news_ingester"},
                )
                result = await llm.classify(item)
                await event_bus.publish(
                    EventType.NEWS_CLASSIFIED,
                    result.to_payload(),
                    {"source": "llm_classifier"},
                )
            await asyncio.sleep(settings.news.poll_interval_minutes * 60)

    async def strategy_loop() -> None:
        while True:
            signals = 0
            approvals = 0
            rejections = 0
            exits = 0
            state = state_manager.state
            if state.requires_manual_review or state.circuit_breaker_active:
                log.warning(
                    "strategy_paused",
                    manual_review=state.requires_manual_review,
                    circuit_breaker=state.circuit_breaker_active,
                )
                await asyncio.sleep(60)
                continue
            if not state.universe:
                log.info("strategy_waiting_for_universe")
                await asyncio.sleep(60)
                continue
            for symbol in state.universe:
                try:
                    daily_klines = await rest.get_klines(symbol, settings.strategy.trend_timeframe, 200)
                    entry_klines = await rest.get_klines(symbol, settings.strategy.entry_timeframe, 200)
                except Exception as exc:
                    log.warning("klines_fetch_failed", symbol=symbol, error=str(exc))
                    continue
                daily_df = _klines_to_df(daily_klines)
                fourh_df = _klines_to_df(entry_klines)
                if daily_df.empty or fourh_df.empty:
                    log.warning("klines_empty", symbol=symbol)
                    continue
                candle_close = fourh_df.index[-1]
                last_candle = last_processed_candles.get(symbol)
                if last_candle is not None and candle_close <= last_candle:
                    continue
                last_processed_candles[symbol] = candle_close
                try:
                    funding_rate = await rest.get_funding_rate(symbol)
                except Exception:
                    funding_rate = 0.0
                news_risk = state_manager.get_news_risk(symbol)
                news_blocked = state_manager.blocks_entries(symbol)
                if news_blocked:
                    news_risk = "HIGH"
                open_position = state.positions.get(symbol)
                signal = signal_generator.generate(
                    symbol=symbol,
                    daily_df=daily_df,
                    fourh_df=fourh_df,
                    funding_rate=funding_rate,
                    news_risk=news_risk,
                    open_position=open_position,
                    current_time=fourh_df.index[-1].to_pydatetime(),
                )
                thinking_logger.log_signal(
                    signal=signal,
                    news_risk=news_risk,
                    funding_rate=funding_rate,
                    entry_style=settings.strategy.entry.style,
                    score_threshold=settings.strategy.entry.score_threshold,
                    trend_timeframe=settings.strategy.trend_timeframe,
                    entry_timeframe=settings.strategy.entry_timeframe,
                    has_open_position=bool(open_position),
                    news_blocked=news_blocked,
                )
                if signal.signal_type != SignalType.NONE:
                    signals += 1
                    await event_bus.publish(
                        EventType.SIGNAL_COMPUTED,
                        {
                            "symbol": symbol,
                            "signal_type": signal.signal_type.value,
                            "score": signal.score.composite if signal.score else None,
                        },
                        {"source": "signal_engine"},
                    )
                if signal.signal_type in {SignalType.LONG, SignalType.SHORT}:
                    filters = parse_symbol_filters(symbol_filters_map.get(symbol, []))
                    proposal = TradeProposal(
                        symbol=symbol,
                        side="LONG" if signal.signal_type == SignalType.LONG else "SHORT",
                        entry_price=signal.entry_price or signal.price,
                        stop_price=signal.stop_price or signal.price,
                        take_profit=signal.take_profit,
                        atr=signal.atr,
                        leverage=settings.risk.default_leverage,
                        score=signal.score,
                        funding_rate=funding_rate,
                        news_risk=news_risk,
                        trade_id=signal.trade_id or "",
                        created_at=datetime.now(timezone.utc),
                    )
                    await event_bus.publish(
                        EventType.TRADE_PROPOSED,
                        {
                            "symbol": symbol,
                            "side": proposal.side,
                            "entry_price": proposal.entry_price,
                            "stop_price": proposal.stop_price,
                            "trade_id": proposal.trade_id,
                        },
                        {"source": "signal_engine"},
                    )
                    risk_result = risk_engine.evaluate(
                        state=state,
                        proposal=proposal,
                        symbol_filters=filters,
                        now=datetime.now(timezone.utc),
                    )
                    thinking_logger.log_risk(proposal, risk_result)
                    if risk_result.circuit_breaker:
                        await event_bus.publish(
                            EventType.CIRCUIT_BREAKER_TRIGGERED,
                            {"reason": "MAX_DRAWDOWN"},
                            {"source": "risk_engine"},
                        )
                        await _kill_switch(state_manager, execution_engine)
                        continue
                    if not risk_result.approved:
                        rejections += 1
                        await event_bus.publish(
                            EventType.RISK_REJECTED,
                            {"symbol": symbol, "reasons": risk_result.reasons},
                            {"source": "risk_engine"},
                        )
                        continue
                    approvals += 1
                    await event_bus.publish(
                        EventType.RISK_APPROVED,
                        {"symbol": symbol, "trade_id": proposal.trade_id},
                        {"source": "risk_engine"},
                    )
                    await execution_engine.execute_entry(proposal, risk_result, filters, state)
                elif signal.signal_type == SignalType.EXIT and open_position:
                    exits += 1
                    await execution_engine.execute_exit(open_position, signal.price, signal.reason or "EXIT")
            metrics.update_state(state_manager.state)
            log.info(
                "strategy_cycle_complete",
                universe=len(state.universe),
                signals=signals,
                approvals=approvals,
                rejections=rejections,
                exits=exits,
            )
            await asyncio.sleep(60 * 15)

    await asyncio.gather(universe_loop(), news_loop(), strategy_loop(), user_stream.run())


async def _reconcile(
    state_manager: StateManager, event_bus: EventBus, rest: BinanceRestClient
) -> None:
    try:
        account = await rest.get_account_info()
        positions = await rest.get_position_risk()
        orders = await rest.get_open_orders()
    except Exception as exc:
        log.warning("reconciliation_failed", error=str(exc))
        return

    discrepancies = state_manager.reconcile(account, positions, orders)
    for discrepancy in discrepancies:
        await event_bus.publish(
            EventType.MANUAL_INTERVENTION,
            discrepancy,
            {"source": "reconciliation"},
        )
    await event_bus.publish(
        EventType.RECONCILIATION_COMPLETED,
        {"discrepancies": len(discrepancies)},
        {"source": "reconciliation"},
    )


async def _kill_switch(state_manager: StateManager, execution_engine: ExecutionEngine) -> None:
    state = state_manager.state
    for order in list(state.open_orders.values()):
        await execution_engine.cancel_order(order.symbol, order.client_order_id)
    for position in list(state.positions.values()):
        await execution_engine.execute_exit(position, position.entry_price, "KILL_SWITCH")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
