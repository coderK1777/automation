"""Cost-inclusive lot sizing and independent pre-trade portfolio gates."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .arithmetic import fixed_decimal

from .execution import PaperFillModel, round_increment
from .models import CoreModel, Instrument, Side, utc_timestamp

D = Decimal


class PositionSizingConfig(CoreModel):
    """Requested sizing style; every mode still obeys cost-inclusive stop risk."""
    mode: Literal["RISK_FRACTION", "FIXED_CONTRACTS", "FIXED_NOTIONAL", "VOLATILITY"] = "RISK_FRACTION"
    fixed_quantity: Decimal | None = Field(default=None, gt=0)
    fixed_notional: Decimal | None = Field(default=None, gt=0)
    volatility_period: int = Field(default=14, ge=2, le=1000)
    volatility_multiplier: Decimal = Field(default=D(2), gt=0, le=100)

    @model_validator(mode="after")
    def explicit_mode(self) -> PositionSizingConfig:
        if (self.mode == "FIXED_CONTRACTS") != (self.fixed_quantity is not None):
            raise ValueError("only FIXED_CONTRACTS requires fixed_quantity")
        if (self.mode == "FIXED_NOTIONAL") != (self.fixed_notional is not None):
            raise ValueError("only FIXED_NOTIONAL requires fixed_notional")
        return self


class PositionSize(CoreModel):
    quantity: Decimal = Field(ge=0)
    entry_price: Decimal = Field(gt=0)
    stop_price: Decimal = Field(gt=0)
    notional: Decimal = Field(ge=0)
    margin: Decimal = Field(ge=0)
    risk_amount: Decimal = Field(ge=0)
    estimated_fees: Decimal = Field(ge=0)
    reason: str = "SIZED"
    sizing_mode: str = "RISK_FRACTION"
    requested_quantity: Decimal | None = Field(default=None, ge=0)
    sizing_capped: bool = False


@fixed_decimal
def size_position(
    *,
    instrument: Instrument,
    side: Side,
    entry_price: Decimal,
    stop_price: Decimal,
    equity: Decimal,
    risk_fraction: Decimal,
    max_leverage: Decimal,
    fee_rate: Decimal = D(".0005"),
    slippage_bps: Decimal = D("2"),
    spread_bps: Decimal = D("1"),
    available_margin: Decimal | None = None,
    max_notional: Decimal | None = None,
    risk_budget: Decimal | None = None,
    sizing: PositionSizingConfig | None = None,
    volatility_atr: Decimal | None = None,
) -> PositionSize:
    if not instrument.metadata_verified or not instrument.trading_enabled:
        raise ValueError("verified enabled contract economics required for sizing")
    if equity <= 0 or not 0 < risk_fraction <= 1 or max_leverage < 1:
        raise ValueError("invalid equity, risk fraction or leverage")
    if stop_price <= 0 or entry_price <= 0 or (entry_price - stop_price) * side.sign <= 0:
        raise ValueError("protective stop must be on loss side of reference entry")
    if available_margin is not None and available_margin < 0:
        raise ValueError("available margin cannot be negative")
    if max_notional is not None and max_notional < 0:
        raise ValueError("notional limit cannot be negative")
    if risk_budget is not None and risk_budget < 0:
        raise ValueError("risk budget cannot be negative")
    model = PaperFillModel(fee_rate=fee_rate, slippage_bps=slippage_bps, spread_bps=spread_bps)
    entry = model.execution_price(instrument, side, entry_price)
    stop = round_increment(stop_price, instrument.tick_size, up=side is Side.SELL)
    if stop <= 0:
        raise ValueError("stop rounds to zero")
    stop_fill = model.execution_price(instrument, side.opposite, stop)
    fees_per_contract = instrument.fee(entry, D(1), fee_rate) + instrument.fee(stop_fill, D(1), fee_rate)
    risk_per_contract = -instrument.pnl(entry, stop_fill, D(1), side) + fees_per_contract
    if risk_per_contract <= 0:
        raise ValueError("protective stop must imply positive economic loss")
    budget = equity * risk_fraction
    if risk_budget is not None:
        budget = min(budget, risk_budget)
    margin_cap = equity if available_margin is None else min(equity, available_margin)
    policy = sizing or PositionSizingConfig()
    requested = budget/risk_per_contract
    if policy.mode == "FIXED_CONTRACTS":
        requested = policy.fixed_quantity
    elif policy.mode == "FIXED_NOTIONAL":
        requested = policy.fixed_notional/instrument.notional(entry,D(1))
    elif policy.mode == "VOLATILITY":
        if volatility_atr is None or not volatility_atr.is_finite() or volatility_atr <= 0:
            raise ValueError("VOLATILITY sizing requires a positive observed closed-bar ATR")
        volatility_cost = volatility_atr*policy.volatility_multiplier*instrument.contract_size*instrument.settlement_rate+fees_per_contract
        requested = budget/volatility_cost
    quantity = min(budget / risk_per_contract, margin_cap / instrument.margin(entry, D(1), max_leverage))
    quantity = min(quantity,requested)
    if max_notional is not None:
        quantity = min(quantity, max_notional / instrument.notional(entry, D(1)))
    if instrument.max_quantity is not None:
        quantity = min(quantity, instrument.max_quantity)
    quantity = round_increment(quantity, instrument.quantity_step)
    if quantity < instrument.min_quantity:
        quantity = D(0)
    return PositionSize(
        quantity=quantity,
        entry_price=entry,
        stop_price=stop,
        notional=instrument.notional(entry, quantity),
        margin=instrument.margin(entry, quantity, max_leverage),
        risk_amount=risk_per_contract * quantity,
        estimated_fees=fees_per_contract * quantity,
        reason="SIZED" if quantity else "BELOW_MINIMUM_AFTER_RISK_ROUNDING",
        sizing_mode=policy.mode,
        requested_quantity=requested,
        sizing_capped=quantity<requested,
    )


class RiskLimits(CoreModel):
    """Editable example policy in account settlement units, not universal advice."""

    max_risk_fraction: Decimal = Field(default=D(".01"), gt=0, le=1)
    max_risk_per_trade: Decimal = Field(default=D("500"), gt=0)
    max_committed_risk_fraction: Decimal = Field(default=D(".05"), gt=0, le=1)
    max_leverage: Decimal = Field(default=D("2"), ge=1)
    min_reward_risk: Decimal = Field(default=D("1"), ge=0)
    max_margin_fraction: Decimal = Field(default=D(".5"), gt=0, le=1)
    max_gross_exposure_multiple: Decimal = Field(default=D("2"), gt=0)
    max_net_exposure_multiple: Decimal = Field(default=D("2"), gt=0)
    max_correlated_exposure_multiple: Decimal | None = Field(default=None, gt=0)
    max_daily_loss_fraction: Decimal = Field(default=D(".05"), gt=0, le=1)
    max_weekly_loss_fraction: Decimal = Field(default=D(".1"), gt=0, le=1)
    max_drawdown_fraction: Decimal = Field(default=D(".1"), gt=0, le=1)
    max_open_positions: int = Field(default=3, ge=1)
    max_pending_orders: int = Field(default=10, ge=1)
    max_spread_bps: Decimal = Field(default=D("10"), ge=0)
    max_slippage_bps: Decimal = Field(default=D("25"), ge=0)
    market_max_age_seconds: int = Field(default=120, ge=1)
    conversion_max_age_seconds: int = Field(default=86400, ge=1)


class RiskContext(CoreModel):
    execution_environment: Literal["PAPER", "BACKTEST", "DEMO", "LIVE"] = "PAPER"
    equity: Decimal = Field(gt=0)
    available_margin: Decimal = Field(ge=0)
    margin_used: Decimal = Field(default=D(0), ge=0)
    gross_exposure: Decimal = Field(default=D(0), ge=0)
    net_exposure: Decimal = D(0)
    correlated_exposure: Decimal = Field(default=D(0), ge=0)
    reserved_risk: Decimal = Field(default=D(0), ge=0)
    open_position_risk: Decimal = Field(default=D(0), ge=0)
    daily_pnl: Decimal = D(0)
    weekly_pnl: Decimal = D(0)
    day_start_equity: Decimal = Field(gt=0)
    week_start_equity: Decimal = Field(gt=0)
    peak_equity: Decimal = Field(gt=0)
    open_positions: int = Field(default=0, ge=0)
    pending_orders: int = Field(default=0, ge=0)
    now: datetime
    market_timestamp: datetime
    account_reconciled: bool = False
    instrument_metadata_fresh: bool = False
    data_healthy: bool = False
    exchange_healthy: bool = False
    worker_healthy: bool = False
    clock_healthy: bool = False
    database_healthy: bool = False
    protective_orders_verified: bool = False
    has_unknown_orders: bool = True
    emergency_stop: bool = False

    _utc = field_validator("now", "market_timestamp")(utc_timestamp)


class RiskDecision(CoreModel):
    approved: bool
    reasons: list[str]
    approved_quantity: Decimal = Field(ge=0)
    estimated_risk: Decimal = Field(ge=0)
    required_margin: Decimal = Field(ge=0)


@fixed_decimal
def evaluate_risk(
    *,
    limits: RiskLimits,
    context: RiskContext,
    instrument: Instrument,
    side: Side,
    size: PositionSize,
    target_price: Decimal,
    leverage: Decimal,
    spread_bps: Decimal = D("1"),
    slippage_bps: Decimal = D("2"),
    fee_rate: Decimal = D(".0005"),
) -> RiskDecision:
    reasons = []
    checks = {
        "EMERGENCY_STOP": not context.emergency_stop,
        "UNRECONCILED_ACCOUNT": context.account_reconciled,
        "UNKNOWN_ORDER": not context.has_unknown_orders,
        "UNVERIFIED_METADATA": instrument.metadata_verified,
        "STALE_INSTRUMENT_METADATA": context.instrument_metadata_fresh,
        "INSTRUMENT_DISABLED": instrument.trading_enabled,
        "UNHEALTHY_MARKET_DATA": context.data_healthy,
        "EXCHANGE_UNHEALTHY": context.exchange_healthy,
        "WORKER_UNHEALTHY": context.worker_healthy,
        "CLOCK_UNHEALTHY": context.clock_healthy,
        "DATABASE_UNHEALTHY": context.database_healthy,
        "PROTECTION_UNVERIFIED": context.protective_orders_verified,
    }
    reasons.extend(name for name, passed in checks.items() if not passed)
    if instrument.verification_scope == "PAPER_MODEL" and context.execution_environment in ("DEMO", "LIVE"):
        reasons.append("PAPER_MODEL_NOT_EXCHANGE_VERIFIED")
    market_age = (context.now - context.market_timestamp).total_seconds()
    if market_age < -5 or market_age > limits.market_max_age_seconds:
        reasons.append("STALE_OR_FUTURE_MARKET_DATA")
    if instrument.quote_asset != instrument.settlement_asset and context.execution_environment != "BACKTEST":
        assert instrument.conversion_timestamp is not None
        age = (context.now - instrument.conversion_timestamp).total_seconds()
        if age < -5 or age > limits.conversion_max_age_seconds:
            reasons.append("STALE_OR_FUTURE_CONVERSION")
    try:
        instrument.validate_quantity(size.quantity)
    except ValueError:
        reasons.append("INVALID_QUANTITY")
    if size.quantity <= 0:
        reasons.append("ZERO_QUANTITY")
    if leverage < 1 or leverage > limits.max_leverage:
        reasons.append("LEVERAGE_LIMIT")
    if spread_bps < 0 or spread_bps > limits.max_spread_bps:
        reasons.append("SPREAD_LIMIT")
    if slippage_bps < 0 or slippage_bps > limits.max_slippage_bps:
        reasons.append("SLIPPAGE_LIMIT")
    if (size.entry_price - size.stop_price) * side.sign <= 0:
        reasons.append("INVALID_STOP")
    expected_notional = instrument.notional(size.entry_price, size.quantity)
    expected_margin = instrument.margin(size.entry_price, size.quantity, max(D(1), leverage))
    model = PaperFillModel(fee_rate=fee_rate, spread_bps=max(D(0), spread_bps), slippage_bps=max(D(0), slippage_bps))
    stop_fill = model.execution_price(instrument, side.opposite, size.stop_price)
    expected_fees = instrument.fee(size.entry_price, size.quantity, fee_rate) + instrument.fee(
        stop_fill, size.quantity, fee_rate
    )
    expected_risk = -instrument.pnl(size.entry_price, stop_fill, size.quantity, side) + expected_fees
    if (size.notional, size.margin, size.risk_amount, size.estimated_fees) != (
        expected_notional,
        expected_margin,
        expected_risk,
        expected_fees,
    ):
        reasons.append("INCONSISTENT_SIZE_ECONOMICS")
    if (
        context.open_position_risk + context.reserved_risk + size.risk_amount
        > context.equity * limits.max_committed_risk_fraction
    ):
        reasons.append("COMMITTED_RISK_LIMIT")
    if size.risk_amount > min(context.equity * limits.max_risk_fraction, limits.max_risk_per_trade):
        reasons.append("TRADE_RISK_LIMIT")
    if (
        size.margin > context.available_margin
        or context.margin_used + size.margin > context.equity * limits.max_margin_fraction
    ):
        reasons.append("MARGIN_LIMIT")
    if context.gross_exposure + size.notional > context.equity * limits.max_gross_exposure_multiple:
        reasons.append("GROSS_EXPOSURE_LIMIT")
    if abs(context.net_exposure + size.notional * side.sign) > context.equity * limits.max_net_exposure_multiple:
        reasons.append("NET_EXPOSURE_LIMIT")
    if limits.max_correlated_exposure_multiple is not None and (
        context.correlated_exposure+size.notional > context.equity*limits.max_correlated_exposure_multiple
    ):
        reasons.append("CORRELATED_EXPOSURE_LIMIT")
    if context.daily_pnl <= -context.day_start_equity * limits.max_daily_loss_fraction:
        reasons.append("DAILY_LOSS_LIMIT")
    if context.weekly_pnl <= -context.week_start_equity * limits.max_weekly_loss_fraction:
        reasons.append("WEEKLY_LOSS_LIMIT")
    if context.equity <= context.peak_equity * (1 - limits.max_drawdown_fraction):
        reasons.append("DRAWDOWN_LIMIT")
    if context.open_positions >= limits.max_open_positions:
        reasons.append("POSITION_LIMIT")
    if context.pending_orders >= limits.max_pending_orders:
        reasons.append("PENDING_ORDER_LIMIT")
    if target_price <= 0 or (target_price - size.entry_price) * side.sign <= 0:
        reasons.append("INVALID_TARGET")
    elif size.quantity > 0:
        model = PaperFillModel(fee_rate=fee_rate, spread_bps=spread_bps, slippage_bps=slippage_bps)
        target = model.execution_price(instrument, side.opposite, target_price)
        reward = (
            instrument.pnl(size.entry_price, target, size.quantity, side)
            - instrument.fee(size.entry_price, size.quantity, fee_rate)
            - instrument.fee(target, size.quantity, fee_rate)
        )
        if size.risk_amount <= 0 or reward / size.risk_amount < limits.min_reward_risk:
            reasons.append("REWARD_RISK_LIMIT")
    return RiskDecision(
        approved=not reasons,
        reasons=reasons,
        approved_quantity=size.quantity if not reasons else D(0),
        estimated_risk=size.risk_amount,
        required_margin=size.margin,
    )
