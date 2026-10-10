"""Deterministic versions, explicit missing intervals, and revision retention."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tradebot.data_models import CandleRevision, DatasetCandle, HistoricalDataset, MarketDataGap
from tradebot.db import aware, utcnow
from tradebot.models import Candle

DELTA_HISTORY_SOURCE = "https://api.india.delta.exchange/v2/history/candles"
LEGACY_SOURCE = "legacy-cache:original-source-unrecorded"
FIELDS = ("open", "high", "low", "close", "volume")


def canonical_decimal(value) -> str:
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError("Non-finite historical price or volume")
    # Decimal.normalize() uses the ambient precision and can round a valid
    # Numeric(30, 12) observation before hashing it. Format then trim instead.
    if number == 0:
        return "0"
    text = format(number, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def candle_payload(row) -> dict:
    return {"timestamp": aware(row.timestamp).isoformat(), "timeframe_seconds": row.timeframe_seconds,
            **{field: canonical_decimal(getattr(row, field)) for field in FIELDS}}


def content_hash(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def record_revision(db: Session, row: Candle, source: str) -> CandleRevision:
    payload = candle_payload(row)
    digest = content_hash(payload)
    query = select(CandleRevision).where(CandleRevision.symbol == row.symbol,
                                        CandleRevision.timeframe_seconds == row.timeframe_seconds,
                                        CandleRevision.timestamp == row.timestamp,
                                        CandleRevision.content_hash == digest, CandleRevision.source == source)
    revision = db.scalar(query)
    if revision is not None:
        return revision
    revision = CandleRevision(symbol=row.symbol, timeframe_seconds=row.timeframe_seconds, timestamp=row.timestamp,
                              source=source, content_hash=digest, payload=payload)
    try:
        with db.begin_nested():
            db.add(revision)
            db.flush()
    except IntegrityError:
        # Another public importer can observe the identical bar concurrently.
        revision = db.scalar(query)
        if revision is None:
            raise
    return revision


def missing_ranges(timestamps: list[datetime], timeframe: int, start: datetime, end: datetime) -> list[dict]:
    if timeframe <= 0 or aware(end) < aware(start):
        return []
    step = timedelta(seconds=timeframe)
    available = {aware(value) for value in timestamps}
    cursor, finish = aware(start), aware(end)
    gaps = []
    while cursor <= finish:
        if cursor in available:
            cursor += step
            continue
        first = cursor
        while cursor <= finish and cursor not in available:
            cursor += step
        gaps.append({"start_at": first.isoformat(), "end_at": (cursor-step).isoformat(),
                     "missing_bars": int((cursor-first).total_seconds()/timeframe)})
    return gaps


def track_gaps(db: Session, symbol: str, timeframe: int, start: datetime, end: datetime) -> list[dict]:
    timestamps = list(db.scalars(select(Candle.timestamp).where(Candle.symbol == symbol,
                           Candle.timeframe_seconds == timeframe, Candle.timestamp >= start, Candle.timestamp <= end)))
    gaps = missing_ranges(timestamps, timeframe, start, end)
    open_rows = list(db.scalars(select(MarketDataGap).where(MarketDataGap.symbol == symbol,
                     MarketDataGap.timeframe_seconds == timeframe, MarketDataGap.status == "OPEN").with_for_update()))
    for row in open_rows:
        # Resolve only after actual cached observations cover the entire gap.
        old_start, old_end = aware(row.start_at), aware(row.end_at)
        old_timestamps = list(db.scalars(select(Candle.timestamp).where(Candle.symbol == symbol,
                            Candle.timeframe_seconds == timeframe, Candle.timestamp >= old_start, Candle.timestamp <= old_end)))
        if not missing_ranges(old_timestamps, timeframe, old_start, old_end):
            row.status, row.resolved_at = "RESOLVED", utcnow()
    step = timedelta(seconds=timeframe)
    for gap in gaps:
        first, last = datetime.fromisoformat(gap["start_at"]), datetime.fromisoformat(gap["end_at"])
        overlaps = [row for row in open_rows if row.status == "OPEN"
                    and aware(row.start_at) <= last+step and aware(row.end_at) >= first-step]
        if overlaps:
            row = overlaps[0]
            row.start_at = min(first, *(aware(item.start_at) for item in overlaps))
            row.end_at = max(last, *(aware(item.end_at) for item in overlaps))
            for duplicate in overlaps[1:]:
                duplicate.status, duplicate.resolved_at = "MERGED", utcnow()
        else:
            row = MarketDataGap(symbol=symbol, timeframe_seconds=timeframe, source=DELTA_HISTORY_SOURCE,
                                start_at=first, end_at=last)
            db.add(row)
            open_rows.append(row)
    db.flush()
    return gaps


def create_dataset(db: Session, symbol: str, timeframe: int, limit: int = 5000) -> HistoricalDataset:
    if not 1 <= limit <= 100000:
        raise ValueError("Dataset limit must be between 1 and 100000")
    now = utcnow()
    last_closed = datetime.fromtimestamp(int(now.timestamp())//timeframe*timeframe-timeframe, timezone.utc)
    rows = list(db.scalars(select(Candle).where(Candle.symbol == symbol, Candle.timeframe_seconds == timeframe,
                       Candle.timestamp <= last_closed).order_by(Candle.timestamp.desc()).limit(limit).with_for_update()))
    rows.reverse()
    if not rows:
        raise ValueError("No observed closed historical candles are available")
    revisions = []
    for row in rows:
        digest = content_hash(candle_payload(row))
        revision = db.scalar(select(CandleRevision).where(CandleRevision.symbol == symbol,
                    CandleRevision.timeframe_seconds == timeframe, CandleRevision.timestamp == row.timestamp,
                    CandleRevision.content_hash == digest).order_by(CandleRevision.id.desc()).limit(1))
        revisions.append(revision or record_revision(db, row, LEGACY_SOURCE))
    gaps = missing_ranges([row.timestamp for row in rows], timeframe, rows[0].timestamp, rows[-1].timestamp)
    sources = sorted({revision.source for revision in revisions})
    identity = {"schema": 1, "symbol": symbol, "timeframe_seconds": timeframe,
                "observations": [{"source": revision.source, "hash": revision.content_hash} for revision in revisions]}
    digest = content_hash(identity)
    existing = db.get(HistoricalDataset, digest)
    if existing:
        return existing
    warnings = ["Historical funding, mark-price and order-book series are not part of this OHLCV dataset."]
    if LEGACY_SOURCE in sources:
        warnings.append("Some candles predate provenance recording; their original source is unknown.")
    if gaps:
        warnings.append("Observed history contains missing bars; no replacement candles were fabricated.")
    dataset = HistoricalDataset(id=digest, content_hash=digest, symbol=symbol, timeframe_seconds=timeframe,
                  source=sources[0] if len(sources) == 1 else "mixed-observations", start_at=rows[0].timestamp,
                  end_at=rows[-1].timestamp, candle_count=len(rows), complete=not gaps, missing_ranges=gaps, warnings=warnings)
    try:
        with db.begin_nested():
            db.add(dataset)
            db.flush()
            for ordinal, revision in enumerate(revisions):
                db.add(DatasetCandle(dataset_id=digest, ordinal=ordinal, revision_id=revision.id))
            db.flush()
    except IntegrityError:
        dataset = db.get(HistoricalDataset, digest)
        if dataset is None:
            raise
    return dataset


def dataset_candles(db: Session, identifier: str) -> list[dict]:
    return [dict(revision.payload, symbol=revision.symbol, source=revision.source,
                 revision_id=revision.id, content_hash=revision.content_hash)
            for revision in db.scalars(select(CandleRevision).join(DatasetCandle,
                    DatasetCandle.revision_id == CandleRevision.id).where(DatasetCandle.dataset_id == identifier)
                    .order_by(DatasetCandle.ordinal))]
