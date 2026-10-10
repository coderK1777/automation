from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from tradebot.db import utcnow
from tradebot.clock import record_clock_observation
from tradebot.models import Candle, Instrument
from tradebot.data_provenance import DELTA_HISTORY_SOURCE, FIELDS, LEGACY_SOURCE, record_revision, track_gaps

RESOLUTIONS = {60: "1m", 180: "3m", 300: "5m", 900: "15m", 1800: "30m", 3600: "1h", 7200: "2h", 14400: "4h", 21600: "6h", 86400: "1d"}


def _persist_clock_observations(db: Session, observations: list[dict]) -> None:
    if not observations:
        return
    # Independent evidence survives a caller's data-validation rollback.
    # No network await occurs while this short transaction owns row locks.
    # Authenticated callers can retain a connection during public IO. Use
    # a separate unpooled connection, so an exhausted caller pool cannot
    # block this event loop waiting for a sibling task to release its slot.
    bind = db.get_bind()
    source_engine = bind if hasattr(bind, "url") else bind.engine
    evidence_engine = create_engine(source_engine.url, poolclass=NullPool)
    try:
        with Session(bind=evidence_engine) as evidence_db:
            for observation in observations:
                record_clock_observation(evidence_db, observation)
            evidence_db.commit()
    finally:
        evidence_engine.dispose()


async def discover(db: Session) -> int:
    from tradebot_delta import DeltaClient
    observations = []
    try:
        async with DeltaClient(environment="production", clock_observer=observations.append) as client:
            products = await client.products(contract_types="perpetual_futures")
    finally:
        _persist_clock_observations(db, observations)
    # A synchronous observer must not acquire database locks while another
    # task can run during HTTP requests/client cleanup. Persist only after
    # the final await so a sibling coroutine cannot block the event loop on
    # a row held by this transaction.
    for value in products:
        row = db.get(Instrument, value["id"])
        if row is None:
            row = Instrument(id=value["id"], symbol=value["symbol"], metadata_json=value)
            db.add(row)
        row.metadata_json, row.verified, row.updated_at = value, bool(value.get("metadata_verified", False)), utcnow()
    db.flush()
    return len(products)


async def import_candles(db: Session, symbol: str, timeframe: int, bars: int) -> int:
    from tradebot_core import Candle as CoreCandle
    from tradebot_delta import DeltaClient
    if timeframe not in RESOLUTIONS:
        raise ValueError("Unsupported Delta candle resolution")
    now = utcnow()
    start = int((now - timedelta(seconds=bars * timeframe)).timestamp())
    observations = []
    try:
        async with DeltaClient(environment="production", clock_observer=observations.append) as client:
            values = await client.candles(symbol, RESOLUTIONS[timeframe], start, int(now.timestamp()))
    finally:
        _persist_clock_observations(db, observations)
    added = 0
    for value in values:
        ts = datetime.fromtimestamp(value["timestamp"], timezone.utc)
        if ts + timedelta(seconds=timeframe) > now:
            continue
        normalized = CoreCandle(timestamp=ts, timeframe_seconds=timeframe,
                                **{key: Decimal(str(value[key])) for key in ("open", "high", "low", "close", "volume")})
        row = db.scalar(select(Candle).where(Candle.symbol == symbol, Candle.timeframe_seconds == timeframe, Candle.timestamp == ts).with_for_update())
        if row is None:
            row = Candle(symbol=symbol, timeframe_seconds=timeframe, timestamp=ts,
                          **{key: getattr(normalized, key) for key in FIELDS})
            db.add(row)
            added += 1
        elif any(getattr(row, key) != getattr(normalized, key) for key in FIELDS):
            # Preserve the previous observation before accepting a corrected
            # authoritative exchange candle into the operational cache.
            from tradebot.data_provenance import candle_payload, content_hash
            from tradebot.data_models import CandleRevision
            previous = db.scalar(select(CandleRevision).where(CandleRevision.symbol == symbol,
                        CandleRevision.timeframe_seconds == timeframe, CandleRevision.timestamp == ts,
                        CandleRevision.content_hash == content_hash(candle_payload(row))))
            if previous is None:
                record_revision(db, row, LEGACY_SOURCE)
            for key in FIELDS:
                setattr(row, key, getattr(normalized, key))
            row.received_at = now
        record_revision(db, row, DELTA_HISTORY_SOURCE)
    db.flush()
    first_requested = datetime.fromtimestamp(((start+timeframe-1)//timeframe)*timeframe, timezone.utc)
    last_closed = datetime.fromtimestamp((int(now.timestamp())//timeframe)*timeframe-timeframe, timezone.utc)
    track_gaps(db, symbol, timeframe, first_requested, last_closed)
    return added
