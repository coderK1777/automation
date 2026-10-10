"""One durable public-feed producer, independent of browser connections.

The exchange sends complete ``ob_l2`` snapshots, not deltas; a reconnect
invalidates the book until a new snapshot arrives. Intrabar WebSocket candles
are display observations only. Historical/paper candles always come from the
closed, validated REST importer, because the compact WebSocket sample does not
provide an authoritative candle-open timestamp.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import json
import logging
import random
import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from tradebot.data_models import MarketFeedState, MarketStreamLease
from tradebot.db import aware, get_db, session_factory, utcnow
from tradebot.market import RESOLUTIONS, import_candles
from tradebot.models import AuthSession, Strategy, StrategyVersion, User
from tradebot.repository import serialize
from tradebot.security import identity

logger = logging.getLogger("tradebot.market")
router = APIRouter(prefix="/api/v1", tags=["public market stream"])
SOURCE = "wss://public-socket.india.delta.exchange"
STALE_AFTER = 35
LEASE_ID = "delta-production-public"


def _number(value, *, positive=False, nonnegative=False):
    if value is None:
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as error:
        raise ValueError("Invalid public feed decimal") from error
    if not result.is_finite() or (positive and result <= 0) or (nonnegative and result < 0):
        raise ValueError("Invalid public feed numeric value")
    return format(result, "f")


def normalize_event(event: dict) -> tuple[str, str, datetime, dict]:
    channel, symbol, stamp = event.get("type"), event.get("sy"), event.get("ts")
    if not isinstance(symbol, str) or not isinstance(channel, str) or type(stamp) is not int:
        raise ValueError("Public feed lacks identity or exchange timestamp")
    event_time = datetime.fromtimestamp(stamp/1_000_000, timezone.utc)
    if event_time > utcnow()+timedelta(seconds=10):
        raise ValueError("Public feed exchange clock is in the future")
    symbol = symbol.removeprefix("MARK:")
    payload = {}
    if channel == "ticker":
        values = event.get("d")
        if not isinstance(values, list):
            raise ValueError("Public ticker lacks compact contract data")
        row = next((value for value in values if isinstance(value, dict) and value.get("s") == symbol), None)
        if row is None:
            raise ValueError("Public ticker has no matching contract")
        quotes, ohlc = row.get("q", []), row.get("ohlc", [])
        if not isinstance(quotes, list) or len(quotes) < 4 or not isinstance(ohlc, list) or len(ohlc) != 4:
            raise ValueError("Public ticker has invalid quote or OHLC data")
        payload = {"last_price": _number(ohlc[3], positive=True), "mark_price": _number(row.get("m"), positive=True),
                   "ask": _number(quotes[0], positive=True), "ask_size": _number(quotes[1], nonnegative=True),
                   "bid": _number(quotes[2], positive=True), "bid_size": _number(quotes[3], nonnegative=True),
                   "spot_price": _number(event.get("sp"), positive=True),
                   "open": _number(ohlc[0], positive=True), "high": _number(ohlc[1], positive=True),
                   "low": _number(ohlc[2], positive=True), "mark_change_24h": _number(row.get("m24hc"))}
    elif channel == "ob_l2":
        for field, key, reverse in (("asks", "a", False), ("bids", "b", True)):
            levels = event.get(key)
            if not isinstance(levels, list) or not 1 <= len(levels) <= 100:
                raise ValueError("Public book has invalid depth")
            parsed = []
            for level in levels:
                if not isinstance(level, list) or len(level) != 2:
                    raise ValueError("Public book has invalid level")
                parsed.append([_number(level[0], positive=True), _number(level[1], nonnegative=True)])
            prices = [Decimal(level[0]) for level in parsed]
            if prices != sorted(prices, reverse=reverse) or len(prices) != len(set(prices)):
                raise ValueError("Public book levels are unordered or duplicated")
            payload[field] = parsed[:15]
        payload.update(ask=payload["asks"][0][0], bid=payload["bids"][0][0], depth=min(len(payload["asks"]), len(payload["bids"])),
                       snapshot=True, sequence_semantics="complete-top-15-snapshot;exchange-timestamp-monotonic")
    elif channel == "mark_price":
        payload = {"mark_price": _number(event.get("p"), positive=True)}
    elif channel == "funding_rate":
        interval, next_time = event.get("fi"), event.get("nfr")
        if type(interval) is not int or interval <= 0 or type(next_time) is not int:
            raise ValueError("Public funding event lacks schedule")
        payload = {"funding_rate": _number(event.get("fr")), "funding_interval_seconds": interval,
                   "next_funding_at": datetime.fromtimestamp(next_time/1_000_000, timezone.utc).isoformat(),
                   "funding_rate_unit": "exchange-raw;not-certified-for-accounting"}
    elif channel.startswith("candlestick_"):
        resolution = channel.removeprefix("candlestick_")
        seconds = next((seconds for seconds, name in RESOLUTIONS.items() if name == resolution), None)
        if seconds is None or event.get("res") != resolution:
            raise ValueError("Public candle resolution mismatch")
        payload = {"open": _number(event.get("o"), positive=True), "high": _number(event.get("h"), positive=True),
                   "low": _number(event.get("l"), positive=True), "close": _number(event.get("c"), positive=True),
                   "volume": _number(event.get("v"), nonnegative=True), "timeframe_seconds": seconds,
                   "closed": False, "historical_source": "REST-closed-candles-only"}
        if any(payload[key] is None for key in ("open", "high", "low", "close", "volume")):
            raise ValueError("Public trade candle lacks OHLCV")
        if Decimal(payload["low"]) > min(Decimal(payload["open"]), Decimal(payload["close"])) or Decimal(payload["high"]) < max(Decimal(payload["open"]), Decimal(payload["close"])):
            raise ValueError("Public candle has invalid geometry")
    else:
        raise ValueError("Unsupported public market event")
    if payload.get("ask") and payload.get("bid") and Decimal(payload["ask"]) <= Decimal(payload["bid"]):
        raise ValueError("Public order book is crossed")
    if channel == "mark_price" and payload["mark_price"] is None:
        raise ValueError("Public mark event lacks price")
    return symbol, channel, event_time, payload


def _state(db: Session, symbol: str, channel: str) -> MarketFeedState:
    identifier = channel+":"+symbol
    row = db.get(MarketFeedState, identifier)
    if row is None:
        row = MarketFeedState(id=identifier, symbol=symbol, channel=channel, source=SOURCE, sequence=0, gap_count=0)
        db.add(row)
    return row


def store_event(db: Session, event: dict) -> bool:
    symbol, channel, stamp, payload = normalize_event(event)
    row = _state(db, symbol, channel)
    if row.event_time is not None and stamp <= aware(row.event_time):
        if stamp == aware(row.event_time) and payload == row.payload:
            return False
        # Reordered/conflicting observations never make the feed look healthy.
        row.status, row.error = "RECOVERING", "Exchange event timestamp reordered or conflicts with stored observation"
        row.gap_count += 1
        row.sequence += 1
        row.updated_at = utcnow()
        raise ValueError("Public event ordering failed; recover before consuming")
    row.payload, row.event_time, row.received_at = payload, stamp, utcnow()
    row.sequence += 1
    row.status, row.error, row.source, row.updated_at = "LIVE", None, SOURCE, utcnow()
    return True


def mark_gap(db: Session, channel: str, symbols: list[str], status="RECOVERING", error=None):
    for selected in symbols:
        row = _state(db, selected.removeprefix("MARK:"), channel)
        row.status, row.error, row.updated_at = status, error, utcnow()
        if row.received_at is not None or row.sequence > 0:
            row.gap_count += 1
        row.sequence += 1


def feed_rows(db: Session) -> list[dict]:
    now = utcnow()
    values = []
    for row in db.scalars(select(MarketFeedState).order_by(MarketFeedState.symbol, MarketFeedState.channel)):
        value = serialize(row)
        ages = [(now-aware(stamp)).total_seconds() for stamp in (row.event_time, row.received_at) if stamp is not None]
        age = max(0, *ages) if ages else None
        stale_after = 120 if row.channel == "funding_rate" else STALE_AFTER
        value.update(age_seconds=round(age, 3) if age is not None else None, stale_after_seconds=stale_after)
        if row.status in ("LIVE", "REST_SNAPSHOT") and (age is None or age > stale_after):
            value["status"] = "STALE"
        values.append(value)
    return values


def feed_health(db: Session, symbol: str, required_channels=("ticker",)) -> dict:
    selected = {row["channel"]: row for row in feed_rows(db) if row["symbol"] == symbol}
    reasons = [f"{channel}: {selected[channel]['status']}" if channel in selected else f"{channel}: unavailable"
               for channel in required_channels if channel not in selected or selected[channel]["status"] != "LIVE"]
    return {"healthy": not reasons, "reasons": reasons, "source": SOURCE}


def snapshot(db: Session, sequence: int | None = None):
    feeds = feed_rows(db)
    revision = sum(row["sequence"] for row in feeds)
    return {"sequence": sequence if sequence is not None else revision, "data_sequence": revision,
            "sequence_scope": "connection-snapshots" if sequence is not None else "durable-feed-revision-sum",
            "server_time": utcnow().isoformat(), "feeds": feeds, "stale_after_seconds": STALE_AFTER}


@router.get("/market/feeds")
def feeds(db: Session = Depends(get_db), principal=Depends(identity)):
    return feed_rows(db)


@router.get("/market/snapshot")
def market_snapshot(db: Session = Depends(get_db), principal=Depends(identity)):
    return snapshot(db)


@router.get("/market/stream")
async def market_stream(request: Request, db: Session = Depends(get_db), principal=Depends(identity)):
    factory = sessionmaker(db.get_bind(), expire_on_commit=False)
    user_id, session_id = principal[0].id, principal[1].id
    # Do not retain an authentication transaction for the lifetime of an SSE
    # connection. Every snapshot checks session validity in a fresh session.
    db.close()

    async def stream():
        sequence = 0
        while not await request.is_disconnected():
            with factory() as current:
                auth, user = current.get(AuthSession, session_id), current.get(User, user_id)
                if auth is None or user is None or not user.active or aware(auth.expires_at) <= utcnow():
                    break
                sequence += 1
                data = snapshot(current, sequence)
            yield f"id: {sequence}\nevent: snapshot\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"
            await asyncio.sleep(2)
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})


def claim_stream_lease(db: Session, owner: str) -> bool:
    row = db.scalar(select(MarketStreamLease).where(MarketStreamLease.id == LEASE_ID).with_for_update())
    now = utcnow()
    if row is None:
        try:
            with db.begin_nested():
                row = MarketStreamLease(id=LEASE_ID, owner=owner, expires_at=now+timedelta(seconds=45))
                db.add(row)
                db.flush()
        except IntegrityError:
            return False
    elif row.owner != owner and aware(row.expires_at) > now:
        return False
    row.owner, row.expires_at = owner, now+timedelta(seconds=45)
    return True


def subscriptions(db: Session) -> dict[str, tuple[str, ...]]:
    symbols = {"BTCUSD", "ETHUSD"}
    candles = {"candlestick_1m": set(symbols)}
    for config in db.scalars(select(StrategyVersion.config).join(Strategy,
                  (Strategy.id == StrategyVersion.strategy_id) & (Strategy.current_version == StrategyVersion.version))):
        symbol, timeframe = config.get("symbol"), config.get("timeframe_seconds")
        if isinstance(symbol, str) and timeframe in RESOLUTIONS:
            symbols.add(symbol)
            candles.setdefault("candlestick_"+RESOLUTIONS[timeframe], set()).add(symbol)
    if len(symbols) > 100:
        raise ValueError("Central public subscription limit exceeded; explicit sharding required")
    return {"ticker": tuple(sorted(symbols)), "ob_l2": tuple(sorted(symbols)),
            "mark_price": tuple("MARK:"+symbol for symbol in sorted(symbols)),
            "funding_rate": tuple(sorted(symbols)),
            **{channel: tuple(sorted(selected)) for channel, selected in candles.items()}}


async def _rest_recovery(client, factory, channel: str, symbols: list[str]):
    if channel == "ticker":
        for symbol in symbols:
            ticker = await client.ticker(symbol)
            quotes = ticker.get("quotes") if isinstance(ticker.get("quotes"), dict) else {}
            payload = {"last_price": _number(ticker.get("close"), positive=True),
                       "mark_price": _number(ticker.get("mark_price"), positive=True),
                       "bid": _number(quotes.get("best_bid"), positive=True),
                       "ask": _number(quotes.get("best_ask"), positive=True),
                       "funding_rate": _number(ticker.get("funding_rate")),
                       "funding_rate_unit": "exchange-raw;not-certified-for-accounting",
                       "recovery": "REST snapshot; stream continuity is not yet restored"}
            timestamp = ticker.get("timestamp")
            stamp = None
            if timestamp is not None:
                raw = float(timestamp)
                stamp = datetime.fromtimestamp(raw/(1_000_000 if raw > 1e14 else 1), timezone.utc)
            with factory() as db:
                row = _state(db, symbol, channel)
                row.payload, row.received_at, row.event_time = payload, utcnow(), stamp
                row.status, row.source, row.updated_at = "REST_SNAPSHOT", "https://api.india.delta.exchange/v2/tickers", utcnow()
                row.sequence += 1
                db.commit()
    elif channel.startswith("candlestick_"):
        timeframe = next(seconds for seconds, resolution in RESOLUTIONS.items() if channel == "candlestick_"+resolution)
        for symbol in symbols:
            with factory() as db:
                await import_candles(db, symbol, timeframe, 100)
                db.commit()
    # Books recover from the next complete ob_l2 snapshot. No REST book or
    # funding/mark historical series is invented when continuity is lost.


async def _consume(channel: str, symbols: tuple[str, ...], factory):
    from tradebot_delta import DeltaClient
    while True:
        try:
            with factory() as db:
                mark_gap(db, channel, list(symbols))
                db.commit()
            async with DeltaClient(environment="production") as client:
                await _rest_recovery(client, factory, channel, list(symbols))
                async for event in client.public_messages(channel, symbols):
                    if event["type"] == "stream_gap":
                        with factory() as db:
                            mark_gap(db, channel, list(symbols))
                            db.commit()
                        await _rest_recovery(client, factory, channel, list(symbols))
                        continue
                    with factory() as db:
                        try:
                            store_event(db, event)
                        except ValueError:
                            db.commit()
                            raise
                        db.commit()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            # Only a class name reaches the logs/state; transport exceptions
            # can contain URLs or credential-bearing connection strings.
            logger.warning("Public feed interrupted", extra={"event": "market_gap", "channel": channel,
                           "error_type": type(error).__name__})
            with factory() as db:
                mark_gap(db, channel, list(symbols), "DISCONNECTED", type(error).__name__)
                db.commit()
            await asyncio.sleep(5+random.uniform(0, 1))


async def run_market_streams(stop_event: asyncio.Event, factory=None):
    """Worker-owned background coroutine; never start it in browser/API routes.

    A database lease prevents a second worker from running a duplicate feed
    consumer. Lease loss cancels every socket before this process stands by.
    """
    factory = factory or session_factory()
    owner = uuid.uuid4().hex
    running: dict[str, tuple[tuple[str, ...], asyncio.Task]] = {}
    try:
        while not stop_event.is_set():
            with factory() as db:
                owns = claim_stream_lease(db, owner)
                desired = subscriptions(db) if owns else {}
                db.commit()
            for channel in list(running):
                selected, task = running[channel]
                if desired.get(channel) != selected or task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    with factory() as current:
                        mark_gap(current, channel, list(selected), "DISCONNECTED", "Subscription ownership changed")
                        current.commit()
                    del running[channel]
            for channel, selected in desired.items():
                if channel not in running:
                    running[channel] = (selected, asyncio.create_task(_consume(channel, selected, factory), name="market:"+channel))
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=10)
            except asyncio.TimeoutError:
                pass
    finally:
        for _, task in running.values():
            task.cancel()
        await asyncio.gather(*(task for _, task in running.values()), return_exceptions=True)
        with factory() as db:
            row = db.get(MarketStreamLease, LEASE_ID)
            if row is not None and row.owner == owner:
                row.expires_at = utcnow()
                for channel, (selected, _) in running.items():
                    mark_gap(db, channel, list(selected), "DISCONNECTED", "Producer stopped")
                db.commit()
