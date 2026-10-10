"""Single lease-owning long-lived public-data and PAPER strategy worker."""
import asyncio
import logging
import signal
import uuid
from datetime import timedelta

from sqlalchemy import select

from tradebot import market
from tradebot.config import get_settings
from tradebot.db import aware, session_factory, utcnow
from tradebot.models import PaperAccount, Position, Strategy, WorkerHeartbeat
from tradebot.paper import claim_lease, enter_signal, lock_account, protect_positions, reconcile
from tradebot.repository import candle_rows, core_candles, notify, position_timeframe, version

logger = logging.getLogger("tradebot.worker")


def heartbeat(owner: str, status: str, error: str | None = None):
    with session_factory()() as db:
        row = db.get(WorkerHeartbeat, owner)
        if row is None:
            row = WorkerHeartbeat(id=owner, status=status)
            db.add(row)
        row.status, row.last_error, row.updated_at = status, error, utcnow()
        db.commit()


def process_paper(db, *, executor_healthy=False):
    from tradebot_core import StrategyConfig, evaluate_strategy
    account = lock_account(db)
    from tradebot.telemetry import record_reconciliation
    issues = reconcile(db, account)
    record_reconciliation("PAPER", not issues, db=db)
    if issues:
        return
    strategies = list(db.scalars(select(Strategy)))
    history = {}
    for strategy in strategies:
        saved = version(db, strategy)
        rows = candle_rows(db, saved.config["symbol"], saved.config["timeframe_seconds"], 5000)
        if rows:
            history[strategy.id] = rows
    for position in db.scalars(select(Position).where(Position.status == "OPEN")):
        history[position.strategy_id] = candle_rows(db, position.symbol, position_timeframe(db, position), 5000)
    # Stops remain active for PAUSED/STOPPED strategies and during entry block.
    protect_positions(db, history)
    from tradebot.strategy_risk import strategy_risk_reasons
    from tradebot.repository import audit
    suspension_reasons = {"STRATEGY_CONSECUTIVE_LOSS_LIMIT", "STRATEGY_DRAWDOWN_LIMIT", "STRATEGY_LEDGER_INVALID",
                          "STRATEGY_RISK_BASELINE_MISSING", "STRATEGY_RISK_BASELINE_INVALID"}
    for strategy in strategies:
        if strategy.status != "RUNNING" or strategy.approved_version != strategy.current_version:
            continue
        try:
            config = StrategyConfig.model_validate(version(db, strategy).config)
            reasons = strategy_risk_reasons(db, strategy.id, config, account)
            breaches = [reason for reason in reasons if reason in suspension_reasons]
            if breaches:
                strategy.status, strategy.last_error = "PAUSED", ", ".join(breaches)
                audit(db, "paper-worker", "STRATEGY_RISK_SUSPEND", str(strategy.id), {"version": config.version, "reasons": breaches})
                notify(db, f"PAPER strategy {strategy.name} suspended by its risk policy; protection retained", "WARNING")
        except (ValueError, ArithmeticError):
            strategy.status, strategy.last_error = "ERROR", "Strategy policy cannot be evaluated safely"
    from tradebot.paper_expansion import process_pending
    process_pending(db, history, executor_healthy=executor_healthy)
    for strategy in strategies:
        if strategy.status != "RUNNING" or strategy.approved_version != strategy.current_version:
            continue
        saved = version(db, strategy)
        if not saved.validated:
            strategy.status, strategy.last_error = "ERROR", "Approved version is not validated"
            continue
        try:
            config = StrategyConfig.model_validate(saved.config)
            rows = candle_rows(db, config.symbol, config.timeframe_seconds, 1500)
            higher = {dependency.timeframe_seconds: core_candles(candle_rows(db, config.symbol, dependency.timeframe_seconds, 1500))
                      for dependency in config.higher_timeframes}
            context = None
            if rows and (config.filters.event_filter_enabled or config.filters.maximum_absolute_funding_rate is not None):
                from tradebot.research_api import evaluation_contexts
                latest = core_candles(rows[-1:])[0]
                context = evaluation_contexts(db, config, [latest])[latest.timestamp+timedelta(seconds=latest.timeframe_seconds)]
            generated = evaluate_strategy(config, core_candles(rows), higher_timeframes=higher, context=context)
            if generated and rows:
                enter_signal(db, strategy.id, config, generated, rows[-1], executor_healthy=executor_healthy)
            strategy.last_error = None
        except (ValueError, ArithmeticError) as error:
            strategy.last_error, strategy.status = str(error), "ERROR"
            notify(db, f"Strategy {strategy.name} blocked by invalid data/configuration", "ERROR")


