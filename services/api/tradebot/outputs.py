"""Typed public response contracts. Economic Decimal values serialize as strings."""
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from tradebot.schemas import RiskInput


class Output(BaseModel):
    model_config = ConfigDict(extra="allow")


class AuthOutput(Output):
    username: str
    role: Literal["operator", "viewer"]
    csrf_token: str


class StrategyOutput(Output):
    id: int
    name: str
    description: str
    status: str
    current_version: int
    version: int
    approved_version: int | None
    config: dict[str, Any]
    last_error: str | None
    mode: Literal["PAPER"]
    created_at: datetime


class AccountOutput(Output):
    id: int
    mode: Literal["PAPER"]
    settlement_asset: str
    initial_balance: Decimal
    cash: Decimal
    equity: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    margin_used: Decimal
    available_margin: Decimal
    fees_paid: Decimal
    planning_capital_inr: Decimal
    usd_inr_rate: Decimal | None
    emergency_stop: bool
    reconciliation_required: bool
    simulated: Literal[True]
    reporting_currency: str
    planning_note: str


class RiskOutput(RiskInput):
    id: int
    emergency_stop: bool
    reconciliation_required: bool


class PositionOutput(Output):
    id: str
    account_id: int
    strategy_id: int
    symbol: str
    side: Literal["BUY", "SELL"]
    status: Literal["OPEN", "CLOSED"]
    quantity: Decimal
    contract_size: Decimal
    conversion_rate: Decimal
    entry_price: Decimal
    mark_price: Decimal
    stop_price: Decimal
    take_profit_price: Decimal
    margin: Decimal
    realized_pnl: Decimal
    protection_verified: bool
    opened_at: datetime
    closed_at: datetime | None


class OrderOutput(Output):
    id: str
    client_order_id: str
    signal_id: str | None
    strategy_id: int | None
    account_id: int
    position_id: str | None
    symbol: str
    side: str
    order_type: str
    status: str
    quantity: Decimal
    price: Decimal | None
    reduce_only: bool
    mode: Literal["PAPER"]
    created_at: datetime


class CandleOutput(Output):
    id: int
    symbol: str
    timeframe_seconds: int
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    received_at: datetime


class BacktestOutput(Output):
    id: int
    strategy_id: int
    version: int
    status: str
    dataset_hash: str
    result: dict[str, Any]
    created_at: datetime


class NotificationOutput(Output):
    id: int
    severity: str
    message: str
    read: bool
    created_at: datetime


class AuditOutput(Output):
    id: int
    actor: str
    action: str
    entity: str
    details: dict[str, Any]
    created_at: datetime


class DashboardOutput(Output):
    mode: Literal["PAPER"]
    live_enabled: Literal[False]
    simulated: Literal[True]
    account: AccountOutput | None
    metrics: dict[str, Any]
    strategies: list[StrategyOutput]
    positions: list[PositionOutput]
    recent_orders: list[OrderOutput]
    equity_curve: list[dict[str, Any]]
    worker: dict[str, Any]
    market_data: dict[str, Any]
    updated_at: datetime
