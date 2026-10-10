"""Exchange observations and immutable historical research inputs.

The mutable ``candles`` table remains the operational cache. A revision keeps
the original observation when that cache changes; a dataset pins revisions so
later exchange corrections cannot silently alter a saved research input.
"""
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint, event
from sqlalchemy.orm import Mapped, mapped_column

from tradebot.db import Base, utcnow


class CandleRevision(Base):
    __tablename__ = "candle_revisions"
    __table_args__ = (UniqueConstraint("symbol", "timeframe_seconds", "timestamp", "content_hash", "source"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(80), index=True)
    timeframe_seconds: Mapped[int] = mapped_column(Integer)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    source: Mapped[str] = mapped_column(String(160))
    content_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class HistoricalDataset(Base):
    __tablename__ = "historical_datasets"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(80), index=True)
    timeframe_seconds: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(160))
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    candle_count: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64), unique=True)
    complete: Mapped[bool] = mapped_column(Boolean)
    missing_ranges: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    warnings: Mapped[list[str]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DatasetCandle(Base):
    __tablename__ = "dataset_candles"
    dataset_id: Mapped[str] = mapped_column(ForeignKey("historical_datasets.id"), primary_key=True)
    ordinal: Mapped[int] = mapped_column(Integer, primary_key=True)
    revision_id: Mapped[int] = mapped_column(ForeignKey("candle_revisions.id"))


class MarketDataGap(Base):
    __tablename__ = "market_data_gaps"
    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(80), index=True)
    timeframe_seconds: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(160))
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="OPEN")
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MarketFeedState(Base):
    __tablename__ = "market_feed_states"
    id: Mapped[str] = mapped_column(String(180), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(80), index=True)
    channel: Mapped[str] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(30), default="DISCONNECTED")
    source: Mapped[str] = mapped_column(String(160))
    sequence: Mapped[int] = mapped_column(Integer, default=0)
    gap_count: Mapped[int] = mapped_column(Integer, default=0)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    event_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    error: Mapped[str | None] = mapped_column(String(160), nullable=True)


class MarketStreamLease(Base):
    __tablename__ = "market_stream_leases"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    owner: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TelemetryState(Base):
    __tablename__ = "telemetry_state"
    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


def _immutable(mapper, connection, target):
    raise ValueError("Historical observations and dataset snapshots are immutable")


for _model in (CandleRevision, HistoricalDataset, DatasetCandle):
    event.listen(_model, "before_update", _immutable)
    event.listen(_model, "before_delete", _immutable)
