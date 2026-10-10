"""Reusable declared OHLC execution and completed-bar protection policies.

These are internal simulator assumptions, never exchange liquidity estimates.
New trailing levels become effective only on the following candle.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field, model_validator

from .arithmetic import fixed_decimal
from .execution import PaperFill, PaperFillModel, round_increment
from .models import Candle, CoreModel, Instrument, Side

D = Decimal


class TargetLeg(CoreModel):
    reward_multiple: Decimal = Field(gt=0, le=100)
    fraction: Decimal = Field(gt=0, le=1)


class ProtectionRules(CoreModel):
    targets: tuple[TargetLeg, ...] = Field(default=(), max_length=10)
    trailing_method: Literal["NONE", "FIXED", "PERCENT", "ATR", "STRUCTURE"] = "NONE"
    trailing_distance: Decimal | None = Field(default=None, gt=0)
    trailing_period: int = Field(default=14, ge=2, le=1000)
    trailing_activation_r: Decimal = Field(default=D(0), ge=0)
    break_even_trigger_r: Decimal | None = Field(default=None, gt=0)
    break_even_include_costs: bool = True
    max_holding_bars: int | None = Field(default=None, ge=1, le=100000)
    opposite_signal_exit: bool = True
    trend_invalidation_exit: bool = False
    dynamic_target_atr_multiple: Decimal | None = Field(default=None, gt=0, le=100)

    @model_validator(mode="after")
    def validate_policy(self) -> ProtectionRules:
        if self.targets:
            if sum((leg.fraction for leg in self.targets), D(0)) != 1:
                raise ValueError("target fractions must sum exactly to one")
            rewards = [leg.reward_multiple for leg in self.targets]
            if rewards != sorted(set(rewards)):
                raise ValueError("target reward multiples must be strictly increasing")
            if self.dynamic_target_atr_multiple is not None:
                raise ValueError("staged targets and a single dynamic target cannot be combined")
        if self.trailing_method in ("FIXED", "PERCENT", "ATR") and self.trailing_distance is None:
            raise ValueError("trailing method requires explicit distance or ATR multiplier")
        if self.trailing_method == "PERCENT" and self.trailing_distance >= 1:
            raise ValueError("percentage trailing distance must be less than one")
        return self


class AdvancedExecutionConfig(CoreModel):
    entry_type: Literal["MARKET", "LIMIT"] = "MARKET"
    limit_offset_fraction: Decimal = Field(default=D(".001"), ge=0, lt=1)
    pending_expiry_bars: int = Field(default=3, ge=1, le=1000)
    volume_participation_fraction: Decimal | None = Field(default=None, gt=0, le=1)
    volume_unit: Literal["CONTRACTS", "UNDERLYING"] = "CONTRACTS"
    maintenance_margin_rate: Decimal | None = Field(default=None, gt=0, lt=1)
    liquidation_fee_rate: Decimal = Field(default=D(0), ge=0, lt=1)

    @model_validator(mode="after")
    def liquidation_rates(self) -> AdvancedExecutionConfig:
        if self.maintenance_margin_rate is not None and self.maintenance_margin_rate+self.liquidation_fee_rate >= 1:
            raise ValueError("maintenance plus liquidation fees must be below notional")
        return self


class SimulatedOrderEvent(CoreModel):
    timestamp: datetime
    signal_id: str
    event: Literal["CREATED", "PARTIALLY_FILLED", "FILLED", "EXPIRED", "CANCELED", "REJECTED", "PROTECTION_UPDATED"]
    requested_quantity: Decimal = Field(ge=0)
    cumulative_filled_quantity: Decimal = Field(ge=0)
    price: Decimal | None = None
    reason: str


@fixed_decimal
def volume_capacity(candle: Candle, instrument: Instrument, config: AdvancedExecutionConfig) -> Decimal | None:
    """Whole-bar capacity is usable only as an end-of-bar fill approximation."""
    if config.volume_participation_fraction is None:
        return None
    quantity = candle.volume * config.volume_participation_fraction
    if config.volume_unit == "UNDERLYING":
        quantity /= instrument.contract_size
    return round_increment(quantity, instrument.quantity_step)


@fixed_decimal
def limit_fill(
    instrument: Instrument, side: Side, quantity: Decimal, limit_price: Decimal,
    candle: Candle, model: PaperFillModel,
) -> PaperFill | None:
    """Require penetration by adverse execution costs; never exceed the limit."""
    touched = candle.low < limit_price if side is Side.BUY else candle.high > limit_price
    if not touched:
        return None
    reference = min(candle.open, limit_price) if side is Side.BUY else max(candle.open, limit_price)
    price = model.execution_price(instrument, side, reference)
    # A mere touch does not promise queue priority. A penetrated bar may fill at
    # the limit if adverse costs at its extreme still fit the requested limit.
    extreme = candle.low if side is Side.BUY else candle.high
    extreme_price = model.execution_price(instrument, side, extreme)
    if (side is Side.BUY and extreme_price > limit_price) or (side is Side.SELL and extreme_price < limit_price):
        return None
    price = min(price, limit_price) if side is Side.BUY else max(price, limit_price)
    return PaperFill(price=price, quantity=quantity, fee=instrument.fee(price, quantity, model.fee_rate),
                     notional=instrument.notional(price, quantity), model="OHLC_PENETRATION_LIMIT")


@fixed_decimal
def next_protective_stop(
    *, side: Side, entry_price: Decimal, initial_stop: Decimal, current_stop: Decimal,
    favorable_price: Decimal, candle: Candle, instrument: Instrument, rules: ProtectionRules,
    atr_value: Decimal | None = None, structure_level: Decimal | None = None,
    break_even_price: Decimal | None = None,
) -> Decimal:
    """Monotonic stop derived from completed data; caller persists next-bar effect."""
    risk = abs(entry_price - initial_stop)
    movement = (favorable_price - entry_price) * side.sign
    proposed = current_stop
    if rules.break_even_trigger_r is not None and movement >= risk * rules.break_even_trigger_r:
        proposed = max(proposed, break_even_price or entry_price) if side is Side.BUY else min(
            proposed, break_even_price or entry_price
        )
    candidate = None
    if movement >= risk * rules.trailing_activation_r:
        if rules.trailing_method == "FIXED":
            candidate = favorable_price - side.sign * rules.trailing_distance
        elif rules.trailing_method == "PERCENT":
            candidate = favorable_price * (1 - side.sign * rules.trailing_distance)
        elif rules.trailing_method == "ATR" and atr_value is not None:
            candidate = favorable_price - side.sign * atr_value * rules.trailing_distance
        elif rules.trailing_method == "STRUCTURE":
            candidate = structure_level
    if candidate is not None and candidate > 0:
        proposed = max(proposed, candidate) if side is Side.BUY else min(proposed, candidate)
    # Round toward greater protection but keep strictly loss-side of next
    # reference. A completed candle cannot install a stop through its close.
    proposed = round_increment(proposed, instrument.tick_size, up=side is Side.BUY)
    if side is Side.BUY:
        return max(current_stop, min(proposed, candle.close - instrument.tick_size))
    return min(current_stop, max(proposed, candle.close + instrument.tick_size))


@fixed_decimal
def isolated_liquidation_price(
    *, instrument: Instrument, side: Side, entry_price: Decimal, quantity: Decimal,
    isolated_margin: Decimal, funding_paid: Decimal, maintenance_margin_rate: Decimal,
    liquidation_fee_rate: Decimal = D(0),
) -> Decimal | None:
    """Declared isolated linear model; no Delta cross-margin certification.

    Solve margin - funding + signed PnL = mark notional * (maintenance + fee).
    """
    if quantity <= 0 or isolated_margin <= 0:
        raise ValueError("liquidation model needs positive quantity and isolated margin")
    multiplier = quantity * instrument.contract_size * instrument.settlement_rate
    cushion = (isolated_margin - funding_paid) / multiplier
    requirement = maintenance_margin_rate + liquidation_fee_rate
    denominator = side.sign - requirement
    price = (side.sign * entry_price - cushion) / denominator
    return price if price > 0 else None


@fixed_decimal
def break_even_reference_price(
    *, instrument: Instrument, side: Side, entry_price: Decimal, quantity: Decimal,
    entry_fees: Decimal, funding_paid: Decimal, model: PaperFillModel,
) -> Decimal:
    """Stop reference covering paid costs and the modeled adverse closing fill."""
    if quantity <= 0 or entry_fees < 0:
        raise ValueError("break-even needs positive quantity and nonnegative entry fees")
    paid_per_unit = (entry_fees+funding_paid)/(quantity*instrument.contract_size*instrument.settlement_rate)
    deviation = (model.slippage_bps+model.spread_bps/2)/D(10000)
    if side is Side.BUY:
        executable = (entry_price+paid_per_unit)/(1-model.fee_rate)
        reference = (executable+instrument.tick_size)/(1-deviation)
        return round_increment(reference,instrument.tick_size,up=True)
    executable = (entry_price-paid_per_unit)/(1+model.fee_rate)
    reference = (executable-instrument.tick_size)/(1+deviation)
    if reference <= 0:
        raise ValueError("short break-even price is nonpositive after costs")
    return round_increment(reference,instrument.tick_size)
