"""Shared pessimistic paper/backtest execution and strict order transitions."""

from __future__ import annotations

from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from enum import StrEnum

from pydantic import Field, model_validator

from .arithmetic import fixed_decimal

from .models import CoreModel, Instrument, Side


@fixed_decimal
def round_increment(value: Decimal, increment: Decimal, *, up: bool = False) -> Decimal:
    if increment <= 0 or value < 0:
        raise ValueError("rounding needs positive increment and nonnegative value")
    return (value / increment).to_integral_value(rounding=ROUND_CEILING if up else ROUND_FLOOR) * increment


class PaperFill(CoreModel):
    price: Decimal = Field(gt=0)
    quantity: Decimal = Field(gt=0)
    fee: Decimal = Field(ge=0)
    notional: Decimal = Field(gt=0)
    model: str = "FULL_FILL_ADVERSE_COSTS"


class PaperFillModel(CoreModel):
    """Declared full-fill model; no order-book liquidity or partial-fill claim."""

    fee_rate: Decimal = Field(default=Decimal(".0005"), ge=0, lt=1)
    slippage_bps: Decimal = Field(default=Decimal("2"), ge=0, lt=10000)
    spread_bps: Decimal = Field(default=Decimal("1"), ge=0, lt=10000)

    @fixed_decimal
    def execution_price(self, instrument: Instrument, side: Side, reference_price: Decimal) -> Decimal:
        if reference_price <= 0:
            raise ValueError("reference price must be positive")
        deviation = (self.slippage_bps + self.spread_bps / 2) / Decimal("10000")
        adjusted = reference_price * (1 + side.sign * deviation)
        price = round_increment(adjusted, instrument.tick_size, up=side is Side.BUY)
        if price <= 0:
            raise ValueError("price rounds to zero")
        return price

    @fixed_decimal
    def fill(self, instrument: Instrument, side: Side, quantity: Decimal, reference_price: Decimal) -> PaperFill:
        instrument.validate_quantity(quantity)
        price = self.execution_price(instrument, side, reference_price)
        return PaperFill(
            price=price,
            quantity=quantity,
            fee=instrument.fee(price, quantity, self.fee_rate),
            notional=instrument.notional(price, quantity),
        )


class OrderStatus(StrEnum):
    CREATED = "CREATED"
    RISK_APPROVED = "RISK_APPROVED"
    REJECTED = "REJECTED"
    SUBMITTING = "SUBMITTING"
    UNKNOWN = "UNKNOWN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCEL_PENDING = "CANCEL_PENDING"
    CANCELED = "CANCELED"
    EXPIRED = "EXPIRED"


TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.CREATED: frozenset({OrderStatus.RISK_APPROVED, OrderStatus.REJECTED}),
    OrderStatus.RISK_APPROVED: frozenset({OrderStatus.SUBMITTING, OrderStatus.REJECTED}),
    OrderStatus.SUBMITTING: frozenset(
        {
            OrderStatus.ACKNOWLEDGED,
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
            OrderStatus.UNKNOWN,
            OrderStatus.REJECTED,
        }
    ),
    OrderStatus.UNKNOWN: frozenset(
        {
            OrderStatus.ACKNOWLEDGED,
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
            OrderStatus.CANCELED,
            OrderStatus.REJECTED,
        }
    ),
    OrderStatus.ACKNOWLEDGED: frozenset(
        {
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
            OrderStatus.CANCEL_PENDING,
            OrderStatus.CANCELED,
            OrderStatus.EXPIRED,
        }
    ),
    OrderStatus.PARTIALLY_FILLED: frozenset(
        {OrderStatus.FILLED, OrderStatus.CANCEL_PENDING, OrderStatus.CANCELED, OrderStatus.EXPIRED}
    ),
    OrderStatus.CANCEL_PENDING: frozenset(
        {OrderStatus.CANCELED, OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED, OrderStatus.UNKNOWN}
    ),
    OrderStatus.FILLED: frozenset(),
    OrderStatus.CANCELED: frozenset(),
    OrderStatus.REJECTED: frozenset(),
    OrderStatus.EXPIRED: frozenset(),
}


def transition_order(current: OrderStatus, target: OrderStatus, *, reconciled: bool = False) -> OrderStatus:
    if target == current:
        return current  # duplicate acknowledgement is idempotent
    if target not in TRANSITIONS[current]:
        raise ValueError(f"illegal order transition {current.value} -> {target.value}")
    if current is OrderStatus.UNKNOWN and not reconciled:
        raise ValueError("UNKNOWN requires affirmative reconciliation evidence before resolution")
    return target


class OrderState(CoreModel):
    order_id: str
    client_order_id: str = Field(min_length=1, max_length=32)
    status: OrderStatus = OrderStatus.CREATED
    quantity: Decimal = Field(gt=0)
    filled_quantity: Decimal = Field(default=Decimal("0"), ge=0)

    @model_validator(mode="after")
    def valid_fill_state(self) -> OrderState:
        if self.filled_quantity > self.quantity:
            raise ValueError("filled quantity exceeds requested quantity")
        if self.status is OrderStatus.FILLED and self.filled_quantity != self.quantity:
            raise ValueError("FILLED requires complete fill evidence")
        if self.status is OrderStatus.PARTIALLY_FILLED and not 0 < self.filled_quantity < self.quantity:
            raise ValueError("partial status requires a strictly partial fill")
        return self

    def transition(
        self, target: OrderStatus, *, filled_quantity: Decimal | None = None, reconciled: bool = False
    ) -> OrderState:
        quantity = self.filled_quantity if filled_quantity is None else filled_quantity
        if quantity < self.filled_quantity:
            raise ValueError("cumulative fills cannot decrease")
        status = transition_order(self.status, target, reconciled=reconciled)
        return OrderState(
            order_id=self.order_id,
            client_order_id=self.client_order_id,
            status=status,
            quantity=self.quantity,
            filled_quantity=quantity,
        )
