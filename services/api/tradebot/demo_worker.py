"""Explicitly started, exclusive DEMO executor and authenticated stream recovery.

Run ``python -m tradebot.demo_worker``. An unconnected account performs no private
requests; writes need both local authorization and MFA-confirmed account enable.
"""
import asyncio
import logging
import signal
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select

from tradebot import demo
from tradebot.demo_capabilities import require_limits, require_strategy
from tradebot.db import aware, session_factory, utcnow
from tradebot.demo_models import DemoAccount, DemoCandle, DemoOrder, DemoPosition, DemoProduct, DemoProtection, DemoStrategy
from tradebot.market import RESOLUTIONS
from tradebot.models import Approval, Strategy, StrategyVersion
from tradebot_core import Candle, StrategyConfig, evaluate_strategy, size_position
from tradebot_core.indicators import atr
from tradebot_core.execution import round_increment
from tradebot_core.risk import RiskLimits

logger = logging.getLogger("tradebot.demo_worker")


def set_stream(owner: str, status: str, error: str | None = None):
    with session_factory()() as db:
        account = db.scalar(select(DemoAccount).where(DemoAccount.id == 1).with_for_update())
        if account and account.owner == owner:
            account.stream_status = status
            account.reconciliation_required = True
            account.last_error = error
            db.commit()


def mark_private_event(owner: str) -> bool:
    with session_factory()() as db:
        account = db.scalar(select(DemoAccount).where(DemoAccount.id == 1).with_for_update())
        if not account or account.owner != owner:
            return False
        account.reconciliation_required = True
        db.commit()
        return True


async def private_stream(owner: str, stopped: asyncio.Event):
    failures = 0
    while not stopped.is_set():
        try:
            with session_factory()() as db:
                account = db.get(DemoAccount, 1)
                if not account or account.owner != owner or not account.lease_expires_at or aware(account.lease_expires_at) <= utcnow():
                    return
            async with demo.client() as adapter:
                # "all" is an official category, ensuring manual external
                # account changes cannot disappear outside the strategy list.
                async for message in adapter.private_messages(("orders", "positions", "v2/user_trades", "margins"), ("all",), emit_auth=True):
                    if stopped.is_set():
                        return
                    kind = message.get("type")
                    if kind == "private_authenticated":
                        failures = 0
                        await asyncio.to_thread(set_stream, owner, "CONNECTED")
                    elif kind in ("orders", "positions", "v2/user_trades", "margins"):
                        # REST recovery persists exact fill identities and fees;
                        # a WS event never invents or overwrites ledger totals.
                        if not await asyncio.to_thread(mark_private_event, owner):
                            return
                    elif kind == "stream_gap":
                        await asyncio.to_thread(set_stream, owner, "DISCONNECTED", "Private sequence gap; REST reconciliation required")
        except asyncio.CancelledError:
            raise
        except Exception:
            failures += 1
            await asyncio.to_thread(set_stream, owner, "DISCONNECTED", "Private demo stream unavailable; account recovery required")
            try:
                await asyncio.wait_for(stopped.wait(), timeout=min(30, 2**min(failures, 5)))
            except asyncio.TimeoutError:
                pass


async def import_candles(db, adapter, symbol: str, timeframe: int):
    if timeframe not in RESOLUTIONS:
        raise demo.DemoRejected("Unsupported demo candle timeframe")
    now = utcnow()
    rows = await adapter.candles(symbol, RESOLUTIONS[timeframe], int((now-timedelta(seconds=timeframe*1500)).timestamp()), int(now.timestamp()))
    for value in rows:
        timestamp = datetime.fromtimestamp(value["timestamp"], timezone.utc)
        if timestamp+timedelta(seconds=timeframe) > now:
            continue
        candle = Candle(timestamp=timestamp, timeframe_seconds=timeframe, **{key: Decimal(value[key]) for key in ("open", "high", "low", "close", "volume")})
        existing = db.scalar(select(DemoCandle).where(DemoCandle.symbol == symbol, DemoCandle.timeframe_seconds == timeframe, DemoCandle.timestamp == timestamp))
        if not existing:
            db.add(DemoCandle(symbol=symbol, timeframe_seconds=timeframe, timestamp=timestamp,
                              **{key: getattr(candle, key) for key in ("open", "high", "low", "close", "volume")}))
        elif any(getattr(existing, key) != getattr(candle, key) for key in ("open", "high", "low", "close", "volume")):
            raise demo.DemoRejected("Demo closed candle revision requires operator review; signals remain blocked")