async def cycle(owner: str, refresh_metadata: bool = False):
    # Public IO happens outside executor transactions. No database lease lock
    # is held across network calls or strategy polling delays.
    with session_factory()() as db:
        account = db.get(PaperAccount, 1)
        if account and not claim_lease(db, owner):
            db.commit()
            heartbeat(owner, "STANDBY")
            return
        db.commit()
        strategies = list(db.scalars(select(Strategy)))
        requests = {(version(db, row).config["symbol"], version(db, row).config["timeframe_seconds"]): 1500 for row in strategies}
        for strategy in strategies:
            config = version(db, strategy).config
            for dependency in config.get("higher_timeframes", []):
                requests[(config["symbol"], dependency["timeframe_seconds"])] = 1500
        for position in db.scalars(select(Position).where(Position.status == "OPEN")):
            strategy = db.get(Strategy, position.strategy_id)
            config = dict(version(db, strategy).config, timeframe_seconds=position_timeframe(db, position))
            cursor = aware(position.last_protection_candle_at or position.opened_at)
            needed = int((utcnow()-cursor).total_seconds()/config["timeframe_seconds"])+3
            key = (position.symbol, config["timeframe_seconds"])
            requests[key] = min(5000, max(requests.get(key, 1500), needed))
        # A browser-independent public feed exists even before strategy setup.
        requests.setdefault(("BTCUSD", 60), 1500)
        requests.setdefault(("ETHUSD", 60), 1500)
        errors = []
        if refresh_metadata:
            try:
                await market.discover(db)
                db.commit()
            except Exception:
                db.rollback()
                errors.append("Delta India public product discovery unavailable")
        for (symbol, timeframe), bars in requests.items():
            try:
                await market.import_candles(db, symbol, timeframe, bars)
                db.commit()
            except Exception:
                db.rollback()
                errors.append(f"Delta India public candles unavailable: {symbol}/{timeframe}")
        if account:
            # Recheck lease after potentially slow network IO; never execute
            # based on ownership observed before its expiry.
            if claim_lease(db, owner):
                process_paper(db, executor_healthy=not errors)
                db.commit()
            else:
                db.rollback()
                heartbeat(owner, "STANDBY")
                return
    heartbeat(owner, "DEGRADED" if errors else "RUNNING", "; ".join(errors) if errors else None)


async def run():
    owner = uuid.uuid4().hex
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stopped.set)
    heartbeat(owner, "STARTING")
    from tradebot.market_stream import run_market_streams
    from tradebot.telemetry import record_process_start
    record_process_start("paper-worker")
    stream_task = asyncio.create_task(run_market_streams(stopped), name="central-market-streams")
    step = 0
    while not stopped.is_set():
        try:
            await cycle(owner, refresh_metadata=step % 120 == 0)
        except Exception:
            logger.exception("Paper worker cycle failed")
            try:
                heartbeat(owner, "ERROR", "Worker cycle failed; review server logs")
            except Exception:
                logger.error("Heartbeat database unavailable")
        step += 1
        try:
            await asyncio.wait_for(stopped.wait(), timeout=get_settings().worker_poll_seconds)
        except TimeoutError:
            pass
    stopped.set()
    try:
        await asyncio.wait_for(stream_task, timeout=10)
    except (TimeoutError, Exception):
        stream_task.cancel()
        await asyncio.gather(stream_task, return_exceptions=True)
    # Shorten only our own lease; a replacement process may already own it.
    try:
        from tradebot.models import ExecutorLease
        with session_factory()() as db:
            if db.get(PaperAccount, 1):
                lock_account(db)
                lease = db.get(ExecutorLease, 1)
                if lease and lease.owner == owner:
                    lease.expires_at = utcnow() - timedelta(seconds=1)
                db.commit()
        heartbeat(owner, "STOPPED")
    except Exception:
        logger.error("Graceful worker shutdown persistence failed")


if __name__ == "__main__":
    from tradebot.telemetry import configure_logging
    configure_logging("paper-worker")
    asyncio.run(run())
