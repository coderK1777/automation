"""Operator-authorized sources, attributed events, and deterministic blackouts."""
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from tradebot.db import Base, utcnow


class NewsSource(Base):
    __tablename__ = "news_sources"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    authorized_by: Mapped[str] = mapped_column(String(80))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    required_for_trading: Mapped[bool] = mapped_column(Boolean, default=False)
    stale_after_seconds: Mapped[int] = mapped_column(default=3600)
    poll_interval_seconds: Mapped[int] = mapped_column(default=300)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class NewsEvent(Base):
    __tablename__ = "news_events"
    __table_args__ = (UniqueConstraint("source_id", "external_id", "revision", name="uq_news_event_revision"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("news_sources.id"))
    external_id: Mapped[str] = mapped_column(String(200))
    revision: Mapped[int] = mapped_column(default=1)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(300))
    category: Mapped[str] = mapped_column(String(30))
    impact: Mapped[str] = mapped_column(String(10))
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    symbols: Mapped[list[str]] = mapped_column(JSON, default=list)
    source_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    details: Mapped[str] = mapped_column(Text, default="")
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BlackoutRule(Base):
    __tablename__ = "news_blackout_rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    categories: Mapped[list[str]] = mapped_column(JSON)
    minimum_impact: Mapped[str] = mapped_column(String(10), default="HIGH")
    before_minutes: Mapped[int] = mapped_column(default=30)
    after_minutes: Mapped[int] = mapped_column(default=30)
    symbols: Mapped[list[str]] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class NewsSourceObservation(Base):
    __tablename__ = "news_source_observations"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("news_sources.id"), index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    status: Mapped[str] = mapped_column(String(16))
    event_count: Mapped[int] = mapped_column(default=0)