async def strategy_history(db, adapter, config: StrategyConfig):
    histories = {}
    for seconds in (config.timeframe_seconds, *(item.timeframe_seconds for item in config.higher_timeframes)):
        await import_candles(db, adapter, config.symbol, seconds)
        rows = list(db.scalars(select(DemoCandle).where(DemoCandle.symbol == config.symbol, DemoCandle.timeframe_seconds == seconds)
                               .order_by(DemoCandle.timestamp.desc()).limit(1500)))[::-1]
        histories[seconds] = [Candle(timestamp=aware(row.timestamp), timeframe_seconds=row.timeframe_seconds,
                                    open=row.open, high=row.high, low=row.low, close=row.close, volume=row.volume) for row in rows]
    return histories.pop(config.timeframe_seconds), histories


async def process_opposite_exits(db, account: DemoAccount, adapter):
    """Manage owned exposure even when its entry runner is paused/disabled.

    Exchange brackets remain installed until an authoritative flat snapshot.
    A new opposite closed-bar signal queues the ordinary owned reduce-only
    close, including cancellation of any remaining entry quantity first.
    """
    if account.reconciliation_required or account.stream_status != "CONNECTED" or not demo.execution_authorized():
        return
    for position in db.scalars(select(DemoPosition).where(DemoPosition.signed_quantity != 0, DemoPosition.managed.is_(True))):
        entry = db.scalar(select(DemoOrder).where(DemoOrder.product_id == position.product_id, DemoOrder.reduce_only.is_(False),
                          DemoOrder.filled_quantity > 0).order_by(DemoOrder.created_at.desc()).limit(1))
        if not entry or not entry.strategy_id:
            continue
        saved = db.scalar(select(StrategyVersion).where(StrategyVersion.strategy_id == entry.strategy_id, StrategyVersion.version == entry.strategy_version))
        if not saved:
            continue
        runner = db.scalar(select(DemoStrategy).where(DemoStrategy.strategy_id == entry.strategy_id))
        try:
            config = StrategyConfig.model_validate(saved.config)
            require_strategy(config)
            if not config.exit_rules.opposite_signal_exit:
                continue
            candles, higher = await strategy_history(db, adapter, config)
            generated = evaluate_strategy(config, candles, higher_timeframes=higher)
            if generated and generated.side.sign*position.signed_quantity < 0:
                if (utcnow()-generated.timestamp).total_seconds() > RiskLimits.model_validate(account.risk_limits).market_max_age_seconds:
                    raise demo.DemoRejected("Demo exit signal is stale; existing exchange protection remains installed")
                close = demo.queue_close(db, position.product_id, "demo-worker:opposite-signal")
                demo.event(db, close, "STRATEGY_EXIT_SIGNAL", {"strategy_id": entry.strategy_id, "strategy_version": entry.strategy_version,
                           "signal_id": generated.signal_id, "data_snapshot_id": generated.data_snapshot_id,
                           "timestamp": generated.timestamp.isoformat(), "reason": "OPPOSITE_CLOSED_BAR_SIGNAL"})
        except demo.DemoRejected as error:
            if runner:
                runner.last_error = str(error)
        except (ValueError, ArithmeticError):
            if runner:
                runner.last_error = "Demo exit evaluation lacks valid causal closed-bar data; existing exchange protection remains installed"
    db.commit()


