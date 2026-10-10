"""Validated immutable data contracts; all economic amounts use Decimal."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .arithmetic import fixed_decimal

ZERO = Decimal("0")
ONE = Decimal("1")


class CoreModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"

    @property
    def sign(self) -> Decimal:
        return ONE if self is Side.BUY else -ONE

    @property
    def opposite(self) -> Side:
        return Side.SELL if self is Side.BUY else Side.BUY


def utc_timestamp(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timezone-aware UTC timestamps are required")
    return value.astimezone(timezone.utc)


class Candle(CoreModel):
    """Timestamp denotes bar OPEN; signals become available after its close."""

    timestamp: datetime
    timeframe_seconds: int = Field(default=60, ge=1)
    open: Decimal = Field(gt=0)
    high: Decimal = Field(gt=0)
    low: Decimal = Field(gt=0)
    close: Decimal = Field(gt=0)
    volume: Decimal = Field(default=ZERO, ge=0)
    closed: bool = True

    _utc = field_validator("timestamp")(utc_timestamp)

    @model_validator(mode="after")
    def valid_ohlc(self) -> Candle:
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close):
            raise ValueError("OHLC values violate low <= open/close <= high")
        if self.low > self.high:
            raise ValueError("low must not exceed high")
        if self.timestamp.microsecond or int(self.timestamp.timestamp()) % self.timeframe_seconds:
            raise ValueError("candle timestamp must align to its timeframe")
        return self


def validate_candles(candles: list[Candle], *, allow_open_last: bool = False) -> None:
    """Reject missing/duplicate/out-of-order candles instead of filling invented prices."""
    for index, candle in enumerate(candles):
        if not candle.closed and not (allow_open_last and index == len(candles) - 1):
            raise ValueError("only closed candles may influence calculations")
        if index:
            previous = candles[index - 1]
            if candle.timeframe_seconds != previous.timeframe_seconds:
                raise ValueError("mixed candle timeframes are not supported")
            delta = (candle.timestamp - previous.timestamp).total_seconds()
            if delta != candle.timeframe_seconds:
                raise ValueError("candles must be strictly ordered, unique and contiguous; missing data")


class Instrument(CoreModel):
    """Explicit linear-futures economics, never inferred from a product name.

    Cross-currency products need an operator verified conversion snapshot. This
    model does not implement inverse contracts or exchange-specific quanto PnL.
    """

    product_id: int = Field(gt=0)
    symbol: str = Field(min_length=1, max_length=64)
    contract_type: Literal["LINEAR"] = "LINEAR"
    underlying_asset: str = Field(min_length=1)
    quote_asset: str = Field(min_length=1)
    settlement_asset: str = Field(min_length=1)
    contract_size: Decimal = Field(gt=0)
    tick_size: Decimal = Field(gt=0)
    quantity_step: Decimal = Field(gt=0)
    min_quantity: Decimal = Field(gt=0)
    max_quantity: Decimal | None = Field(default=None, gt=0)
    initial_margin_rate: Decimal = Field(gt=0, le=1)
    quote_to_settlement_rate: Decimal | None = Field(default=None, gt=0)
    conversion_timestamp: datetime | None = None
    metadata_verified: bool = False
    verification_scope: Literal["EXCHANGE", "PAPER_MODEL"] = "EXCHANGE"
    assumptions: tuple[str, ...] = Field(default=(), max_length=20)
    metadata_timestamp: datetime
    trading_enabled: bool = True

    _utc = field_validator("metadata_timestamp")(utc_timestamp)

    @field_validator("conversion_timestamp")
    @classmethod
    def valid_conversion_time(cls, value: datetime | None) -> datetime | None:
        return utc_timestamp(value) if value else None

    @model_validator(mode="after")
    @fixed_decimal
    def validate_economics(self) -> Instrument:
        if self.verification_scope == "PAPER_MODEL" and not self.assumptions:
            raise ValueError("internal paper economics require declared assumptions")
        if self.min_quantity % self.quantity_step:
            raise ValueError("minimum quantity must align to quantity step")
        if self.max_quantity is not None:
            if self.max_quantity < self.min_quantity or self.max_quantity % self.quantity_step:
                raise ValueError("invalid maximum quantity/increment")
        if self.quote_asset == self.settlement_asset:
            if self.quote_to_settlement_rate not in (None, ONE):
                raise ValueError("identical quote and settlement require rate one")
        elif self.quote_to_settlement_rate is None or self.conversion_timestamp is None:
            raise ValueError("cross-currency settlement requires an explicit verified conversion snapshot")
        return self

    @property
    def settlement_rate(self) -> Decimal:
        return self.quote_to_settlement_rate or ONE

    @fixed_decimal
    def validate_quantity(self, quantity: Decimal) -> None:
        if quantity < self.min_quantity or quantity % self.quantity_step:
            raise ValueError("quantity violates exchange minimum or increment")
        if self.max_quantity is not None and quantity > self.max_quantity:
            raise ValueError("quantity exceeds exchange maximum")

    @fixed_decimal
    def notional(self, price: Decimal, quantity: Decimal) -> Decimal:
        if price <= 0 or quantity < 0:
            raise ValueError("price must be positive and quantity nonnegative")
        return price * quantity * self.contract_size * self.settlement_rate

    @fixed_decimal
    def margin(self, price: Decimal, quantity: Decimal, leverage: Decimal) -> Decimal:
        if leverage < ONE:
            raise ValueError("leverage must be at least one")
        return self.notional(price, quantity) * max(self.initial_margin_rate, ONE / leverage)

    @fixed_decimal
    def pnl(self, entry: Decimal, exit: Decimal, quantity: Decimal, side: Side) -> Decimal:
        if min(entry, exit) <= 0 or quantity < 0:
            raise ValueError("invalid PnL prices or quantity")
        return (exit - entry) * quantity * self.contract_size * self.settlement_rate * side.sign

    @fixed_decimal
    def fee(self, price: Decimal, quantity: Decimal, rate: Decimal) -> Decimal:
        if rate < ZERO:
            raise ValueError("fee rate must be nonnegative")
        return self.notional(price, quantity) * rate


class Signal(CoreModel):
    signal_id: str
    strategy_version: int
    symbol: str
    side: Side
    timestamp: datetime
    candle_timestamp: datetime
    reference_price: Decimal = Field(gt=0)
    stop_price: Decimal = Field(gt=0)
    target_price: Decimal = Field(gt=0)
    reason: str
    data_snapshot_id: str

    _utc = field_validator("timestamp", "candle_timestamp")(utc_timestamp)
