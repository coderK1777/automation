"""Versioned immutable report snapshots, with account and mode segregation."""
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from tradebot.db import Base, utcnow


class PerformanceReport(Base):
    __tablename__ = "performance_reports"
    __table_args__ = (UniqueConstraint("account_id", "mode", "period", "period_start", "period_end"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("paper_accounts.id"))
    mode: Mapped[str] = mapped_column(String(16), default="PAPER")
    period: Mapped[str] = mapped_column(String(16))
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