async def process_strategies(db, account: DemoAccount, adapter):
    if not account.entries_enabled or account.emergency_stop or account.reconciliation_required or account.stream_status != "CONNECTED":
        return
    for runner in db.scalars(select(DemoStrategy).where(DemoStrategy.status == "RUNNING")):
        strategy = db.get(Strategy, runner.strategy_id)
        if not strategy or strategy.current_version != runner.version:
            runner.status, runner.last_error = "PAUSED", "Strategy version changed; exact-version demo approval required"
            continue
        saved = db.scalar(select(StrategyVersion).where(StrategyVersion.strategy_id == runner.strategy_id, StrategyVersion.version == runner.version))
        try:
            approved = db.scalar(select(Approval).where(Approval.strategy_id == runner.strategy_id, Approval.version == runner.version, Approval.stage == "DEMO"))
            if not saved or not saved.validated or not approved:
                raise demo.DemoRejected("Demo strategy version lacks validation")
            config = StrategyConfig.model_validate(saved.config)
            require_strategy(config)
            limits = RiskLimits.model_validate(account.risk_limits)
            require_limits(limits)
            occupied = db.scalar(select(DemoPosition).where(DemoPosition.symbol == config.symbol, DemoPosition.signed_quantity != 0))
            if occupied:
                continue
            candles, higher = await strategy_history(db, adapter, config)
            generated = evaluate_strategy(config, candles, higher_timeframes=higher)
            if generated:
                if (utcnow()-generated.timestamp).total_seconds() > RiskLimits.model_validate(account.risk_limits).market_max_age_seconds:
                    raise demo.DemoRejected("Demo strategy signal is stale")
                key = f"strategy:{runner.strategy_id}:v{runner.version}:{generated.signal_id}"
                if db.scalar(select(DemoOrder).where(DemoOrder.idempotency_key == key)):
                    continue
                product = db.scalar(select(DemoProduct).where(DemoProduct.symbol == config.symbol))
                instrument = demo.core_contract(product)
                volatility_atr = atr(candles, config.sizing.volatility_period)[-1] if candles and config.sizing.mode == "VOLATILITY" else None
                sized = size_position(instrument=instrument, side=generated.side, entry_price=generated.reference_price,
                                      stop_price=generated.stop_price, equity=account.equity, risk_fraction=min(config.risk_fraction, limits.max_risk_fraction),
                                      max_leverage=min(config.max_leverage, limits.max_leverage), fee_rate=demo.FEE_RATE,
                                      slippage_bps=demo.SLIPPAGE_BPS, spread_bps=Decimal(0), available_margin=account.available_margin,
                                      sizing=config.sizing, volatility_atr=volatility_atr)
                if sized.quantity <= 0:
                    raise demo.DemoRejected("Risk budget cannot afford the certified demo minimum")
                order = demo.queue_order(db, {"idempotency_key": key, "symbol": config.symbol, "side": generated.side.value,
                                      "quantity": int(sized.quantity), "order_type": "MARKET", "stop_price": sized.stop_price,
                                      "target_price": round_increment(generated.target_price, instrument.tick_size, up=generated.side.value == "SELL"), "strategy_id": runner.strategy_id,
                                      "strategy_version": runner.version}, "demo-worker")
                demo.event(db, order, "STRATEGY_SIZING", {"signal_id": generated.signal_id, "data_snapshot_id": generated.data_snapshot_id,
                           "signal_timestamp": generated.timestamp.isoformat(), "sizing": sized.model_dump(mode="json"),
                           "volatility_atr": str(volatility_atr) if volatility_atr is not None else None})
            runner.last_error = None
        except demo.DemoRejected as error:
            runner.status, runner.last_error = "PAUSED", str(error)
        except (ValueError, ArithmeticError):
            runner.last_error = "Demo strategy blocked by data, ownership or sizing; inspect account reconciliation and contract evidence"
    db.commit()


