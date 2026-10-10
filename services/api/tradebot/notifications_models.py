"""Telegram configuration metadata and durable, retryable delivery outbox."""
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from tradebot.db import Base, utcnow


class TelegramSettings(Base):
    __tablename__ = "telegram_settings"
    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    allowed_chat_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    allowed_user_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    last_notification_id: Mapped[int] = mapped_column(default=0)
    last_update_id: Mapped[int] = mapped_column(default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TelegramOutbox(Base):
    __tablename__ = "telegram_outbox"
    __table_args__ = (UniqueConstraint("source_key", "chat_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    source_key: Mapped[str] = mapped_column(String(100))
    chat_id: Mapped[str] = mapped_column(String(40))
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    attempts: Mapped[int] = mapped_column(default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
