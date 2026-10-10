"""Observed per-version PAPER risk baseline; historical peaks are never inferred."""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, Text
from sqlalchemy.orm import Mapped, mapped_column

from tradebot.db import Base, utcnow


class StrategyRiskState(Base):
    __tablename__ = "paper_strategy_risk_state"
    strategy_id: Mapped[int] = mapped_column(ForeignKey("strategies.id"), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    baseline_equity: Mapped[Decimal] = mapped_column(Numeric(30, 12))
    baseline_pnl: Mapped[Decimal] = mapped_column(Numeric(30, 12))
    current_equity: Mapped[Decimal] = mapped_column(Numeric(30, 12))
    peak_equity: Mapped[Decimal] = mapped_column(Numeric(30, 12))
    ledger_valid: Mapped[bool] = mapped_column(Boolean, default=True)
    note: Mapped[str] = mapped_column(Text)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
