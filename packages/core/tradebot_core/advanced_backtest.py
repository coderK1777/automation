"""Extended event loop used by BacktestEngine for declared advanced policies."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from .arithmetic import fixed_decimal
from .backtest import (
    ENGINE_VERSION, BacktestConfig, BacktestResult, BacktestTrade, EquityPoint,
    RejectedSignal, _hash, calculate_metrics,
    strategy_risk_reasons,
)
from .context import EvaluationContext
from .execution import PaperFill, PaperFillModel, round_increment
from .indicators import atr, ema
from .models import Candle, Instrument, Side, Signal, validate_candles
from .risk import RiskContext, evaluate_risk, size_position
from .simulation import (
    AdvancedExecutionConfig, SimulatedOrderEvent, isolated_liquidation_price,
    break_even_reference_price, limit_fill, next_protective_stop, volume_capacity,
)
from .strategy import StrategyConfig, StrategyEvaluator

D = Decimal


@dataclass
class _Intent:
    signal: Signal
    quantity: Decimal
    created_index: int
    limit_price: Decimal | None
    filled: Decimal = D(0)


@dataclass
class _Exposure:
    signal: Signal
    entry_timestamp: datetime
    entry_index: int
    initial_stop: Decimal
    stop: Decimal
    target: Decimal
    favorable: Decimal
    entry_fills: list[PaperFill] = field(default_factory=list)
    exit_fills: list[PaperFill] = field(default_factory=list)
    funding: Decimal = D(0)
    realized: Decimal = D(0)
    next_target: int = 0

    @property
    def total_quantity(self) -> Decimal:
        return sum((fill.quantity for fill in self.entry_fills), D(0))

    @property
    def remaining(self) -> Decimal:
        return self.total_quantity - sum((fill.quantity for fill in self.exit_fills), D(0))

    @property
    def entry_price(self) -> Decimal:
        return sum((fill.price * fill.quantity for fill in self.entry_fills), D(0)) / self.total_quantity


@fixed_decimal
def run_advanced(
    candles: list[Candle], instrument: Instrument, strategy: StrategyConfig, config: BacktestConfig,
    *, higher_timeframes: dict[int, list[Candle]] | None = None,
    evaluation_contexts: dict[datetime, EvaluationContext] | None = None,
) -> BacktestResult:
    validate_candles(candles)
    if not 2 <= len(candles) <= config.max_candles:
        raise ValueError("backtest requires 2..max_candles contiguous closed bars")
    if candles[0].timeframe_seconds != strategy.timeframe_seconds or instrument.symbol != strategy.symbol:
        raise ValueError("backtest instrument/timeframe must match strategy")
    if not instrument.metadata_verified:
        raise ValueError("backtest needs explicit reviewed contract economics")
    if "BACKTEST" not in strategy.allowed_environments:
        raise ValueError("strategy does not permit BACKTEST execution")
    execution = config.execution_model or strategy.execution or AdvancedExecutionConfig()
    if execution.maintenance_margin_rate is not None and execution.maintenance_margin_rate >= instrument.initial_margin_rate:
        raise ValueError("declared maintenance margin must be below initial margin")
    evaluator = StrategyEvaluator(strategy, candles, higher_timeframes=higher_timeframes)
    ranges = atr(candles, strategy.exit_rules.trailing_period)
    sizing_ranges = atr(candles,strategy.sizing.volatility_period) if strategy.sizing.mode == "VOLATILITY" else []
    trend_fast = ema(candles,strategy.fast_period) if strategy.exit_rules.trend_invalidation_exit else []
    trend_slow = ema(candles,strategy.slow_period) if strategy.exit_rules.trend_invalidation_exit else []
    dataset_end = candles[-1].timestamp+timedelta(seconds=candles[-1].timeframe_seconds)
    if any(not candles[0].timestamp <= event.timestamp < dataset_end for event in config.funding_events):
        raise ValueError("funding events must occur inside the observed dataset time range")
    funding = {event.timestamp: event for event in config.funding_events}
    funding_by_bar = {}
    for event in config.funding_events:
        index = int((event.timestamp-candles[0].timestamp).total_seconds())//candles[0].timeframe_seconds
        funding_by_bar.setdefault(index,[]).append(event)
    model = PaperFillModel(fee_rate=config.fee_rate, spread_bps=config.spread_bps, slippage_bps=config.slippage_bps)
    cash = peak = day_equity = week_equity = config.initial_capital
    active_day, active_week = candles[0].timestamp.date(), candles[0].timestamp.isocalendar()[:2]
    exposure: _Exposure | None = None
    intent: _Intent | None = None
    pending_signal: Signal | None = None
    last_entry_index = -strategy.cooldown_bars - 1
    entries_today = 0
    trades: list[BacktestTrade] = []
    curve: list[EquityPoint] = []
    rejected: list[RejectedSignal] = []
    events: list[SimulatedOrderEvent] = []

    def emit(event: str, timestamp: datetime, order: _Intent, reason: str, price: Decimal | None = None) -> None:
        events.append(SimulatedOrderEvent(timestamp=timestamp, signal_id=order.signal.signal_id, event=event,
                                         requested_quantity=order.quantity, cumulative_filled_quantity=order.filled,
                                         reason=reason, price=price))

    def cancel(timestamp: datetime, reason: str) -> None:
        nonlocal intent
        if intent is not None:
            emit("CANCELED", timestamp, intent, reason)
            intent = None

    def close(quantity: Decimal, reference: Decimal, timestamp: datetime, reason: str) -> None:
        nonlocal cash, exposure
        assert exposure is not None
        # A staged exit freezes entry exposure; no later partial entry changes
        # the weighted basis of already realized quantities.
        cancel(timestamp, "PROTECTION_EXIT_CANCELS_ENTRY_REMAINDER")
        quantity = min(quantity, exposure.remaining)
        fill = model.fill(instrument, exposure.signal.side.opposite, quantity, reference)
        if reason.startswith("LIQUIDATION"):
            fill = fill.model_copy(update={"fee": fill.fee + instrument.fee(fill.price, quantity, execution.liquidation_fee_rate)})
        gross = instrument.pnl(exposure.entry_price, fill.price, quantity, exposure.signal.side)
        cash += gross - fill.fee
        exposure.realized += gross
        exposure.exit_fills.append(fill)
        if exposure.remaining == 0:
            fees = sum((item.fee for item in exposure.entry_fills + exposure.exit_fills), D(0))
            exit_price = sum((item.price * item.quantity for item in exposure.exit_fills), D(0)) / exposure.total_quantity
            trades.append(BacktestTrade(
                signal_id=exposure.signal.signal_id, side=exposure.signal.side, quantity=exposure.total_quantity,
                signal_timestamp=exposure.signal.timestamp, entry_timestamp=exposure.entry_timestamp,
                exit_timestamp=timestamp, entry_price=exposure.entry_price, exit_price=exit_price,
                stop_price=exposure.initial_stop, target_price=exposure.target, gross_pnl=exposure.realized,
                fees=fees, funding=exposure.funding, net_pnl=exposure.realized-fees-exposure.funding,
                exit_reason=reason, holding_seconds=int((timestamp-exposure.entry_timestamp).total_seconds()),
                entry_fill_count=len(exposure.entry_fills), exit_fill_count=len(exposure.exit_fills),
            ))
            exposure = None

    def liquidation_level() -> Decimal | None:
        if exposure is None or execution.maintenance_margin_rate is None:
            return None
        remaining_fraction = exposure.remaining / exposure.total_quantity
        return isolated_liquidation_price(
            instrument=instrument, side=exposure.signal.side, entry_price=exposure.entry_price,
            quantity=exposure.remaining,
            isolated_margin=instrument.margin(exposure.entry_price, exposure.remaining, strategy.max_leverage),
            funding_paid=exposure.funding * remaining_fraction,
            maintenance_margin_rate=execution.maintenance_margin_rate,
            liquidation_fee_rate=execution.liquidation_fee_rate,
        )

    def targets() -> list[tuple[Decimal, Decimal]]:
        assert exposure is not None
        if not strategy.exit_rules.targets:
            return [(exposure.target, exposure.remaining)]
        risk_distance = abs(exposure.entry_price-exposure.initial_stop)
        result = []
        assigned = D(0)
        for index, leg in enumerate(strategy.exit_rules.targets):
            quantity = exposure.total_quantity-assigned if index == len(strategy.exit_rules.targets)-1 else round_increment(
                exposure.total_quantity*leg.fraction, instrument.quantity_step
            )
            assigned += quantity
            price = round_increment(exposure.entry_price+exposure.signal.side.sign*risk_distance*leg.reward_multiple,
                                    instrument.tick_size, up=exposure.signal.side is Side.SELL)
            result.append((price, quantity))
        return result

    def protective(candle: Candle, timestamp: datetime, *, open_only: bool = False) -> None:
        if exposure is None:
            return
        side = exposure.signal.side
        low, high = (candle.open, candle.open) if open_only else (candle.low, candle.high)
        liq = liquidation_level()
        liq_hit = liq is not None and (low <= liq if side is Side.BUY else high >= liq)
        stop_hit = low <= exposure.stop if side is Side.BUY else high >= exposure.stop
        current_targets = targets()
        target_hit = any(high >= price if side is Side.BUY else low <= price for price, _ in current_targets[exposure.next_target:])
        # Gap through liquidation and stop cannot fill at an unavailable stop.
        # Within a bar the earlier loss-side level is the protective stop; if
        # liquidation is closer than the stop, liquidation takes precedence.
        liq_before_stop = liq_hit and (open_only or not stop_hit or (
            liq >= exposure.stop if side is Side.BUY else liq <= exposure.stop
        ))
        if liq_before_stop:
            close(exposure.remaining, candle.open if open_only else liq, timestamp,
                  "LIQUIDATION_GAP" if open_only else "LIQUIDATION")
        elif stop_hit:
            close(exposure.remaining, candle.open if open_only else exposure.stop, timestamp,
                  "STOP_GAP" if open_only else ("STOP_FIRST_AMBIGUOUS" if target_hit else "STOP"))
        elif target_hit:
            while exposure is not None and exposure.next_target < len(current_targets):
                price, quantity = current_targets[exposure.next_target]
                if not (high >= price if side is Side.BUY else low <= price):
                    break
                exposure.next_target += 1
                if quantity <= 0:
                    continue
                # Small staged legs below the declared minimum are deferred
                # until a later target or final exit instead of inventing lots.
                if quantity < instrument.min_quantity and quantity < exposure.remaining:
                    continue
                if exposure.next_target == len(current_targets):
                    quantity = exposure.remaining
                close(quantity, price, timestamp, "TARGET_GAP" if open_only else "TARGET")

    def size_for(signal: Signal, price: Decimal, timestamp: datetime):
        equity = cash + (instrument.pnl(exposure.entry_price, price, exposure.remaining, exposure.signal.side) if exposure else D(0))
        if equity <= 0:
            raise ValueError("NONPOSITIVE_EQUITY")
        strategy_reasons = strategy_risk_reasons(strategy,equity,peak,trades)
        if strategy_reasons:
            raise ValueError(";".join(strategy_reasons))
        limits = config.risk_limits
        if strategy.max_daily_loss_fraction is not None and equity-day_equity <= -day_equity*strategy.max_daily_loss_fraction:
            raise ValueError("STRATEGY_DAILY_LOSS_LIMIT")
        committed_risk = D(0)
        if exposure is not None:
            adverse_stop = model.execution_price(instrument,exposure.signal.side.opposite,exposure.stop)
            committed_risk = max(D(0),-instrument.pnl(exposure.entry_price,adverse_stop,exposure.remaining,exposure.signal.side)) + instrument.fee(adverse_stop,exposure.remaining,config.fee_rate) + sum((fill.fee for fill in exposure.entry_fills),D(0))
        remaining_trade_budget = max(D(0), min(limits.max_risk_per_trade,equity*limits.max_risk_fraction,
                                              equity*strategy.risk_fraction)-committed_risk)
        size = size_position(instrument=instrument, side=signal.side, entry_price=price, stop_price=signal.stop_price,
                             equity=equity, risk_fraction=strategy.risk_fraction, max_leverage=strategy.max_leverage,
                             fee_rate=config.fee_rate, spread_bps=config.spread_bps, slippage_bps=config.slippage_bps,
                             available_margin=max(D(0), equity*min(limits.max_margin_fraction,strategy.capital_allocation_fraction or D(1))-(instrument.margin(
                                 price, exposure.remaining, strategy.max_leverage) if exposure else D(0))),
                             max_notional=max(D(0), equity*limits.max_gross_exposure_multiple-(instrument.notional(
                                 price, exposure.remaining) if exposure else D(0))),
                             risk_budget=remaining_trade_budget,sizing=strategy.sizing,
                             volatility_atr=sizing_ranges[index-1] if sizing_ranges and index>0 else None)
        context = RiskContext(execution_environment="BACKTEST", equity=equity, available_margin=equity,
                              day_start_equity=max(day_equity,D(".00000001")), week_start_equity=max(week_equity,D(".00000001")),
                              peak_equity=peak,daily_pnl=equity-day_equity,weekly_pnl=equity-week_equity,
                              now=timestamp,market_timestamp=timestamp,account_reconciled=True,instrument_metadata_fresh=True,
                              data_healthy=True,exchange_healthy=True,worker_healthy=True,clock_healthy=True,database_healthy=True,
                              protective_orders_verified=True,has_unknown_orders=False)
        if exposure is not None:
            context = context.model_copy(update={
                "gross_exposure":instrument.notional(price,exposure.remaining),
                "net_exposure":instrument.notional(price,exposure.remaining)*exposure.signal.side.sign,
                "margin_used":instrument.margin(price,exposure.remaining,strategy.max_leverage),
                "open_position_risk":committed_risk,
                "correlated_exposure":instrument.notional(price,exposure.remaining),
            })
        target = signal.target_price
        if strategy.exit_rules.targets:
            reward = sum((leg.reward_multiple*leg.fraction for leg in strategy.exit_rules.targets),D(0))
            target = size.entry_price+signal.side.sign*abs(size.entry_price-size.stop_price)*reward
        decision = evaluate_risk(limits=limits,context=context,instrument=instrument,side=signal.side,size=size,
                                 target_price=target,leverage=strategy.max_leverage,fee_rate=config.fee_rate,
                                 spread_bps=config.spread_bps,slippage_bps=config.slippage_bps)
        if not decision.approved:
            raise ValueError(";".join(decision.reasons))
        if strategy.min_liquidation_distance_fraction is not None:
            if execution.maintenance_margin_rate is None:
                raise ValueError("UNVERIFIED_LIQUIDATION_DISTANCE")
            liquidation = isolated_liquidation_price(instrument=instrument,side=signal.side,entry_price=size.entry_price,
                                                     quantity=size.quantity,isolated_margin=size.margin,funding_paid=D(0),
                                                     maintenance_margin_rate=execution.maintenance_margin_rate,
                                                     liquidation_fee_rate=execution.liquidation_fee_rate)
            if liquidation is not None and abs(liquidation-size.entry_price)/size.entry_price < strategy.min_liquidation_distance_fraction:
                raise ValueError("LIQUIDATION_DISTANCE_LIMIT")
        return size

    for index, candle in enumerate(candles):
        bar_close = candle.timestamp+timedelta(seconds=candle.timeframe_seconds)
        opening_equity = cash+(instrument.pnl(exposure.entry_price,candle.open,exposure.remaining,exposure.signal.side) if exposure else D(0))
        if candle.timestamp.date() != active_day:
            day_equity,active_day = opening_equity,candle.timestamp.date()
            entries_today = 0
        if candle.timestamp.isocalendar()[:2] != active_week:
            week_equity,active_week = opening_equity,candle.timestamp.isocalendar()[:2]
        had_exposure = exposure is not None
        if exposure is not None and candle.timestamp in funding:
            event = funding[candle.timestamp]
            payment = instrument.notional(event.mark_price,exposure.remaining)*event.rate*exposure.signal.side.sign
            exposure.funding += payment
            cash -= payment
        protective(candle,candle.timestamp,open_only=True)
        if pending_signal is not None and exposure is not None and pending_signal.side != exposure.signal.side:
            cancel(candle.timestamp,"OPPOSITE_SIGNAL")
            if strategy.exit_rules.opposite_signal_exit:
                close(exposure.remaining,candle.open,candle.timestamp,"OPPOSITE_SIGNAL")
        if intent is not None and pending_signal is not None and pending_signal.side != intent.signal.side:
            cancel(candle.timestamp,"OPPOSITE_SIGNAL")
        if exposure is not None and strategy.exit_rules.max_holding_bars is not None and index-exposure.entry_index >= strategy.exit_rules.max_holding_bars:
            close(exposure.remaining,candle.open,candle.timestamp,"TIME_EXIT")
        if exposure is not None and index > 0:
            if evaluator.exit_at(index-1,exposure.signal.side) is True:
                close(exposure.remaining,candle.open,candle.timestamp,"INDICATOR_EXIT")
            elif strategy.exit_rules.trend_invalidation_exit and trend_fast[index-1] is not None and trend_slow[index-1] is not None and (
                trend_fast[index-1] <= trend_slow[index-1] if exposure.signal.side is Side.BUY else trend_fast[index-1] >= trend_slow[index-1]
            ):
                close(exposure.remaining,candle.open,candle.timestamp,"TREND_INVALIDATION")
        if intent is None and exposure is None and not had_exposure and pending_signal is not None and (
            config.trade_start_timestamp is None or candle.timestamp >= config.trade_start_timestamp
        ):
            if strategy.max_trades_per_day is not None and entries_today >= strategy.max_trades_per_day:
                rejected.append(RejectedSignal(signal_id=pending_signal.signal_id,timestamp=candle.timestamp,reasons=["STRATEGY_DAILY_TRADE_LIMIT"]))
            elif index-last_entry_index <= strategy.cooldown_bars:
                rejected.append(RejectedSignal(signal_id=pending_signal.signal_id,timestamp=candle.timestamp,reasons=["COOLDOWN_ACTIVE"]))
            else:
                try:
                    price = candle.open if execution.entry_type == "MARKET" else round_increment(
                        pending_signal.reference_price*(1-pending_signal.side.sign*execution.limit_offset_fraction),
                        instrument.tick_size,up=pending_signal.side is Side.SELL)
                    size = size_for(pending_signal,price,candle.timestamp)
                    intent = _Intent(pending_signal,size.quantity,index,None if execution.entry_type == "MARKET" else price)
                    emit("CREATED",candle.timestamp,intent,"PRIOR_CLOSED_BAR_SIGNAL",price)
                except ValueError as error:
                    rejected.append(RejectedSignal(signal_id=pending_signal.signal_id,timestamp=candle.timestamp,reasons=[str(error)]))
        # Full market fills need no end-of-bar information. All limit or
        # volume-capped fills are timestamped at close; their protection starts
        # from close onward, never replaying earlier intrabar prices.
        end_bar_fill = execution.entry_type == "LIMIT" or execution.volume_participation_fraction is not None
        if not end_bar_fill and intent is not None:
            fill = model.fill(instrument,intent.signal.side,intent.quantity-intent.filled,candle.open)
            intent.filled += fill.quantity
            stop = round_increment(intent.signal.stop_price,instrument.tick_size,up=intent.signal.side is Side.SELL)
            target = round_increment(intent.signal.target_price,instrument.tick_size,up=intent.signal.side is Side.SELL)
            exposure = _Exposure(intent.signal,candle.timestamp,index,stop,stop,target,fill.price,[fill])
            cash -= fill.fee
            last_entry_index = index
            entries_today += 1
            emit("FILLED",candle.timestamp,intent,"FULL_MARKET_ADVERSE_COSTS",fill.price)
            intent = None
        # Intrabar funding timing is unknown relative to an OHLC stop. Charge
        # payable funding on existing/opening fills conservatively. Credits
        # require the position to survive the entire bar and are applied after
        # protection. A close-timestamp fill does not receive retroactive events.
        intrabar_events = [event for event in funding_by_bar.get(index,[]) if event.timestamp > candle.timestamp]
        credit_events = []
        if exposure is not None:
            for event in intrabar_events:
                payment = instrument.notional(event.mark_price,exposure.remaining)*event.rate*exposure.signal.side.sign
                if payment >= 0:
                    exposure.funding += payment
                    cash -= payment
                else:
                    credit_events.append((event,payment,exposure.remaining))
        protective(candle,bar_close)
        if exposure is not None:
            for event,payment,previous_quantity in credit_events:
                conservative_credit = payment*exposure.remaining/previous_quantity
                exposure.funding += conservative_credit
                cash -= conservative_credit
        if end_bar_fill and intent is not None:
            capacity = volume_capacity(candle,instrument,execution)
            quantity = min(intent.quantity-intent.filled,capacity) if capacity is not None else intent.quantity-intent.filled
            if quantity >= instrument.min_quantity:
                fill = limit_fill(instrument,intent.signal.side,quantity,intent.limit_price,candle,model) if intent.limit_price is not None else model.fill(instrument,intent.signal.side,quantity,candle.close)
                if fill is not None:
                    # Re-size each completed-bar tranche so an aged order never
                    # turns price movement into a central risk bypass.
                    try:
                        allowed = size_for(intent.signal,fill.price,bar_close)
                        if fill.quantity > allowed.quantity:
                            raise ValueError("PARTIAL_FILL_RISK_REVALIDATION")
                        if exposure is None:
                            stop = round_increment(intent.signal.stop_price,instrument.tick_size,up=intent.signal.side is Side.SELL)
                            target = round_increment(intent.signal.target_price,instrument.tick_size,up=intent.signal.side is Side.SELL)
                            exposure = _Exposure(intent.signal,bar_close,index,stop,stop,target,fill.price)
                            last_entry_index = index
                            entries_today += 1
                        exposure.entry_fills.append(fill)
                        cash -= fill.fee
                        intent.filled += fill.quantity
                        emit("FILLED" if intent.filled == intent.quantity else "PARTIALLY_FILLED",bar_close,intent,
                             "END_BAR_DECLARED_VOLUME_CAPACITY" if capacity is not None else "END_BAR_LIMIT_PENETRATION",fill.price)
                        if intent.filled == intent.quantity:
                            intent = None
                        # At-close entry may be already beyond a protective
                        # boundary. Only the close is causally available here.
                        close_candle = candle.model_copy(update={"open":candle.close,"high":candle.close,"low":candle.close})
                        protective(close_candle,bar_close,open_only=True)
                    except ValueError as error:
                        emit("REJECTED",bar_close,intent,str(error),fill.price)
                        intent = None
            if intent is not None and index-intent.created_index+1 >= execution.pending_expiry_bars:
                emit("EXPIRED",bar_close,intent,"DECLARED_ORDER_EXPIRY")
                intent = None
        if exposure is not None:
            # Only bars after the end-bar fill can update its high-water mark.
            favorable = candle.high if exposure.signal.side is Side.BUY else candle.low
            if exposure.entry_timestamp < bar_close:
                exposure.favorable = max(exposure.favorable,favorable) if exposure.signal.side is Side.BUY else min(exposure.favorable,favorable)
                period = strategy.exit_rules.trailing_period
                window = candles[max(0,index-period+1):index+1]
                structure = min(bar.low for bar in window) if exposure.signal.side is Side.BUY else max(bar.high for bar in window)
                next_stop = next_protective_stop(side=exposure.signal.side,entry_price=exposure.entry_price,
                                                initial_stop=exposure.initial_stop,current_stop=exposure.stop,
                                                favorable_price=exposure.favorable,candle=candle,instrument=instrument,
                                                rules=strategy.exit_rules,atr_value=ranges[index],structure_level=structure,
                                                break_even_price=break_even_reference_price(
                                                    instrument=instrument,side=exposure.signal.side,entry_price=exposure.entry_price,
                                                    quantity=exposure.total_quantity,entry_fees=sum((fill.fee for fill in exposure.entry_fills),D(0)),
                                                    funding_paid=exposure.funding,model=model,
                                                ) if strategy.exit_rules.break_even_include_costs and strategy.exit_rules.break_even_trigger_r is not None else None)
                if next_stop != exposure.stop:
                    exposure.stop = next_stop
                    events.append(SimulatedOrderEvent(timestamp=bar_close,signal_id=exposure.signal.signal_id,event="PROTECTION_UPDATED",
                                                     requested_quantity=exposure.remaining,cumulative_filled_quantity=D(0),price=next_stop,
                                                     reason="COMPLETED_BAR_STOP_EFFECTIVE_NEXT_BAR"))
                if strategy.exit_rules.dynamic_target_atr_multiple is not None and ranges[index] is not None and not strategy.exit_rules.targets:
                    new_target = exposure.entry_price+exposure.signal.side.sign*ranges[index]*strategy.exit_rules.dynamic_target_atr_multiple
                    if new_target > 0 and (new_target-candle.close)*exposure.signal.side.sign > 0:
                        exposure.target = round_increment(new_target,instrument.tick_size,up=exposure.signal.side is Side.SELL)
        if index == len(candles)-1:
            cancel(bar_close,"END_OF_DATA")
            if exposure is not None:
                close(exposure.remaining,candle.close,bar_close,"END_OF_DATA")
        unrealized = instrument.pnl(exposure.entry_price,candle.close,exposure.remaining,exposure.signal.side) if exposure else D(0)
        equity = cash+unrealized
        peak = max(peak,equity)
        curve.append(EquityPoint(timestamp=bar_close,equity=equity,cash=cash,unrealized_pnl=unrealized,
                                 gross_exposure=instrument.notional(candle.close,exposure.remaining) if exposure else D(0),
                                 margin_used=instrument.margin(candle.close,exposure.remaining,strategy.max_leverage) if exposure else D(0)))
        pending_signal = evaluator.signal_at(index,context=(evaluation_contexts or {}).get(bar_close)) if index < len(candles)-1 else None
    dataset_hash = _hash([candle.model_dump(mode="json") for candle in candles])
    fingerprint = _hash({"engine_version":ENGINE_VERSION,"dataset_hash":dataset_hash,"instrument":instrument.model_dump(mode="json"),
                         "strategy":strategy.model_dump(mode="json"),"config":config.model_dump(mode="json"),
                         "higher_timeframes":{str(seconds):[bar.model_dump(mode="json") for bar in bars] for seconds,bars in (higher_timeframes or {}).items()},
                         "evaluation_contexts":{stamp.isoformat():value.model_dump(mode="json") for stamp,value in (evaluation_contexts or {}).items()}})
    warnings = ["Declared OHLC simulation, no measured order-book queue, latency or Delta margin certification.",
                "Limit and volume-capped fills are end-bar approximations; whole-bar volume never authorizes an opening fill.",
                "Candle-close equity can understate intrabar drawdown; holding and partial-fill timestamps are approximate.",
                "Fixed reviewed contract/conversion snapshot; historical changes are not reconstructed."]
    if any(event.timestamp not in {bar.timestamp for bar in candles} for event in config.funding_events):
        warnings.append("Intrabar funding/protection sequence is unknowable: payable charges precede protection; credits require bar survival.")
    if config.funding_mode == "EXPLICIT_ZERO":
        warnings.append("Funding explicitly assumed zero; not historical funding data.")
    if execution.maintenance_margin_rate is None:
        warnings.append("Liquidation disabled: no explicit maintenance-margin model was supplied.")
    else:
        warnings.append("Liquidation uses a declared isolated linear margin model, not exchange cross-margin rules.")
    if instrument.verification_scope == "PAPER_MODEL":
        warnings.append("Sizing uses declared internal PAPER assumptions, not exchange-certified economics.")
    return BacktestResult(dataset_version=config.dataset_version,dataset_hash=dataset_hash,reproducibility_hash=fingerprint,
                          strategy=strategy,instrument=instrument,execution=config,currency=instrument.settlement_asset,
                          starting_capital=config.initial_capital,ending_equity=cash,trades=trades,equity_curve=curve,
                          rejected_signals=rejected,metrics=calculate_metrics(config.initial_capital,cash,trades,curve),
                          assumptions=list(instrument.assumptions)+[
                              "Signals use previous completed bars; adverse spread, slippage, tick rounding and fees apply.",
                              "Stops precede profitable targets when intrabar order is unknowable; gap stops use worse open.",
                              "Whole-bar declared volume participation is a capacity approximation, not measured executable liquidity.",
                              "Trailing/break-even updates from completed bars become effective next bar; stops never loosen.",
                              "Target legs round down; last target closes remainder; partial exit cancels remaining entry.",
                              "One owned position; opposite signal exits without same-bar reversal; exact cash/fee/funding ledger.",
                          ],warnings=warnings,order_events=events)