async def cycle(owner: str):
    if not demo.credential_configured():
        return False
    with session_factory()() as db:
        account = db.get(DemoAccount, 1)
        if not account or not account.connected:
            return False
        if not demo.claim_lease(db, owner):
            db.commit()
            return False
        db.commit()
        async with demo.client() as adapter:
            await demo.discover(db, adapter)
            account = await demo.reconcile(db, adapter)
            db.commit()
            if not demo.claim_lease(db, owner):
                db.rollback()
                return False
            await process_opposite_exits(db, account, adapter)
            await process_strategies(db, account, adapter)
        if demo.execution_authorized():
            async with demo.client(writes=True) as adapter:
                # Missing protection blocks new entries and requests a managed
                # reduce-only close. No unrelated exchange position is touched.
                for position in db.scalars(select(DemoPosition).where(DemoPosition.signed_quantity != 0, DemoPosition.managed.is_(True), DemoPosition.protection_verified.is_(False))):
                    demo.block_entries(db, account, "demo-worker:protection-failure")
                    demo.queue_close(db, position.product_id, "demo-worker:protection-failure")
                db.commit()
                for protection in db.scalars(select(DemoProtection).where(DemoProtection.status == "CANCEL_PENDING")):
                    if not demo.claim_lease(db, owner):
                        db.rollback()
                        return False
                    position = db.get(DemoPosition, protection.product_id)
                    if position and position.signed_quantity != 0:
                        continue
                    try:
                        await adapter.cancel_order(product_id=protection.product_id, order_id=protection.exchange_order_id)
                        protection.status = "CANCELED"
                    except Exception:
                        protection.status = "UNKNOWN"
                        account.reconciliation_required = True
                    db.commit()
                pending = list(db.scalars(select(DemoOrder).where(DemoOrder.status.in_(("RISK_PENDING", "RISK_APPROVED", "CANCEL_PENDING"))).order_by(DemoOrder.reduce_only.desc(), DemoOrder.created_at)))
                pending.sort(key=lambda order: (order.status != "CANCEL_PENDING", not order.reduce_only, order.created_at))
                for order in pending:
                    if not demo.claim_lease(db, owner):
                        db.rollback()
                        return False
                    if order.status == "CANCEL_PENDING":
                        await demo.execute_cancel(db, account, order, adapter)
                    else:
                        try:
                            await demo.execute_intent(db, account, order, adapter)
                        except (ValueError, ArithmeticError) as error:
                            order.status, order.last_error = "REJECTED", str(error)
                            demo.event(db, order, "REJECTED_BEFORE_SUBMISSION", {"reason": str(error)})
                            db.commit()
                    account = await demo.reconcile(db, adapter)
                    db.commit()
        account.worker_status, account.worker_updated_at, account.last_error = "RUNNING", utcnow(), None
        from tradebot.demo_reporting import schedule_reports
        schedule_reports(db)
        db.commit()
    return True


async def run():
    owner = uuid.uuid4().hex
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stopped.set)
    stream = None
    try:
        while not stopped.is_set():
            try:
                active = await cycle(owner)
                if active and (stream is None or stream.done()):
                    stream = asyncio.create_task(private_stream(owner, stopped))
                elif not active and stream:
                    stream.cancel()
                    await asyncio.gather(stream, return_exceptions=True)
                    stream = None
            except Exception:
                logger.error("DEMO cycle failed; entries require account recovery")
                with session_factory()() as db:
                    account = db.get(DemoAccount, 1)
                    if account and account.owner == owner:
                        account.worker_status, account.worker_updated_at = "ERROR", utcnow()
                        account.reconciliation_required, account.last_error = True, "Demo cycle failed; review credential configuration and exchange recovery"
                        db.commit()
            try:
                await asyncio.wait_for(stopped.wait(), timeout=10)
            except asyncio.TimeoutError:
                pass
    finally:
        if stream:
            stream.cancel()
            await asyncio.gather(stream, return_exceptions=True)
        with session_factory()() as db:
            account = db.get(DemoAccount, 1)
            if account and account.owner == owner:
                account.worker_status, account.stream_status = "STOPPED", "DISCONNECTED"
                account.lease_expires_at, account.worker_updated_at = utcnow(), utcnow()
                account.reconciliation_required = True
                db.commit()


if __name__ == "__main__":
    asyncio.run(run())
