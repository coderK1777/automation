"""Durable Delta India DEMO ledger, deliberately separate from PAPER tables."""
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from tradebot.db import Base, utcnow
from tradebot.models import Money


class DemoAccount(Base):
    __tablename__ = "demo_accounts"
    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    credential_fingerprint: Mapped[str] = mapped_column(String(64))
    connected: Mapped[bool] = mapped_column(Boolean, default=False)
    entries_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    emergency_stop: Mapped[bool] = mapped_column(Boolean, default=False)
    reconciliation_required: Mapped[bool] = mapped_column(Boolean, default=True)
    settlement_asset: Mapped[str] = mapped_column(String(16), default="USD")
    equity: Mapped[Decimal] = mapped_column(Money, default=0)
    available_margin: Mapped[Decimal] = mapped_column(Money, default=0)
    initial_equity: Mapped[Decimal] = mapped_column(Money, default=0)
    peak_equity: Mapped[Decimal] = mapped_column(Money, default=0)
    issues: Mapped[list[str]] = mapped_column(JSON, default=list)
    risk_limits: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stream_status: Mapped[str] = mapped_column(String(20), default="DISCONNECTED")
    worker_status: Mapped[str] = mapped_column(String(20), default="STOPPED")
    worker_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DemoProduct(Base):
    __tablename__ = "demo_products"
    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(64), unique=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    verified_contract: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DemoOrder(Base):
    __tablename__ = "demo_orders"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True)
    client_order_id: Mapped[str] = mapped_column(String(32), unique=True)
    strategy_id: Mapped[int | None] = mapped_column(ForeignKey("strategies.id"), nullable=True)
    strategy_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("demo_products.id"))
    exchange_order_id: Mapped[int | None] = mapped_column(Integer, unique=True, nullable=True)
    symbol: Mapped[str] = mapped_column(String(64))
    side: Mapped[str] = mapped_column(String(8))
    order_type: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(30), default="RISK_PENDING")
    quantity: Mapped[Decimal] = mapped_column(Money)
    filled_quantity: Mapped[Decimal] = mapped_column(Money, default=0)
    limit_price: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    stop_price: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    target_price: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    reduce_only: Mapped[bool] = mapped_column(Boolean, default=False)
    reserved_risk: Mapped[Decimal] = mapped_column(Money, default=0)
    reserved_margin: Mapped[Decimal] = mapped_column(Money, default=0)
    risk_decision: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    instrument_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    average_fill_price: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DemoOrderEvent(Base):
    __tablename__ = "demo_order_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("demo_orders.id"))
    event_key: Mapped[str] = mapped_column(String(128), unique=True)
    event_type: Mapped[str] = mapped_column(String(40))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DemoFill(Base):
    __tablename__ = "demo_fills"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    order_id: Mapped[str | None] = mapped_column(ForeignKey("demo_orders.id"), nullable=True)
    protection_order_id: Mapped[int | None] = mapped_column(ForeignKey("demo_protection_orders.exchange_order_id"), nullable=True)
    exchange_order_id: Mapped[str] = mapped_column(String(80))
    product_id: Mapped[int] = mapped_column(Integer)
    side: Mapped[str] = mapped_column(String(8))
    quantity: Mapped[Decimal] = mapped_column(Money)
    price: Mapped[Decimal] = mapped_column(Money)
    fee: Mapped[Decimal] = mapped_column(Money)
    settlement_asset: Mapped[str] = mapped_column(String(16))
    fill_type: Mapped[str] = mapped_column(String(30), default="normal")
    exchange_created_at: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DemoPosition(Base):
    __tablename__ = "demo_positions"
    product_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(64))
    signed_quantity: Mapped[Decimal] = mapped_column(Money)
    entry_price: Mapped[Decimal] = mapped_column(Money)
    mark_price: Mapped[Decimal] = mapped_column(Money)
    margin: Mapped[Decimal] = mapped_column(Money)
    unrealized_pnl: Mapped[Decimal] = mapped_column(Money, default=0)
    realized_pnl: Mapped[Decimal] = mapped_column(Money, default=0)
    realized_funding: Mapped[Decimal] = mapped_column(Money, default=0)
    managed: Mapped[bool] = mapped_column(Boolean, default=False)
    protection_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DemoEquitySnapshot(Base):
    __tablename__ = "demo_equity_snapshots"
    id: Mapped[int] = mapped_column(primary_key=True)
    equity: Mapped[Decimal] = mapped_column(Money)
    available_margin: Mapped[Decimal] = mapped_column(Money)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DemoStrategy(Base):
    __tablename__ = "demo_strategy_runs"
    __table_args__ = (UniqueConstraint("strategy_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    strategy_id: Mapped[int] = mapped_column(ForeignKey("strategies.id"))
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="PAUSED")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DemoProtection(Base):
    __tablename__ = "demo_protection_orders"
    exchange_order_id: Mapped[int] = mapped_column(primary_key=True)
    entry_order_id: Mapped[str] = mapped_column(ForeignKey("demo_orders.id"))
    product_id: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(30))
    stop_price: Mapped[Decimal] = mapped_column(Money)
    status: Mapped[str] = mapped_column(String(20))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DemoCandle(Base):
    __tablename__ = "demo_candles"
    __table_args__ = (UniqueConstraint("symbol", "timeframe_seconds", "timestamp"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(64))
    timeframe_seconds: Mapped[int] = mapped_column(Integer)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    open: Mapped[Decimal] = mapped_column(Money)
    high: Mapped[Decimal] = mapped_column(Money)
    low: Mapped[Decimal] = mapped_column(Money)
    close: Mapped[Decimal] = mapped_column(Money)
    volume: Mapped[Decimal] = mapped_column(Money)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DemoCashMovement(Base):
    __tablename__ = "demo_cash_movements"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    asset_id: Mapped[int] = mapped_column(Integer)
    asset_symbol: Mapped[str] = mapped_column(String(16))
    amount: Mapped[Decimal] = mapped_column(Money)
    balance: Mapped[Decimal] = mapped_column(Money)
    transaction_type: Mapped[str] = mapped_column(String(80))
    product_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    exchange_created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DemoPerformanceReport(Base):
    __tablename__ = "demo_performance_reports"
    __table_args__ = (UniqueConstraint("account_id", "period", "period_start", "period_end", "dataset_hash"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("demo_accounts.id"))
    period: Mapped[str] = mapped_column(String(16))
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    dataset_hash: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
