"""Durable simulator and research records; these never contain exchange credentials."""
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from tradebot.db import Base, utcnow

Money = Numeric(30, 12)


class PaperMovement(Base):
    __tablename__ = "paper_movements"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("paper_accounts.id"))
    position_id: Mapped[str | None] = mapped_column(ForeignKey("positions.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(24))
    amount: Mapped[Decimal] = mapped_column(Money)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PaperFundingEvent(Base):
    __tablename__ = "paper_funding_events"
    __table_args__ = (UniqueConstraint("symbol", "timestamp"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    rate: Mapped[Decimal] = mapped_column(Money)
    mark_price: Mapped[Decimal] = mapped_column(Money)
    source: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PaperPositionState(Base):
    __tablename__ = "paper_position_state"
    position_id: Mapped[str] = mapped_column(ForeignKey("positions.id"), primary_key=True)
    strategy_version: Mapped[int] = mapped_column(default=1)
    initial_stop: Mapped[Decimal] = mapped_column(Money)
    entry_quantity: Mapped[Decimal] = mapped_column(Money)
    exited_quantity: Mapped[Decimal] = mapped_column(Money, default=0)
    favorable_price: Mapped[Decimal] = mapped_column(Money)
    funding_paid: Mapped[Decimal] = mapped_column(Money, default=0)
    policy: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    completed_targets: Mapped[list[int]] = mapped_column(JSON, default=list)
    entry_observations: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class PaperPendingEntry(Base):
    __tablename__ = "paper_pending_entries"
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), primary_key=True)
    signal_id: Mapped[str] = mapped_column(ForeignKey("signals.id"), unique=True)
    strategy_id: Mapped[int] = mapped_column(ForeignKey("strategies.id"))
    config: Mapped[dict[str, Any]] = mapped_column(JSON)
    instrument: Mapped[dict[str, Any]] = mapped_column(JSON)
    stop_price: Mapped[Decimal] = mapped_column(Money)
    target_price: Mapped[Decimal] = mapped_column(Money)
    reserved_margin: Mapped[Decimal] = mapped_column(Money)
    reserved_risk: Mapped[Decimal] = mapped_column(Money)
    filled_quantity: Mapped[Decimal] = mapped_column(Money, default=0)
    last_candle_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ResearchRun(Base):
    __tablename__ = "research_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    strategy_id: Mapped[int] = mapped_column(ForeignKey("strategies.id"))
    version: Mapped[int] = mapped_column()
    kind: Mapped[str] = mapped_column(String(32))
    dataset_hash: Mapped[str] = mapped_column(String(64))
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON)
    result: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PaperRiskDecision(Base):
    __tablename__ = "paper_risk_decisions"
    id: Mapped[int] = mapped_column(primary_key=True)
    signal_id: Mapped[str] = mapped_column(ForeignKey("signals.id"), index=True)
    strategy_id: Mapped[int] = mapped_column(ForeignKey("strategies.id"))
    version: Mapped[int] = mapped_column()
    stage: Mapped[str] = mapped_column(String(16), default="ENTRY")
    approved: Mapped[bool] = mapped_column(Boolean)
    decision: Mapped[dict[str, Any]] = mapped_column(JSON)
    context: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
