"""Main runtime loop for the trading bot."""

from __future__ import annotations

import asyncio
import atexit
from datetime import datetime, timezone
from pathlib import Path
import time
import sys
from typing import Any

import pandas as pd
import structlog
import uvicorn

from src.api.operator import create_app
from src.config.settings import load_settings
from src.connectors import (
    BinanceRestClient,
    LLMClassifier,
    LLMNewsClassifierAdapter,
    NewsIngester,
    RuleBasedNewsClassifier,
)
from src.execution import ExecutionEngine
from src.execution.user_stream import UserDataStream
from src.ledger import EventBus, EventLedger, EventType, StateManager
from src.models import TradeProposal
from src.monitoring import (
    AlertWebhookHandler,
    EventConsoleLogger,
    Metrics,
    OrderLogger,
    PerformanceTelemetry,
    ThinkingLogger,
    TradeLogger,
    configure_logging,
)
from src.risk.engine import RiskEngine
from src.strategy import SignalGenerator, SignalType, UniverseSelector
from src.strategy.portfolio import PortfolioSelector, TradeCandidate
from src.utils import parse_symbol_filters
from src.utils.single_instance import SingleInstanceAlreadyRunning, SingleInstanceLock

log = structlog.get_logger(__name__)


def _klines_to_df(klines: list[list[Any]]) -> pd.DataFrame:
    rows = []
    if not isinstance(klines, list):
        log.warning("klines_invalid_type", type=type(klines).__name__)
        return pd.DataFrame()
    for kline in klines:
        try:
            if len(kline) < 6:
                raise IndexError("kline missing required fields")
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
        except (TypeError, ValueError, IndexError) as exc:
            log.warning("kline_parse_failed", error=str(exc))
            continue
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
    configure_logging(settings.monitoring.log_level, settings.storage.logs_path, settings.monitoring)
    if sys.version_info < (3, 10) or sys.version_info >= (3, 13):
        log.warning(
            "python_version_unverified",
            version=f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        )
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

    # Initialize alert webhooks if configured
    if settings.monitoring.alert_webhooks:
        alert_handler = AlertWebhookHandler(
            webhook_urls=settings.monitoring.alert_webhooks,
            run_mode=settings.run.mode,
        )
        event_bus.register(EventType.MANUAL_INTERVENTION, alert_handler.handle_event)
        event_bus.register(EventType.CIRCUIT_BREAKER_TRIGGERED, alert_handler.handle_event)

    rest = BinanceRestClient(settings)
    metrics = Metrics()
    rest.set_metrics(metrics)

    # Sync server time before making any signed requests
    try:
        await rest.sync_server_time()
        metrics.binance_time_sync_offset_ms.set(rest.get_time_offset())
    except Exception as exc:
        log.warning("initial_time_sync_failed", error=str(exc))

    news_ingester = NewsIngester(settings.news)

    llm_key = settings.openai_api_key or settings.anthropic_api_key
    llm = LLMClassifier(settings.llm, llm_key)

    # News classifier is deterministic by default; LLM is optional behind a feature flag.
    if settings.news.classifier_provider == "llm":
        news_classifier = LLMNewsClassifierAdapter(llm)
        news_classifier_source = "llm_classifier"
    else:
        news_classifier = RuleBasedNewsClassifier()
        news_classifier_source = "rule_classifier"
    risk_engine = RiskEngine(settings.risk)
    signal_generator = SignalGenerator(settings.strategy, settings.regime)
    universe_selector = UniverseSelector(rest, settings.universe, event_bus=event_bus)
    execution_engine = ExecutionEngine(
        settings, rest, event_bus, state_path=settings.storage.state_path
    )
    event_bus.register(EventType.ORDER_FILLED, execution_engine.handle_order_filled)
    event_bus.register(EventType.ORDER_CANCELLED, execution_engine.handle_order_cancelled)
    portfolio_selector = PortfolioSelector(max_positions=settings.risk.max_positions)

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

    try:
        metrics.start(settings.monitoring.metrics_port)
    except Exception as exc:
        log.warning("metrics_start_failed", error=str(exc))
    execution_engine.set_metrics(metrics)
    trade_logger = TradeLogger(f"{settings.storage.logs_path}/trades.csv")
    event_bus.register(EventType.POSITION_OPENED, trade_logger.handle_event)
    event_bus.register(EventType.POSITION_CLOSED, trade_logger.handle_event)
    order_logger = OrderLogger(f"{settings.storage.logs_path}/orders.csv")
    event_bus.register(EventType.ORDER_PLACED, order_logger.handle_event)
    event_bus.register(EventType.ORDER_PARTIAL_FILL, order_logger.handle_event)
    event_bus.register(EventType.ORDER_FILLED, order_logger.handle_event)
    event_bus.register(EventType.ORDER_CANCELLED, order_logger.handle_event)
    thinking_logger = ThinkingLogger(f"{settings.storage.logs_path}/thinking.jsonl")

    # Initialize performance telemetry
    telemetry = PerformanceTelemetry(
        metrics=metrics,
        rest_client=rest if not execution_engine.simulate else None,
        logs_path=settings.storage.logs_path,
        trades_csv_path=f"{settings.storage.logs_path}/trades.csv",
        simulate=execution_engine.simulate,
    )
    event_bus.register(EventType.ORDER_PLACED, telemetry.handle_order_placed)
    event_bus.register(EventType.ORDER_FILLED, telemetry.handle_order_filled)
    event_bus.register(EventType.POSITION_OPENED, telemetry.handle_position_opened)
    event_bus.register(EventType.POSITION_CLOSED, telemetry.handle_position_closed)

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

    # Track consecutive reconciliation failures for alerting
    reconciliation_failure_count = 0

    def _reconciliation_enabled() -> tuple[bool, str | None]:
        if not settings.reconciliation.enabled:
            return False, "disabled"
        if settings.run.mode == "paper":
            if not settings.reconciliation.allow_in_paper:
                return False, "run_mode_paper"
            if not (settings.active_binance_api_key and settings.active_binance_secret_key):
                return False, "missing_api_keys"
        return True, None

    reconciliation_ok, reconciliation_reason = _reconciliation_enabled()
    if reconciliation_ok:
        await _reconcile(state_manager, event_bus, rest, metrics)
    else:
        log.info("reconciliation_skipped", reason=reconciliation_reason)

    # Recovery: Validate persisted pending entries against open orders from Binance
    if execution_engine._state_store:
        await _recover_pending_entries(execution_engine, rest, event_bus)

    user_stream = UserDataStream(settings, rest, event_bus, state_manager, metrics=metrics)

    async def universe_loop() -> None:
        last_tick = time.time()
        while True:
            now = time.time()
            metrics.loop_last_tick_age_sec.labels(loop="universe").set(now - last_tick)
            last_tick = now
            delay_sec = settings.universe.refresh_interval_hours * 60 * 60
            try:
                result = await universe_selector.select_with_filtering()
                symbols = [u.symbol for u in result.selected]
                filtered_symbols = [f.symbol for f in result.filtered]
                await event_bus.publish(
                    EventType.UNIVERSE_UPDATED,
                    {
                        "symbols": symbols,
                        "filtered_count": len(filtered_symbols),
                        "filtered_symbols": filtered_symbols,
                    },
                    {"source": "universe_selector"},
                )
                log.info(
                    "universe_selected",
                    count=len(symbols),
                    symbols=symbols,
                    filtered_count=len(filtered_symbols),
                    filtered_symbols=filtered_symbols,
                )
            except Exception as exc:
                log.warning("universe_selection_failed", error=str(exc))
                delay_sec = settings.universe.retry_interval_minutes * 60
            await asyncio.sleep(delay_sec)

    async def news_loop() -> None:
        last_tick = time.time()
        while True:
            now = time.time()
            metrics.loop_last_tick_age_sec.labels(loop="news").set(now - last_tick)
            last_tick = now
            try:
                if not settings.news.enabled:
                    await asyncio.sleep(60)
                    continue
                for item in await news_ingester.fetch_async():
                    try:
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
                        result = await news_classifier.classify(item)
                        await event_bus.publish(
                            EventType.NEWS_CLASSIFIED,
                            result.to_payload(),
                            {"source": news_classifier_source},
                        )
                    except Exception as exc:
                        log.warning("news_item_failed", source=item.source, error=str(exc))
                await asyncio.sleep(settings.news.poll_interval_minutes * 60)
            except Exception as exc:
                log.warning("news_loop_error", error=str(exc))
                await asyncio.sleep(60)

    async def reconciliation_loop() -> None:
        """Continuous reconciliation loop that runs every N minutes."""
        nonlocal reconciliation_failure_count
        last_tick = time.time()
        while True:
            now = time.time()
            metrics.loop_last_tick_age_sec.labels(loop="reconciliation").set(now - last_tick)
            last_tick = now
            reconciliation_ok, reconciliation_reason = _reconciliation_enabled()
            if not reconciliation_ok:
                if reconciliation_reason:
                    log.info("reconciliation_skipped", reason=reconciliation_reason)
                await asyncio.sleep(60)
                continue
            try:
                success = await _reconcile(state_manager, event_bus, rest, metrics)
                if success:
                    reconciliation_failure_count = 0
                    metrics.reconciliation_success_total.inc()
                else:
                    reconciliation_failure_count += 1
                    metrics.reconciliation_consecutive_failures.set(reconciliation_failure_count)
            except Exception as exc:
                reconciliation_failure_count += 1
                metrics.reconciliation_failure_total.inc()
                metrics.reconciliation_consecutive_failures.set(reconciliation_failure_count)
                log.warning("reconciliation_loop_error", error=str(exc))

            # Trigger manual review alert on consecutive failures
            if reconciliation_failure_count >= settings.reconciliation.failure_threshold:
                await event_bus.publish(
                    EventType.MANUAL_INTERVENTION,
                    {
                        "action": "RECONCILIATION_FAILURE_THRESHOLD",
                        "consecutive_failures": reconciliation_failure_count,
                        "threshold": settings.reconciliation.failure_threshold,
                        "reason": "Repeated reconciliation failures require manual review",
                    },
                    {"source": "reconciliation_loop"},
                )
                log.warning(
                    "reconciliation_failure_threshold_reached",
                    consecutive_failures=reconciliation_failure_count,
                    threshold=settings.reconciliation.failure_threshold,
                )
                # Reset counter after alerting to avoid spamming
                reconciliation_failure_count = 0

            await asyncio.sleep(settings.reconciliation.interval_minutes * 60)

    async def strategy_loop() -> None:
        last_tick = time.time()
        while True:
            now = time.time()
            metrics.loop_last_tick_age_sec.labels(loop="strategy").set(now - last_tick)
            last_tick = now
            try:
                signals = 0
                rejections = 0
                exits = 0
                candidates: list[TradeCandidate] = []
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

                # Phase 1: Collect all candidates (don't execute yet)
                for symbol in state.universe:
                    try:
                        daily_klines = await rest.get_klines(
                            symbol, settings.strategy.trend_timeframe, 200
                        )
                        entry_klines = await rest.get_klines(
                            symbol, settings.strategy.entry_timeframe, 200
                        )
                        daily_df = _klines_to_df(daily_klines)
                        fourh_df = _klines_to_df(entry_klines)
                        if daily_df.empty or fourh_df.empty:
                            log.warning("klines_empty", symbol=symbol)
                            continue
                        candle_close = fourh_df.index[-1]
                        last_candle = last_processed_candles.get(symbol)
                        # Check if we have a pending order for this candle (lifecycle tracking)
                        has_pending = execution_engine.has_pending_for_candle(
                            symbol, candle_close.to_pydatetime()
                        )
                        if (
                            last_candle is not None
                            and candle_close <= last_candle
                            and not has_pending
                        ):
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

                        # Handle exits immediately (they reduce position count)
                        if signal.signal_type == SignalType.EXIT and open_position:
                            exits += 1
                            await execution_engine.execute_exit(
                                open_position, signal.price, signal.reason or "EXIT"
                            )

                        # Handle trailing stops for open positions
                        if open_position:
                            current_price = float(fourh_df["close"].iloc[-1])
                            atr = signal.atr if signal else None
                            if atr and atr > 0:
                                filters = parse_symbol_filters(symbol_filters_map.get(symbol, []))
                                tick_size = filters.tick_size if filters else 0.0001
                                await execution_engine.update_trailing_stop(
                                    open_position, current_price, atr, tick_size
                                )

                        # Collect entry candidates for cross-sectional selection
                        if signal.signal_type in {SignalType.LONG, SignalType.SHORT}:
                            filters = parse_symbol_filters(symbol_filters_map.get(symbol, []))
                            proposal = TradeProposal(
                                symbol=symbol,
                                side="LONG"
                                if signal.signal_type == SignalType.LONG
                                else "SHORT",
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
                                candle_timestamp=fourh_df.index[-1].to_pydatetime(),
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

                            # Add to candidates for cross-sectional selection
                            candidates.append(
                                TradeCandidate(
                                    symbol=symbol,
                                    proposal=proposal,
                                    risk_result=risk_result,
                                    score=signal.score,
                                    funding_rate=funding_rate,
                                    news_risk=news_risk,
                                    candle_timestamp=candle_close,
                                )
                            )
                    except Exception as exc:
                        log.warning("strategy_symbol_failed", symbol=symbol, error=str(exc))
                        continue

                # Phase 2: Cross-sectional selection
                blocked_symbols = {s for s in state.universe if state_manager.blocks_entries(s)}
                selected = portfolio_selector.select(
                    candidates=candidates,
                    current_positions=state.positions,
                    blocked_symbols=blocked_symbols,
                    state=state,
                )

                # Phase 3: Execute selected trades
                executed_count = 0
                skipped_count = 0
                for candidate in selected:
                    filters = parse_symbol_filters(symbol_filters_map.get(candidate.symbol, []))
                    result = await execution_engine.execute_entry(
                        candidate.proposal,
                        candidate.risk_result,
                        filters,
                        state,
                    )
                    if result.placed:
                        executed_count += 1
                        await event_bus.publish(
                            EventType.RISK_APPROVED,
                            {"symbol": candidate.symbol, "trade_id": candidate.proposal.trade_id},
                            {"source": "portfolio_selector"},
                        )
                    else:
                        skipped_count += 1

                # Phase 4: Emit cycle summary event
                ineligible_reasons: dict[str, str] = {}
                for c in candidates:
                    if not c.selected and c.rank is not None:
                        # Determine why not selected
                        if c.symbol in blocked_symbols:
                            ineligible_reasons[c.symbol] = f"news_blocked ({c.news_risk})"
                        elif c.symbol in state.positions:
                            ineligible_reasons[c.symbol] = "already_have_position"
                        else:
                            # Check if we hit max positions
                            current_entry_count = sum(
                                1
                                for pos in state.positions.values()
                                if pos.symbol not in blocked_symbols
                            )
                            if current_entry_count >= settings.risk.max_positions:
                                ineligible_reasons[c.symbol] = (
                                    f"max_positions_reached "
                                    f"({current_entry_count}/{settings.risk.max_positions})"
                                )
                            else:
                                ineligible_reasons[c.symbol] = (
                                    f"rank_too_low ({c.rank} > {settings.risk.max_positions})"
                                )

                summary = portfolio_selector.get_selection_summary(
                    candidates, selected, ineligible_reasons
                )
                await event_bus.publish(
                    EventType.TRADE_CYCLE_COMPLETED,
                    summary,
                    {"source": "portfolio_selector"},
                )

                metrics.update_state(state_manager.state)
                log.info(
                    "strategy_cycle_complete",
                    universe=len(state.universe),
                    signals=signals,
                    candidates=len(candidates),
                    selected=executed_count,
                    skipped=skipped_count,
                    rejections=rejections,
                    exits=exits,
                )
                await asyncio.sleep(60 * 15)
            except Exception as exc:
                log.warning("strategy_loop_error", error=str(exc))
                await asyncio.sleep(60)

    async def watchdog_loop() -> None:
        """Periodic watchdog to verify protective orders are on-exchange."""
        last_tick = time.time()
        while True:
            now = time.time()
            metrics.loop_last_tick_age_sec.labels(loop="watchdog").set(now - last_tick)
            last_tick = now
            try:
                await execution_engine.verify_protective_orders(state_manager.state)
            except Exception as exc:
                log.warning("watchdog_cycle_failed", error=str(exc))
            await asyncio.sleep(settings.watchdog.interval_sec)

    async def api_server() -> None:
        """Run the operator API server."""
        try:
            config = uvicorn.Config(
                create_app(
                    event_bus=event_bus,
                    state_manager=state_manager,
                    ledger=ledger,
                    settings=settings,
                ),
                host="127.0.0.1",
                port=settings.monitoring.api_port,
                log_level="info",
            )
            server = uvicorn.Server(config)
            await server.serve()
        except Exception as exc:
            log.warning("api_server_failed", error=str(exc))

    async def telemetry_loop() -> None:
        """Periodic telemetry updates and daily summary generation."""
        last_daily_summary_date: str | None = None
        last_tick = time.time()
        while True:
            now = time.time()
            metrics.loop_last_tick_age_sec.labels(loop="telemetry").set(now - last_tick)
            last_tick = now
            try:
                # Update metrics every 5 minutes
                ws_age = user_stream.last_message_age_sec()
                if ws_age is not None:
                    metrics.ws_last_message_age_sec.set(ws_age)
                await telemetry.update_metrics()

                # Check if we need to generate daily summary (at UTC 00:00)
                now = datetime.now(timezone.utc)
                today_str = now.strftime("%Y-%m-%d")
                # Trigger at start of new day (between 00:00 and 00:05 UTC)
                if now.hour == 0 and now.minute < 5 and last_daily_summary_date != today_str:
                    await telemetry.run_daily_summary()
                    last_daily_summary_date = today_str
            except Exception as exc:
                log.warning("telemetry_loop_error", error=str(exc))
            await asyncio.sleep(60 * 5)  # 5 minutes

    await asyncio.gather(
        universe_loop(),
        news_loop(),
        strategy_loop(),
        reconciliation_loop(),
        user_stream.run(),
        watchdog_loop(),
        api_server(),
        telemetry_loop(),
        return_exceptions=True,
    )


async def _reconcile(
    state_manager: StateManager,
    event_bus: EventBus,
    rest: BinanceRestClient,
    metrics: Metrics | None = None,
) -> bool:
    """
    Reconcile exchange state with internal state.

    Returns True if reconciliation succeeded, False on API errors.
    """
    try:
        account = await rest.get_account_info()
        positions = await rest.get_position_risk()
        orders = await rest.get_open_orders()
    except Exception as exc:
        log.warning("reconciliation_failed", error=str(exc))
        return False

    discrepancies = state_manager.reconcile(account, positions, orders)

    # Update discrepancy metrics
    if metrics is not None:
        for discrepancy in discrepancies:
            disc_type = discrepancy.get("action", "UNKNOWN")
            metrics.reconciliation_discrepancy_total.labels(type=disc_type).inc()

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
    return True


async def _recover_pending_entries(
    execution_engine: ExecutionEngine, rest: BinanceRestClient, event_bus: EventBus
) -> None:
    """
    Recover pending entries from state store and validate against open orders from Binance.

    On restart, if an entry order was placed before the bot crashed, the pending entry
    context is persisted. We need to:
    1. Load persisted pending entries from state store
    2. Fetch open orders from Binance
    3. Cross-reference: only keep pending entries whose orders are still open
    4. If order is no longer open, remove from state store (it was either filled
       - which will be handled by reconciliation - or cancelled)
    """
    try:
        open_orders = await rest.get_open_orders()
    except Exception as exc:
        log.warning("pending_entries_recovery_failed", error=str(exc))
        return

    # Build set of open client order IDs from exchange
    open_client_ids: set[str] = set()
    for order in open_orders:
        client_id = order.get("clientOrderId") or str(order.get("orderId"))
        if client_id:
            open_client_ids.add(client_id)

    # Get persisted entries
    persisted = execution_engine._state_store.load_all()
    recovered_count = 0
    stale_count = 0

    for client_order_id in list(persisted.keys()):
        if client_order_id not in open_client_ids:
            # Order no longer open on exchange - remove from state store
            # It was either filled (recovered by reconciliation) or cancelled
            execution_engine._state_store.remove(client_order_id)
            stale_count += 1
            # Remove from memory if present
            execution_engine._pending_entries.pop(client_order_id, None)
        else:
            recovered_count += 1

    if recovered_count > 0:
        log.info(
            "pending_entries_recovered",
            recovered=recovered_count,
            stale=stale_count,
        )
    elif stale_count > 0:
        log.info(
            "pending_entries_stale_cleaned",
            stale=stale_count,
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
