"""Deterministic, event-driven single-instrument OHLC research engine."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, localcontext
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .arithmetic import fixed_decimal

from .execution import PaperFill, PaperFillModel, round_increment
from .models import Candle, CoreModel, Instrument, Side, Signal, utc_timestamp, validate_candles
from .risk import RiskContext, RiskLimits, evaluate_risk, size_position
from .strategy import StrategyConfig, StrategyEvaluator
from .indicators import atr
from .context import EvaluationContext
from .simulation import AdvancedExecutionConfig, SimulatedOrderEvent

D = Decimal
ENGINE_VERSION = "ohlc-v2"


class FundingEvent(CoreModel):
    timestamp: datetime
    rate: Decimal
    mark_price: Decimal = Field(gt=0)
    source: str = Field(min_length=1)
    _utc = field_validator("timestamp")(utc_timestamp)


class BacktestConfig(CoreModel):
    initial_capital: Decimal = Field(default=D("50000"), gt=0)
    dataset_version: str = Field(min_length=1, max_length=200)
    fee_rate: Decimal = Field(default=D(".0005"), ge=0, lt=1)
    spread_bps: Decimal = Field(default=D("1"), ge=0, lt=10000)
    slippage_bps: Decimal = Field(default=D("2"), ge=0, lt=10000)
    funding_mode: Literal["EXPLICIT_ZERO", "PROVIDED"] = "EXPLICIT_ZERO"
    funding_events: list[FundingEvent] = Field(default_factory=list)
    risk_limits: RiskLimits = Field(default_factory=RiskLimits)
    max_candles: int = Field(default=10000, ge=2, le=100000)
    execution_model: AdvancedExecutionConfig | None = None
    trade_start_timestamp: datetime | None = None

    @field_validator("trade_start_timestamp")
    @classmethod
    def trading_start_utc(cls, value: datetime | None) -> datetime | None:
        return utc_timestamp(value) if value else None

    @model_validator(mode="after")
    def funding_contract(self) -> BacktestConfig:
        if self.funding_mode == "EXPLICIT_ZERO" and self.funding_events:
            raise ValueError("zero-funding assumption cannot include funding events")
        stamps = [event.timestamp for event in self.funding_events]
        if len(set(stamps)) != len(stamps):
            raise ValueError("duplicate funding events are not permitted")
        if stamps != sorted(stamps):
            raise ValueError("funding events must be chronological")
        return self


class BacktestTrade(CoreModel):
    signal_id: str
    side: Side
    quantity: Decimal
    signal_timestamp: datetime
    entry_timestamp: datetime
    exit_timestamp: datetime
    entry_price: Decimal
    exit_price: Decimal
    stop_price: Decimal
    target_price: Decimal
    gross_pnl: Decimal
    fees: Decimal
    funding: Decimal
    net_pnl: Decimal
    exit_reason: str
    holding_seconds: int
    entry_fill_count: int = 1
    exit_fill_count: int = 1


class EquityPoint(CoreModel):
    timestamp: datetime
    equity: Decimal
    cash: Decimal
    unrealized_pnl: Decimal
    gross_exposure: Decimal
    margin_used: Decimal


class RejectedSignal(CoreModel):
    signal_id: str
    timestamp: datetime
    reasons: list[str]


class BacktestMetrics(CoreModel):
    total_trades: int
    winning_trades: int
    losing_trades: int
    break_even_trades: int
    net_profit: Decimal
    net_return: Decimal
    gross_profit: Decimal
    gross_loss: Decimal
    win_rate: Decimal | None
    profit_factor: Decimal | None
    average_win: Decimal | None
    average_loss: Decimal | None
    expectancy: Decimal | None
    max_drawdown: Decimal
    max_drawdown_fraction: Decimal
    recovery_factor: Decimal | None
    fees: Decimal
    funding: Decimal
    average_holding_seconds: Decimal | None
    sharpe_ratio: Decimal | None
    sortino_ratio: Decimal | None
    calmar_ratio: Decimal | None
    daily_return_samples: int
    max_consecutive_wins: int
    max_consecutive_losses: int
    long_trades: int
    short_trades: int
    monthly_returns: dict[str, Decimal] = Field(default_factory=dict)
    long_net_pnl: Decimal = D(0)
    short_net_pnl: Decimal = D(0)
    average_margin_deployed: Decimal = D(0)
    return_on_average_margin: Decimal | None = None
    exposure_fraction: Decimal = D(0)
    exposure_adjusted_return: Decimal | None = None


class BacktestResult(CoreModel):
    engine_version: str = ENGINE_VERSION
    environment: Literal["BACKTEST"] = "BACKTEST"
    dataset_version: str
    dataset_hash: str
    reproducibility_hash: str
    strategy: StrategyConfig
    instrument: Instrument
    execution: BacktestConfig
    currency: str
    starting_capital: Decimal
    ending_equity: Decimal
    trades: list[BacktestTrade]
    equity_curve: list[EquityPoint]
    rejected_signals: list[RejectedSignal]
    metrics: BacktestMetrics
    assumptions: list[str]
    warnings: list[str]
    order_events: list[SimulatedOrderEvent] = Field(default_factory=list)


@dataclass
class _Position:
    signal: Signal
    quantity: Decimal
    fill: PaperFill
    entry_timestamp: datetime
    stop_price: Decimal
    target_price: Decimal
    margin: Decimal
    funding: Decimal = D(0)


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def strategy_risk_reasons(strategy: StrategyConfig, equity: Decimal, peak: Decimal, trades: list[BacktestTrade]) -> list[str]:
    """Per-strategy historical gates use only already realized trades/equity."""
    reasons = []
    if strategy.max_drawdown_fraction is not None and equity <= peak*(1-strategy.max_drawdown_fraction):
        reasons.append("STRATEGY_DRAWDOWN_LIMIT")
    if strategy.max_consecutive_losses is not None:
        losses = 0
        for trade in reversed(trades):
            if trade.net_pnl >= 0:
                break
            losses += 1
        if losses >= strategy.max_consecutive_losses:
            reasons.append("STRATEGY_CONSECUTIVE_LOSS_LIMIT")
    return reasons


@fixed_decimal
def calculate_metrics(
    initial: Decimal, ending: Decimal, trades: list[BacktestTrade], curve: list[EquityPoint]
) -> BacktestMetrics:
    profits = [trade.net_pnl for trade in trades if trade.net_pnl > 0]
    losses = [trade.net_pnl for trade in trades if trade.net_pnl < 0]
    gross_profit, gross_loss = sum(profits, D(0)), -sum(losses, D(0))
    peak, drawdown, fraction = initial, D(0), D(0)
    for point in curve:
        peak = max(peak, point.equity)
        drawdown = max(drawdown, peak - point.equity)
        fraction = max(fraction, (peak - point.equity) / peak)
    wins = loss_run = max_wins = max_losses = 0
    for trade in trades:
        if trade.net_pnl > 0:
            wins += 1
            loss_run = 0
        elif trade.net_pnl < 0:
            loss_run += 1
            wins = 0
        else:
            wins = loss_run = 0
        max_wins, max_losses = max(max_wins, wins), max(max_losses, loss_run)
    # One closing observation per UTC day; intraday bar returns are not passed
    # off as independent daily samples. Need >=30 full daily intervals.
    daily_points = [point for point in curve if point.timestamp.time().isoformat() == "00:00:00"]
    returns = [
        current.equity / previous.equity - 1
        for previous, current in zip(daily_points, daily_points[1:])
        if previous.equity > 0 and current.timestamp - previous.timestamp == timedelta(days=1)
    ]
    sharpe = sortino = calmar = None
    if len(returns) >= 30:
        with localcontext() as context:
            context.prec = 34
            average = sum(returns, D(0)) / len(returns)
            variance = sum((value - average) ** 2 for value in returns) / (len(returns) - 1)
            if variance > 0:
                sharpe = average / variance.sqrt() * D(365).sqrt()
            negative = [value for value in returns if value < 0]
            if len(negative) >= 2:
                downside = (sum(min(D(0), value) ** 2 for value in returns) / len(returns)).sqrt()
                if downside > 0:
                    sortino = average / downside * D(365).sqrt()
            # Geometric annualization requires >=365 days and positive equity.
            if len(returns) >= 365 and fraction > 0 and ending > 0:
                annual_return = context.power(ending / initial, D(365) / D(len(returns))) - 1
                calmar = annual_return / fraction
    count = len(trades)
    # Exposure is sampled at bar closes with the same equally spaced bars as
    # the curve; partial first/last calendar months are explicitly reported.
    exposure_fraction = D(sum(point.gross_exposure > 0 for point in curve)) / len(curve) if curve else D(0)
    average_margin = sum((point.margin_used for point in curve), D(0)) / len(curve) if curve else D(0)
    monthly: dict[str, Decimal] = {}
    month_start, previous_equity, current_month = initial, initial, None
    for point in curve:
        month = point.timestamp.strftime("%Y-%m")
        if current_month is not None and month != current_month:
            month_start = previous_equity
        current_month = month
        monthly[month] = point.equity / month_start - 1 if month_start > 0 else D(0)
        previous_equity = point.equity
    return BacktestMetrics(
        total_trades=count,
        winning_trades=len(profits),
        losing_trades=len(losses),
        break_even_trades=count - len(profits) - len(losses),
        net_profit=ending - initial,
        net_return=ending / initial - 1,
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        win_rate=D(len(profits)) / count if count else None,
        profit_factor=gross_profit / gross_loss if gross_loss else None,
        average_win=gross_profit / len(profits) if profits else None,
        average_loss=-gross_loss / len(losses) if losses else None,
        expectancy=sum((t.net_pnl for t in trades), D(0)) / count if count else None,
        max_drawdown=drawdown,
        max_drawdown_fraction=fraction,
        recovery_factor=(ending - initial) / drawdown if drawdown else None,
        fees=sum((t.fees for t in trades), D(0)),
        funding=sum((t.funding for t in trades), D(0)),
        average_holding_seconds=D(sum(t.holding_seconds for t in trades)) / count if count else None,
        sharpe_ratio=sharpe,
        sortino_ratio=sortino,
        calmar_ratio=calmar,
        daily_return_samples=len(returns),
        max_consecutive_wins=max_wins,
        max_consecutive_losses=max_losses,
        long_trades=sum(t.side is Side.BUY for t in trades),
        short_trades=sum(t.side is Side.SELL for t in trades),
        monthly_returns=monthly,
        long_net_pnl=sum((t.net_pnl for t in trades if t.side is Side.BUY), D(0)),
        short_net_pnl=sum((t.net_pnl for t in trades if t.side is Side.SELL), D(0)),
        average_margin_deployed=average_margin,
        return_on_average_margin=(ending-initial)/average_margin if average_margin else None,
        exposure_fraction=exposure_fraction,
        exposure_adjusted_return=(ending/initial-1)/exposure_fraction if exposure_fraction else None,
    )


class BacktestEngine:
    @fixed_decimal
    def run(
        self, candles: list[Candle], instrument: Instrument, strategy: StrategyConfig, config: BacktestConfig,
        *, higher_timeframes: dict[int, list[Candle]] | None = None,
        evaluation_contexts: dict[datetime, EvaluationContext] | None = None,
    ) -> BacktestResult:
        # Isolate Decimal precision from callers to keep the hash/model repeatable.
        with localcontext() as context:
            context.prec = 34
            if config.execution_model is not None or strategy.execution is not None or strategy.long_exit_rules or strategy.short_exit_rules or strategy.exit_rules.model_dump() != strategy.exit_rules.__class__().model_dump():
                from .advanced_backtest import run_advanced
                return run_advanced(candles, instrument, strategy, config, higher_timeframes=higher_timeframes,
                                    evaluation_contexts=evaluation_contexts)
            return self._run(candles, instrument, strategy, config, higher_timeframes=higher_timeframes,
                             evaluation_contexts=evaluation_contexts)

    def _run(
        self, candles: list[Candle], instrument: Instrument, strategy: StrategyConfig, config: BacktestConfig,
        *, higher_timeframes: dict[int, list[Candle]] | None = None,
        evaluation_contexts: dict[datetime, EvaluationContext] | None = None,
    ) -> BacktestResult:
        validate_candles(candles)
        if len(candles) < 2 or len(candles) > config.max_candles:
            raise ValueError("backtest requires 2..max_candles contiguous closed bars")
        if candles[0].timeframe_seconds != strategy.timeframe_seconds or instrument.symbol != strategy.symbol:
            raise ValueError("backtest instrument/timeframe must match strategy")
        if not instrument.metadata_verified:
            raise ValueError(
                "backtest needs explicit reviewed contract economics; public metadata alone is insufficient"
            )
        if "BACKTEST" not in strategy.allowed_environments:
            raise ValueError("strategy does not permit BACKTEST execution")
        evaluator = StrategyEvaluator(strategy, candles, higher_timeframes=higher_timeframes)
        sizing_ranges = atr(candles,strategy.sizing.volatility_period) if strategy.sizing.mode == "VOLATILITY" else []
        opening_times = {candle.timestamp for candle in candles}
        if any(event.timestamp not in opening_times for event in config.funding_events):
            raise ValueError("funding events must align to candle-open timestamps inside dataset")
        funding = {event.timestamp: event for event in config.funding_events}
        fill_model = PaperFillModel(
            fee_rate=config.fee_rate, slippage_bps=config.slippage_bps, spread_bps=config.spread_bps
        )
        cash = config.initial_capital
        peak = cash
        day_cash = week_cash = cash
        active_day = candles[0].timestamp.date()
        active_week = candles[0].timestamp.isocalendar()[:2]
        position: _Position | None = None
        pending: Signal | None = None
        trades: list[BacktestTrade] = []
        curve: list[EquityPoint] = []
        rejected: list[RejectedSignal] = []
        last_entry_index = -strategy.cooldown_bars - 1
        entries_today = 0

        def close(reference: Decimal, timestamp: datetime, reason: str) -> None:
            nonlocal cash, position
            assert position is not None
            exit_fill = fill_model.fill(instrument, position.signal.side.opposite, position.quantity, reference)
            gross = instrument.pnl(position.fill.price, exit_fill.price, position.quantity, position.signal.side)
            cash += gross - exit_fill.fee
            trades.append(
                BacktestTrade(
                    signal_id=position.signal.signal_id,
                    side=position.signal.side,
                    quantity=position.quantity,
                    signal_timestamp=position.signal.timestamp,
                    entry_timestamp=position.entry_timestamp,
                    exit_timestamp=timestamp,
                    entry_price=position.fill.price,
                    exit_price=exit_fill.price,
                    stop_price=position.stop_price,
                    target_price=position.target_price,
                    gross_pnl=gross,
                    fees=position.fill.fee + exit_fill.fee,
                    funding=position.funding,
                    net_pnl=gross - position.fill.fee - exit_fill.fee - position.funding,
                    exit_reason=reason,
                    holding_seconds=int((timestamp - position.entry_timestamp).total_seconds()),
                )
            )
            position = None

        for index, candle in enumerate(candles):
            had_position = position is not None
            bar_close = candle.timestamp + timedelta(seconds=candle.timeframe_seconds)
            unrealized_open = (
                instrument.pnl(position.fill.price, candle.open, position.quantity, position.signal.side)
                if position
                else D(0)
            )
            if candle.timestamp.date() != active_day:
                day_cash, active_day = cash + unrealized_open, candle.timestamp.date()
                entries_today = 0
            if candle.timestamp.isocalendar()[:2] != active_week:
                week_cash, active_week = cash + unrealized_open, candle.timestamp.isocalendar()[:2]
            if position and candle.timestamp in funding:
                event = funding[candle.timestamp]
                payment = (
                    instrument.notional(event.mark_price, position.quantity) * event.rate * position.signal.side.sign
                )
                position.funding += payment
                cash -= payment
            # Gap protective exits get first priority at open; stop gaps fill at
            # the worse open, TP gaps conservatively at the target reference.
            if position:
                if position.signal.side is Side.BUY:
                    if candle.open <= position.stop_price:
                        close(candle.open, candle.timestamp, "STOP_GAP")
                    elif candle.open >= position.target_price:
                        close(position.target_price, candle.timestamp, "TARGET_GAP")
                else:
                    if candle.open >= position.stop_price:
                        close(candle.open, candle.timestamp, "STOP_GAP")
                    elif candle.open <= position.target_price:
                        close(position.target_price, candle.timestamp, "TARGET_GAP")
            if position and pending and position.signal.side is not pending.side:
                close(candle.open, candle.timestamp, "OPPOSITE_SIGNAL")
            elif position is None and pending and not had_position:
                equity = cash
                if config.trade_start_timestamp is not None and candle.timestamp < config.trade_start_timestamp:
                    pass
                elif index-last_entry_index <= strategy.cooldown_bars:
                    rejected.append(RejectedSignal(signal_id=pending.signal_id,timestamp=candle.timestamp,reasons=["COOLDOWN_ACTIVE"]))
                elif strategy.max_trades_per_day is not None and entries_today >= strategy.max_trades_per_day:
                    rejected.append(RejectedSignal(signal_id=pending.signal_id,timestamp=candle.timestamp,reasons=["STRATEGY_DAILY_TRADE_LIMIT"]))
                elif strategy.max_daily_loss_fraction is not None and equity-day_cash <= -day_cash*strategy.max_daily_loss_fraction:
                    rejected.append(RejectedSignal(signal_id=pending.signal_id,timestamp=candle.timestamp,reasons=["STRATEGY_DAILY_LOSS_LIMIT"]))
                elif strategy_risk_reasons(strategy,equity,peak,trades):
                    rejected.append(RejectedSignal(signal_id=pending.signal_id,timestamp=candle.timestamp,reasons=strategy_risk_reasons(strategy,equity,peak,trades)))
                elif equity <= 0:
                    rejected.append(
                        RejectedSignal(
                            signal_id=pending.signal_id, timestamp=candle.timestamp, reasons=["NONPOSITIVE_EQUITY"]
                        )
                    )
                else:
                    limits = config.risk_limits
                    try:
                        size = size_position(
                            instrument=instrument,
                            side=pending.side,
                            entry_price=candle.open,
                            stop_price=pending.stop_price,
                            equity=equity,
                            risk_fraction=strategy.risk_fraction,
                            max_leverage=strategy.max_leverage,
                            fee_rate=config.fee_rate,
                            slippage_bps=config.slippage_bps,
                            spread_bps=config.spread_bps,
                            available_margin=equity * min(limits.max_margin_fraction,strategy.capital_allocation_fraction or D(1)),
                            max_notional=equity * limits.max_gross_exposure_multiple,
                            risk_budget=min(limits.max_risk_per_trade, equity * limits.max_risk_fraction),
                            sizing=strategy.sizing,
                            volatility_atr=sizing_ranges[index-1] if sizing_ranges and index>0 else None,
                        )
                        ctx = RiskContext(
                            execution_environment="BACKTEST",
                            equity=equity,
                            available_margin=equity,
                            day_start_equity=max(day_cash, D(".00000001")),
                            week_start_equity=max(week_cash, D(".00000001")),
                            peak_equity=peak,
                            daily_pnl=equity - day_cash,
                            weekly_pnl=equity - week_cash,
                            now=candle.timestamp,
                            market_timestamp=candle.timestamp,
                            account_reconciled=True,
                            instrument_metadata_fresh=True,
                            data_healthy=True,
                            exchange_healthy=True,
                            worker_healthy=True,
                            clock_healthy=True,
                            database_healthy=True,
                            protective_orders_verified=True,
                            has_unknown_orders=False,
                        )
                        decision = evaluate_risk(
                            limits=limits,
                            context=ctx,
                            instrument=instrument,
                            side=pending.side,
                            size=size,
                            target_price=pending.target_price,
                            leverage=strategy.max_leverage,
                            fee_rate=config.fee_rate,
                            spread_bps=config.spread_bps,
                            slippage_bps=config.slippage_bps,
                        )
                        if decision.approved:
                            fill = fill_model.fill(instrument, pending.side, size.quantity, candle.open)
                            cash -= fill.fee
                            target = round_increment(
                                pending.target_price, instrument.tick_size, up=pending.side is Side.SELL
                            )
                            position = _Position(
                                signal=pending,
                                quantity=size.quantity,
                                fill=fill,
                                entry_timestamp=candle.timestamp,
                                stop_price=size.stop_price,
                                target_price=target,
                                margin=size.margin,
                            )
                            last_entry_index = index
                            entries_today += 1
                        else:
                            rejected.append(
                                RejectedSignal(
                                    signal_id=pending.signal_id, timestamp=candle.timestamp, reasons=decision.reasons
                                )
                            )
                    except ValueError as error:
                        rejected.append(
                            RejectedSignal(
                                signal_id=pending.signal_id, timestamp=candle.timestamp, reasons=[str(error)]
                            )
                        )
            # Intrabar path unknown: assume loss-side stop occurs before target.
            if position:
                stop_hit = (
                    candle.low <= position.stop_price
                    if position.signal.side is Side.BUY
                    else candle.high >= position.stop_price
                )
                target_hit = (
                    candle.high >= position.target_price
                    if position.signal.side is Side.BUY
                    else candle.low <= position.target_price
                )
                if stop_hit:
                    close(position.stop_price, bar_close, "STOP_FIRST_AMBIGUOUS" if target_hit else "STOP")
                elif target_hit:
                    close(position.target_price, bar_close, "TARGET")
            if index == len(candles) - 1 and position:
                close(candle.close, bar_close, "END_OF_DATA")
            unrealized = (
                instrument.pnl(position.fill.price, candle.close, position.quantity, position.signal.side)
                if position
                else D(0)
            )
            equity = cash + unrealized
            peak = max(peak, equity)
            curve.append(
                EquityPoint(
                    timestamp=bar_close,
                    equity=equity,
                    cash=cash,
                    unrealized_pnl=unrealized,
                    gross_exposure=instrument.notional(candle.close, position.quantity) if position else D(0),
                    margin_used=instrument.margin(candle.close, position.quantity, strategy.max_leverage)
                    if position
                    else D(0),
                )
            )
            pending = evaluator.signal_at(index, context=(evaluation_contexts or {}).get(bar_close)) if index < len(candles) - 1 else None
        dataset_hash = _hash([candle.model_dump(mode="json") for candle in candles])
        fingerprint = _hash(
            {
                "engine_version": ENGINE_VERSION,
                "dataset_hash": dataset_hash,
                "instrument": instrument.model_dump(mode="json"),
                "strategy": strategy.model_dump(mode="json"),
                "config": config.model_dump(mode="json"),
                "higher_timeframes": {str(seconds): [bar.model_dump(mode="json") for bar in bars] for seconds, bars in (higher_timeframes or {}).items()},
                "evaluation_contexts": {stamp.isoformat(): context.model_dump(mode="json") for stamp, context in (evaluation_contexts or {}).items()},
            }
        )
        warnings = [
            "Research simulation; no exchange execution, liquidation, order-book liquidity or partial-fill model.",
            "OHLC stop-first ambiguity; intrabar exit timestamps use bar close and holding times are approximate.",
            "Fixed verified instrument/conversion snapshot; historical economics and changing FX are not reconstructed.",
            "Drawdown uses candle-close equity; intrabar mark-to-market drawdown can be larger.",
        ]
        if instrument.verification_scope == "PAPER_MODEL":
            warnings.append(
                "Contract sizing uses declared internal PAPER assumptions, not exchange-certified order economics."
            )
        if config.funding_mode == "EXPLICIT_ZERO":
            warnings.append("Funding explicitly assumed zero; not historical funding data.")
        if len(candles) < max(strategy.slow_period, strategy.lookback, strategy.rsi_period + 1) + 1:
            warnings.append("Dataset may be too short for configured indicator warmup.")
        return BacktestResult(
            dataset_version=config.dataset_version,
            dataset_hash=dataset_hash,
            reproducibility_hash=fingerprint,
            strategy=strategy,
            instrument=instrument,
            execution=config,
            currency=instrument.settlement_asset,
            starting_capital=config.initial_capital,
            ending_equity=cash,
            trades=trades,
            equity_curve=curve,
            rejected_signals=rejected,
            metrics=calculate_metrics(config.initial_capital, cash, trades, curve),
            assumptions=list(instrument.assumptions)
            + [
                "Closed bar signal executes no earlier than next bar open.",
                "Full fills at adverse spread/slippage plus exact tick rounding.",
                "Fees apply to entry and exit notional; positive funding rate means long pays, short receives.",
                "One owned position; opposite signal closes existing exposure without simultaneous reversal.",
            ],
            warnings=warnings,
        )
