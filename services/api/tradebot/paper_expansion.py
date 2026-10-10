"""Persisted, conservative PAPER execution policies and explicit funding.

Whole-candle volume caps are declared simulation assumptions. They are not
observed exchange liquidity. No function here submits any exchange request.
"""
import hashlib
import uuid
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select

from tradebot.config import get_settings
from tradebot.db import aware, utcnow
from tradebot.expansion_models import PaperFundingEvent, PaperMovement, PaperPendingEntry, PaperPositionState
from tradebot.models import Fill, Order, OrderEvent, Position, Signal, Strategy
from tradebot.repository import core_candles, notify, open_positions, risk_config, unrealized

D = Decimal
ZERO = D(0)
PENDING = ("OPEN", "PARTIALLY_FILLED")


def pending_entries(db, exclude=None):
    query = select(PaperPendingEntry).join(Order).where(Order.status.in_(PENDING))
    if exclude:
        query = query.where(Order.id != exclude)
    return list(db.scalars(query))


def reservations(db, exclude=None):
    entries = pending_entries(db, exclude)
    return (sum((row.reserved_margin for row in entries), ZERO),
            sum((row.reserved_risk for row in entries), ZERO))


def reserved_exposures(db, exclude=None):
    gross = net = ZERO
    for pending in pending_entries(db, exclude):
        order = db.get(Order, pending.order_id)
        notional = pending.reserved_margin*D(pending.config["max_leverage"])
        gross += notional
        net += notional*(1 if order.side == "BUY" else -1)
    return gross, net


def state_for(db, position, config=None):
    state = db.get(PaperPositionState, position.id)
    if state is None:
        state = PaperPositionState(position_id=position.id, initial_stop=position.stop_price,
                                   entry_quantity=position.quantity, favorable_price=position.entry_price,
                                   exited_quantity=ZERO, funding_paid=ZERO,
                                   strategy_version=config.version if config else 1,
                                   policy=config.model_dump(mode="json") if config else {})
        db.add(state)
        db.flush()
    return state


def queue_entry(db, *, signal, config, instrument, sizing, stop, target, candle):
    """Reserve the whole approved intent before returning to the worker loop."""
    from tradebot_core import Side
    from tradebot_core.execution import round_increment
    policy = config.execution
    side = Side(signal.side)
    oid = uuid.uuid4().hex
    limit = None
    if policy.entry_type == "LIMIT":
        limit = round_increment(candle.close * (1-side.sign*policy.limit_offset_fraction),
                                instrument.tick_size, up=side is Side.SELL)
        if (limit-stop)*side.sign <= 0:
            raise ValueError("Limit entry must remain on the protected side of the stop")
    order = Order(id=oid, client_order_id=oid, signal_id=signal.id, strategy_id=signal.strategy_id,
                  account_id=1, symbol=config.symbol, side=side.value, order_type=policy.entry_type,
                  status="OPEN", quantity=sizing.quantity, price=limit, reduce_only=False, mode="PAPER")
    db.add(order)
    db.flush()
    db.add(PaperPendingEntry(order_id=oid, signal_id=signal.id, strategy_id=signal.strategy_id,
                            config=config.model_dump(mode="json"), instrument=instrument.model_dump(mode="json"),
                            stop_price=stop, target_price=target, reserved_margin=sizing.margin,
                            reserved_risk=sizing.risk_amount, filled_quantity=ZERO,
                            last_candle_at=aware(candle.timestamp),
                            expires_at=aware(candle.timestamp)+timedelta(seconds=config.timeframe_seconds*(policy.pending_expiry_bars+1))))
    db.add(OrderEvent(order_id=oid, event_type="RISK_APPROVED", details={
        "mode": "PAPER", "simulated": True, "execution_policy": policy.model_dump(mode="json"),
        "reservation": {"margin": str(sizing.margin), "risk": str(sizing.risk_amount)},
        "volume_assumption": "Completed-bar participation cap; no queue/order-book inference"}))
    signal.status = "PENDING"
    notify(db, f"PAPER {policy.entry_type} entry queued for {config.symbol}; risk and margin reserved")


def cancel_pending(db, pending, reason="OPERATOR", status="CANCELED"):
    order = db.get(Order, pending.order_id)
    if order.status not in PENDING:
        return order
    order.status = status
    pending.reserved_margin = pending.reserved_risk = ZERO
    signal = db.get(Signal, pending.signal_id)
    signal.status, signal.reason = ("PARTIALLY_FILLED" if pending.filled_quantity else status), reason
    db.add(OrderEvent(order_id=order.id, event_type=status, details={"reason": reason, "simulated": True}))
    return order


def cancel_all_entries(db, reason="EMERGENCY"):
    for pending in pending_entries(db):
        cancel_pending(db, pending, reason)


def _risk_for_fill(db, account, pending, instrument, fill, candle, healthy):
    from tradebot.paper import committed_position_risk, period_pnl
    from tradebot_core import PaperFillModel, RiskContext, RiskLimits, Side, evaluate_risk
    from tradebot_core.risk import PositionSize
    policy = pending.config
    limits = risk_config(db)
    positions = open_positions(db)
    other_margin, other_risk = reservations(db, pending.order_id)
    order = db.get(Order, pending.order_id)
    remaining_ratio = (order.quantity-pending.filled_quantity-fill.quantity)/(order.quantity-pending.filled_quantity)
    other_margin += pending.reserved_margin*remaining_ratio
    other_risk += pending.reserved_risk*remaining_ratio
    from tradebot.news import entry_allowed
    gate = entry_allowed(db, order.symbol)
    if not gate["allowed"]:
        raise ValueError("Event/news blackout at fill: " + ", ".join(item["kind"] for item in gate["reasons"]))
    from tradebot.research_api import evaluation_contexts
    from tradebot_core import StrategyConfig
    config = StrategyConfig.model_validate(pending.config)
    at = aware(candle.timestamp)+timedelta(seconds=candle.timeframe_seconds)
    if not config.filters.allows(at, evaluation_contexts(db, config, core_candles([candle]))[at]):
        raise ValueError("Required event/funding filter evidence does not permit this partial fill")
    equity = account.cash + sum((unrealized(position) for position in positions), ZERO)
    used = sum((position.margin for position in positions), ZERO) + other_margin
    fees, slip = D(get_settings().paper_fee_rate), D(get_settings().paper_slippage_bps)
    side = Side(db.get(Order, pending.order_id).side)
    model = PaperFillModel(fee_rate=fees, slippage_bps=slip)
    stop_exit = model.execution_price(instrument, side.opposite, pending.stop_price)
    estimated_fees = instrument.fee(fill.price, fill.quantity, fees)+instrument.fee(stop_exit, fill.quantity, fees)
    economic_risk = -instrument.pnl(fill.price, stop_exit, fill.quantity, side)+estimated_fees
    if equity <= 0 or economic_risk <= 0:
        raise ValueError("Nonpositive account equity or invalid entry/stop economics")
    owned = next((position for position in positions if position.id == db.get(Order, pending.order_id).position_id), None)
    if any(position.symbol == instrument.symbol and position is not owned for position in positions):
        raise ValueError("Instrument already owned by a different position")
    if owned and committed_position_risk(owned)+economic_risk > equity*D(policy["risk_fraction"]):
        raise ValueError("Cumulative partial fill exceeds strategy trade risk")
    size = PositionSize(quantity=fill.quantity, entry_price=fill.price, stop_price=pending.stop_price,
                        notional=instrument.notional(fill.price, fill.quantity),
                        margin=instrument.margin(fill.price, fill.quantity, D(policy["max_leverage"])),
                        risk_amount=economic_risk, estimated_fees=estimated_fees)
    if config.execution.maintenance_margin_rate is not None:
        maintenance = size.notional*(config.execution.maintenance_margin_rate+config.execution.liquidation_fee_rate)
        if size.margin <= maintenance:
            raise ValueError("Declared isolated margin does not cover maintenance and liquidation fees at entry")
    day, week, day_pnl, week_pnl = period_pnl(db, account, equity)
    from tradebot.strategy_risk import strategy_liquidation_reasons, strategy_risk_reasons
    strategy_reasons = strategy_risk_reasons(db, pending.strategy_id, config, account, equity=equity, own_position=owned)
    strategy_reasons += strategy_liquidation_reasons(db, config, instrument=instrument, side=side, size=size, own_position=owned)
    if strategy_reasons:
        raise ValueError(", ".join(strategy_reasons))
    today = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    if config.max_trades_per_day is not None:
        trades = list(db.scalars(select(Position).where(Position.strategy_id == pending.strategy_id,
                                                       Position.opened_at >= today)))
        if len([position for position in trades if position is not owned]) >= config.max_trades_per_day:
            raise ValueError("Strategy daily trade limit reached before pending fill")
    if config.max_daily_loss_fraction is not None:
        fills = list(db.scalars(select(Fill).join(Order).where(Order.strategy_id == pending.strategy_id,
                                                              Fill.created_at >= today)))
        movements = list(db.scalars(select(PaperMovement).join(Position).where(Position.strategy_id == pending.strategy_id,
                        PaperMovement.kind != "RESET", PaperMovement.created_at >= today)))
        strategy_pnl = sum((item.realized_pnl-item.fee for item in fills), ZERO)+sum((item.amount for item in movements), ZERO)
        strategy_pnl += sum((unrealized(position) for position in positions if position.strategy_id == pending.strategy_id), ZERO)
        baseline = day*(config.capital_allocation_fraction or D(1))
        if strategy_pnl <= -baseline*config.max_daily_loss_fraction:
            raise ValueError("Strategy daily loss limit reached before pending fill")
    if config.capital_allocation_fraction is not None:
        owned_margin = sum((position.margin for position in positions if position.strategy_id == pending.strategy_id), ZERO)
        owned_margin += sum((item.reserved_margin for item in pending_entries(db, pending.order_id)
                            if item.strategy_id == pending.strategy_id), ZERO)
        owned_margin += pending.reserved_margin*remaining_ratio+size.margin
        if owned_margin > equity*config.capital_allocation_fraction:
            raise ValueError("Strategy capital allocation exceeded by pending fill and remaining reservation")
    exposure = sum((position.mark_price*position.quantity*position.contract_size*position.conversion_rate for position in positions), ZERO)
    net = sum((position.mark_price*position.quantity*position.contract_size*position.conversion_rate*(1 if position.side == "BUY" else -1) for position in positions), ZERO)
    reserved_gross, reserved_net = reserved_exposures(db, pending.order_id)
    own_remaining = pending.reserved_margin*remaining_ratio*D(policy["max_leverage"])
    exposure += reserved_gross+own_remaining
    net += reserved_net+own_remaining*side.sign
    from tradebot.repository import correlated_exposure
    from tradebot.clock import clock_healthy
    context = RiskContext(equity=equity, available_margin=max(ZERO, equity-used), margin_used=used,
                          gross_exposure=exposure, net_exposure=net, reserved_risk=other_risk,
                          correlated_exposure=correlated_exposure(db, order.symbol, limits.correlation_groups, {pending.order_id: remaining_ratio}),
                          open_position_risk=sum((committed_position_risk(position) for position in positions), ZERO),
                          daily_pnl=day_pnl, weekly_pnl=week_pnl, day_start_equity=day, week_start_equity=week,
                          peak_equity=account.high_water_equity, open_positions=len(positions)-(1 if owned else 0),
                          pending_orders=len(pending_entries(db, pending.order_id)), now=utcnow(),
                          market_timestamp=aware(candle.timestamp)+timedelta(seconds=candle.timeframe_seconds),
                          account_reconciled=not account.reconciliation_required,
                          instrument_metadata_fresh=(utcnow()-instrument.metadata_timestamp).total_seconds()<86400,
                          data_healthy=healthy, exchange_healthy=healthy, worker_healthy=healthy,
                          clock_healthy=clock_healthy(db), database_healthy=True,
                          protective_orders_verified=all(position.protection_verified for position in positions),
                          has_unknown_orders=bool(db.scalar(select(Order).where(Order.status == "UNKNOWN").limit(1))),
                          emergency_stop=account.emergency_stop or not limits.entries_enabled)
    account_limits = RiskLimits(max_risk_fraction=min(limits.max_risk_per_trade, D(policy["risk_fraction"])),
                                max_risk_per_trade=equity*limits.max_risk_per_trade,
                                max_committed_risk_fraction=limits.max_total_risk,
                                max_daily_loss_fraction=limits.max_daily_loss, max_drawdown_fraction=limits.max_drawdown,
                                max_weekly_loss_fraction=limits.max_weekly_loss,
                                max_leverage=limits.max_leverage, max_open_positions=limits.max_open_positions,
                                market_max_age_seconds=limits.stale_after_seconds, max_margin_fraction=limits.max_margin_fraction,
                                max_pending_orders=limits.max_pending_orders,
                                max_gross_exposure_multiple=limits.max_gross_exposure_multiple,
                                max_net_exposure_multiple=limits.max_net_exposure_multiple,
                                max_correlated_exposure_multiple=limits.max_correlated_exposure_multiple,
                                max_spread_bps=limits.max_spread_bps, max_slippage_bps=limits.max_slippage_bps)
    decision = evaluate_risk(limits=account_limits, context=context, instrument=instrument, side=side,
                             size=size, target_price=pending.target_price, leverage=D(policy["max_leverage"]),
                             fee_rate=fees, slippage_bps=slip)
    if not decision.approved:
        raise ValueError(", ".join(decision.reasons))


def process_pending(db, history, *, executor_healthy=False):
    from tradebot.paper import _order, close_position, lock_account, snapshot
    from tradebot_core import Instrument, PaperFillModel, Side, StrategyConfig
    from tradebot_core.simulation import limit_fill, volume_capacity
    account = lock_account(db)
    for pending in pending_entries(db):
        order = db.get(Order, pending.order_id)
        strategy = db.get(Strategy, pending.strategy_id)
        config = StrategyConfig.model_validate(pending.config)
        if (account.emergency_stop or not risk_config(db).entries_enabled or not strategy
                or strategy.status != "RUNNING" or strategy.approved_version != config.version):
            cancel_pending(db, pending, "Entries disabled, strategy paused, or version approval changed")
            continue
        bars = history.get(pending.strategy_id, [])
        after = [bar for bar in bars if aware(bar.timestamp) > aware(pending.last_candle_at)]
        if not after:
            continue
        # Do not invent exposure during downtime using a candle that is stale
        # when execution resumes. Existing position protection still replays.
        if len(after) > 1:
            cancel_pending(db, pending, "Worker downtime: historical entry opportunities are not replayed")
            continue
        candle = after[0]
        if aware(candle.timestamp) >= aware(pending.expires_at):
            cancel_pending(db, pending, "Declared pending lifetime elapsed", "EXPIRED")
            continue
        expected = aware(pending.last_candle_at)+timedelta(seconds=config.timeframe_seconds)
        if aware(candle.timestamp) != expected:
            cancel_pending(db, pending, "Missing entry candle; continuity uncertain")
            continue
        pending.last_candle_at = aware(candle.timestamp)
        instrument = Instrument.model_validate(pending.instrument)
        side = Side(order.side)
        quantity = order.quantity-pending.filled_quantity
        core_bar = core_candles([candle])[0]
        cap = volume_capacity(core_bar, instrument, config.execution)
        if cap is not None:
            quantity = min(quantity, cap)
        if quantity < instrument.min_quantity:
            continue
        model = PaperFillModel(fee_rate=D(get_settings().paper_fee_rate), slippage_bps=D(get_settings().paper_slippage_bps))
        fill = (limit_fill(instrument, side, quantity, order.price, core_bar, model)
                if order.order_type == "LIMIT" else model.fill(instrument, side, quantity, candle.close))
        if fill is None:
            continue
        try:
            with db.begin_nested():
                _risk_for_fill(db, account, pending, instrument, fill, candle, executor_healthy)
                position = db.get(Position, order.position_id) if order.position_id else None
                if position and position.status != "OPEN":
                    raise ValueError("Prior partial position is closed; remaining entry canceled")
                if position is None:
                    position = Position(id=uuid.uuid4().hex, account_id=1, strategy_id=pending.strategy_id,
                                        symbol=order.symbol, side=order.side, quantity=fill.quantity,
                                        contract_size=instrument.contract_size, conversion_rate=instrument.settlement_rate,
                                        instrument_snapshot=instrument.model_dump(mode="json"),
                                        entry_price=fill.price, mark_price=candle.close, stop_price=pending.stop_price,
                                        take_profit_price=pending.target_price,
                                        margin=instrument.margin(fill.price, fill.quantity, config.max_leverage),
                                        opened_at=aware(candle.timestamp)+timedelta(seconds=config.timeframe_seconds),
                                        last_protection_candle_at=aware(candle.timestamp), protection_verified=True)
                    db.add(position)
                    db.flush()
                    order.position_id = position.id
                    state = state_for(db, position, config)
                    state.entry_observations = {"reference_price": str(candle.close), "fill_price": str(fill.price),
                                                "model": fill.model, "volume_cap": str(cap) if cap is not None else None}
                    opposite = side.opposite.value
                    _order(db, position=position, side=opposite, kind="STOP_MARKET", status="OPEN", price=position.stop_price, reduce=True)
                    _order(db, position=position, side=opposite, kind="TAKE_PROFIT", status="OPEN", price=position.take_profit_price, reduce=True)
                else:
                    state = state_for(db, position, config)
                    if state.exited_quantity:
                        raise ValueError("Profit taking started; further partial entries canceled")
                    position.entry_price = (position.entry_price*position.quantity+fill.price*fill.quantity)/(position.quantity+fill.quantity)
                    position.quantity += fill.quantity
                    position.margin = instrument.margin(position.entry_price, position.quantity, config.max_leverage)
                    position.mark_price = candle.close
                    state.entry_quantity += fill.quantity
                    refresh_brackets(db, position)
                fid = hashlib.sha256(f"{order.id}:{aware(candle.timestamp).isoformat()}".encode()).hexdigest()
                execution_at = aware(candle.timestamp)+timedelta(seconds=candle.timeframe_seconds)
                db.add(Fill(id=fid, order_id=order.id, position_id=position.id, quantity=fill.quantity, price=fill.price, fee=fill.fee,
                            created_at=execution_at))
                account.cash -= fill.fee
                account.fees_paid += fill.fee
                original_remaining = order.quantity-pending.filled_quantity
                pending.filled_quantity += fill.quantity
                remaining_ratio = (order.quantity-pending.filled_quantity)/original_remaining
                pending.reserved_margin *= remaining_ratio
                pending.reserved_risk *= remaining_ratio
                order.status = "FILLED" if pending.filled_quantity == order.quantity else "PARTIALLY_FILLED"
                signal = db.get(Signal, pending.signal_id)
                signal.status = order.status
                db.add(OrderEvent(order_id=order.id, event_type=order.status, details={
                    "fill_id": fid, "price": str(fill.price), "quantity": str(fill.quantity), "simulated": True,
                    "model": fill.model, "execution_at": execution_at.isoformat(),
                    "observation": "completed-candle"}))
                db.flush()
                # Whole-bar capacity and limit penetration are observed at its
                # completion. The assigned fill time is its close, so prices
                # earlier in this bar cannot activate new position protection.
                if _liquidation_exit(db, position, candle, close_only=True):
                    pass
                elif (candle.close <= position.stop_price if side is Side.BUY else candle.close >= position.stop_price):
                    close_position(db, position, candle.close, "ENTRY_CLOSE_STOP", execution_at=execution_at)
                elif config.exit_rules.targets:
                    _target_exits(db, position, candle, config, state_for(db, position), close_only=True)
                elif (candle.close >= position.take_profit_price if side is Side.BUY else candle.close <= position.take_profit_price):
                    close_position(db, position, position.take_profit_price, "ENTRY_CLOSE_TARGET", execution_at=execution_at)
                snapshot(db, account)
        except (ValueError, ArithmeticError) as error:
            cancel_pending(db, pending, str(error), "REJECTED")
            notify(db, f"PAPER pending entry rejected: {error}", "WARNING")


def refresh_brackets(db, position):
    for order in db.scalars(select(Order).where(Order.position_id == position.id, Order.reduce_only.is_(True), Order.status == "OPEN")):
        order.quantity = position.quantity
        if order.order_type == "STOP_MARKET":
            order.price = position.stop_price
        elif order.order_type == "TAKE_PROFIT":
            order.price = position.take_profit_price


def close_partial(db, position, quantity, reference, reason, *, execution_at=None):
    from tradebot.paper import _order, close_position, lock_account, snapshot
    from tradebot_core import Instrument, PaperFillModel, Side
    account = lock_account(db)
    if position.status != "OPEN":
        return position
    if quantity >= position.quantity:
        return close_position(db, position, reference, reason, execution_at=execution_at)
    instrument = Instrument.model_validate(position.instrument_snapshot)
    instrument.validate_quantity(quantity)
    apply_funding_through(db, position, execution_at or utcnow())
    state = state_for(db, position)
    remaining_funding = _isolated_funding(state, position)
    side = Side(position.side).opposite
    fill = PaperFillModel(fee_rate=D(get_settings().paper_fee_rate), slippage_bps=D(get_settings().paper_slippage_bps)).fill(instrument, side, quantity, reference)
    order = _order(db, position=position, side=side.value, kind="MARKET", status="FILLED", price=fill.price, reduce=True)
    order.quantity = quantity
    pnl = instrument.pnl(position.entry_price, fill.price, quantity, side.opposite)
    db.add(Fill(id=hashlib.sha256(order.id.encode()).hexdigest(), order_id=order.id, position_id=position.id,
                quantity=quantity, price=fill.price, fee=fill.fee, realized_pnl=pnl,
                created_at=execution_at or utcnow()))
    state.entry_observations = {**state.entry_observations,
                               "isolated_funding_paid": str(remaining_funding*(position.quantity-quantity)/position.quantity)}
    position.margin *= (position.quantity-quantity)/position.quantity
    position.quantity -= quantity
    state.exited_quantity += quantity
    position.realized_pnl += pnl
    account.cash += pnl-fill.fee
    account.realized_pnl += pnl
    account.fees_paid += fill.fee
    refresh_brackets(db, position)
    for pending in pending_entries(db):
        if db.get(Order, pending.order_id).position_id == position.id:
            cancel_pending(db, pending, "Partial exit releases remaining entry")
    db.flush()
    snapshot(db, account)
    notify(db, f"PAPER {position.symbol} partial exit {quantity} ({reason})")
    return position


def apply_funding_through(db, position, through):
    """Use only explicit attributed events; no guessed rates or missing fills."""
    from tradebot.paper import lock_account
    from tradebot_core import Instrument, Side
    if position.status != "OPEN":
        return
    account = lock_account(db)
    state = state_for(db, position)
    instrument = Instrument.model_validate(position.instrument_snapshot)
    events = db.scalars(select(PaperFundingEvent).where(PaperFundingEvent.symbol == position.symbol,
                       PaperFundingEvent.timestamp >= position.opened_at, PaperFundingEvent.timestamp <= through)
                       .order_by(PaperFundingEvent.timestamp))
    for event in events:
        identifier = hashlib.sha256(f"funding:{position.id}:{event.id}".encode()).hexdigest()
        if db.get(PaperMovement, identifier):
            continue
        paid = instrument.notional(event.mark_price, position.quantity)*event.rate*Side(position.side).sign
        remaining_funding = _isolated_funding(state, position)
        db.add(PaperMovement(id=identifier, account_id=1, position_id=position.id, kind="FUNDING", amount=-paid,
                            details={"event_id": event.id, "source": event.source, "rate": str(event.rate),
                                     "mark_price": str(event.mark_price), "timestamp": aware(event.timestamp).isoformat()},
                            created_at=aware(event.timestamp)))
        account.cash -= paid
        state.funding_paid += paid
        state.entry_observations = {**state.entry_observations, "isolated_funding_paid": str(remaining_funding+paid)}
        db.flush()


def _isolated_funding(state, position):
    value = state.entry_observations.get("isolated_funding_paid")
    if value is not None:
        return D(value)
    # Older records did not allocate financing across partially exited lots.
    return state.funding_paid*position.quantity/state.entry_quantity if state.entry_quantity else ZERO


def pre_protective_exit(db, position, candle, *, partial_entry_bar=False):
    """Resolve gap liquidation before stops using only funding known at bar open."""
    return _liquidation_exit(db, position, candle, close_only=partial_entry_bar)


def _target_exits(db, position, candle, config, state, *, close_only=False):
    from tradebot_core import Instrument, Side
    from tradebot_core.execution import round_increment
    side = Side(position.side)
    instrument = Instrument.model_validate(position.instrument_snapshot)
    initial_risk = abs(position.entry_price-state.initial_stop)
    completed = list(state.completed_targets)
    for index, leg in enumerate(config.exit_rules.targets):
        if index in completed:
            continue
        price = round_increment(position.entry_price+side.sign*initial_risk*leg.reward_multiple,
                                instrument.tick_size, up=side is Side.SELL)
        observed = candle.close if close_only else (candle.high if side is Side.BUY else candle.low)
        if (observed-price)*side.sign < 0:
            break
        quantity = (position.quantity if index == len(config.exit_rules.targets)-1 else
                    round_increment(state.entry_quantity*leg.fraction, instrument.quantity_step))
        if quantity < instrument.min_quantity:
            continue
        close_partial(db, position, min(position.quantity, quantity), price, f"TARGET_LEG_{index+1}",
                      execution_at=aware(candle.timestamp)+timedelta(seconds=candle.timeframe_seconds))
        completed.append(index)
        state.completed_targets = completed
        if position.status != "OPEN":
            return True
    return False


def _liquidation_exit(db, position, candle, *, close_only=False):
    from tradebot.paper import close_position, lock_account, snapshot
    from tradebot_core import Instrument, Side, StrategyConfig
    from tradebot_core.simulation import isolated_liquidation_price
    if position.status != "OPEN":
        return False
    state = state_for(db, position)
    if not state.policy:
        return False
    config = StrategyConfig.model_validate(state.policy)
    if not config.execution or config.execution.maintenance_margin_rate is None:
        return False
    side = Side(position.side)
    instrument = Instrument.model_validate(position.instrument_snapshot)
    effective_at = aware(candle.timestamp)+timedelta(seconds=candle.timeframe_seconds) if close_only else aware(candle.timestamp)
    apply_funding_through(db, position, effective_at)
    liquidation = isolated_liquidation_price(instrument=instrument, side=side, entry_price=position.entry_price,
                     quantity=position.quantity, isolated_margin=position.margin, funding_paid=_isolated_funding(state, position),
                     maintenance_margin_rate=config.execution.maintenance_margin_rate,
                     liquidation_fee_rate=config.execution.liquidation_fee_rate)
    if liquidation is None:
        return False
    observed = candle.close if close_only else (candle.low if side is Side.BUY else candle.high)
    hit = observed <= liquidation if side is Side.BUY else observed >= liquidation
    if not hit:
        return False
    gap = candle.close if close_only else candle.open
    gap_crossed = gap <= liquidation if side is Side.BUY else gap >= liquidation
    stop_hit = observed <= position.stop_price if side is Side.BUY else observed >= position.stop_price
    liquidation_first = (liquidation >= position.stop_price if side is Side.BUY else liquidation <= position.stop_price)
    if not close_only and not gap_crossed and stop_hit and not liquidation_first:
        return False
    reference = gap if gap_crossed else liquidation
    quantity = position.quantity
    close_position(db, position, reference, "SIMULATED_ISOLATED_LIQUIDATION", execution_at=effective_at)
    fee = instrument.notional(position.mark_price, quantity)*config.execution.liquidation_fee_rate
    if fee:
        account = lock_account(db)
        identifier = hashlib.sha256(f"liquidation-fee:{position.id}".encode()).hexdigest()
        db.add(PaperMovement(id=identifier, account_id=1, position_id=position.id,
                            kind="LIQUIDATION_FEE", amount=-fee, created_at=effective_at,
                            details={"model": "DECLARED_LINEAR_ISOLATED", "execution_at": effective_at.isoformat()}))
        account.cash -= fee
        db.flush()
        snapshot(db, account)
    return True


def advanced_exit(db, position, candle, bars, *, partial_entry_bar=False):
    """Called after legacy stop handling; completed data updates next-bar stops."""
    from tradebot.paper import close_position
    from tradebot_core import Instrument, PaperFillModel, Side, StrategyConfig, StrategyEvaluator
    from tradebot_core.execution import round_increment
    from tradebot_core.indicators import atr, ema
    from tradebot_core.simulation import break_even_reference_price, next_protective_stop
    state = state_for(db, position)
    if not state.policy:
        return False
    config = StrategyConfig.model_validate(state.policy)
    rules, side = config.exit_rules, Side(position.side)
    instrument = Instrument.model_validate(position.instrument_snapshot)
    available = [bar for bar in bars if aware(bar.timestamp) <= aware(candle.timestamp)]
    apply_funding_through(db, position, aware(candle.timestamp)+timedelta(seconds=candle.timeframe_seconds))
    # End-of-bar financing may change maintenance after the intrabar price path.
    # Only this close may trigger liquidation from that newly known financing.
    if _liquidation_exit(db, position, candle, close_only=True):
        return True
    if _target_exits(db, position, candle, config, state, close_only=partial_entry_bar):
        return True
    held = int((aware(candle.timestamp)+timedelta(seconds=candle.timeframe_seconds)-aware(position.opened_at)).total_seconds()/candle.timeframe_seconds)
    if rules.max_holding_bars and held >= rules.max_holding_bars:
        close_position(db, position, candle.close, "TIME_EXIT", execution_at=aware(candle.timestamp)+timedelta(seconds=candle.timeframe_seconds))
        return True
    at = aware(candle.timestamp)+timedelta(seconds=candle.timeframe_seconds)
    if rules.opposite_signal_exit or config.long_exit_rules or config.short_exit_rules:
        from tradebot.repository import candle_rows
        from tradebot.research_api import evaluation_contexts
        higher = {dependency.timeframe_seconds: core_candles([bar for bar in candle_rows(db, position.symbol, dependency.timeframe_seconds, 5000)
                    if aware(bar.timestamp)+timedelta(seconds=bar.timeframe_seconds) <= at]) for dependency in config.higher_timeframes}
        context = evaluation_contexts(db, config, core_candles(available[-1:]))[at] if available else None
        evaluator = StrategyEvaluator(config, core_candles(available), higher_timeframes=higher, context=context)
        if available and evaluator.exit_at(len(available)-1, side) is True:
            close_position(db, position, candle.close, "INDICATOR_EXIT", execution_at=at)
            return True
        opposite = evaluator.signal_at(len(available)-1) if available and rules.opposite_signal_exit else None
        if opposite and opposite.side is side.opposite:
            close_position(db, position, candle.close, "OPPOSITE_SIGNAL", execution_at=at)
            return True
    if rules.trend_invalidation_exit and available:
        fast, slow = ema(core_candles(available), config.fast_period)[-1], ema(core_candles(available), config.slow_period)[-1]
        invalid = fast is not None and slow is not None and (fast <= slow if side is Side.BUY else fast >= slow)
        if invalid:
            close_position(db, position, candle.close, "TREND_INVALIDATION", execution_at=at)
            return True
    state.favorable_price = max(state.favorable_price, candle.close) if side is Side.BUY else min(state.favorable_price, candle.close)
    atr_value = atr(core_candles(available), rules.trailing_period)[-1] if available and (rules.trailing_method == "ATR" or rules.dynamic_target_atr_multiple) else None
    structure = None
    if rules.trailing_method == "STRUCTURE" and len(available) >= rules.trailing_period:
        recent = available[-rules.trailing_period:]
        structure = min(bar.low for bar in recent) if side is Side.BUY else max(bar.high for bar in recent)
    break_even = None
    if rules.break_even_include_costs and rules.break_even_trigger_r is not None:
        entry_fees = sum((fill.fee for fill in db.scalars(select(Fill).join(Order).where(
            Order.position_id == position.id, Order.reduce_only.is_(False)))), ZERO)
        break_even = break_even_reference_price(instrument=instrument, side=side, entry_price=position.entry_price,
                    quantity=state.entry_quantity, entry_fees=entry_fees, funding_paid=state.funding_paid,
                    model=PaperFillModel(fee_rate=D(get_settings().paper_fee_rate), slippage_bps=D(get_settings().paper_slippage_bps)))
    stop = next_protective_stop(side=side, entry_price=position.entry_price, initial_stop=state.initial_stop,
                               current_stop=position.stop_price, favorable_price=state.favorable_price,
                               candle=core_candles([candle])[0], instrument=instrument, rules=rules,
                               atr_value=atr_value, structure_level=structure, break_even_price=break_even)
    if stop != position.stop_price:
        position.stop_price = stop
        refresh_brackets(db, position)
        for order in db.scalars(select(Order).where(Order.position_id == position.id, Order.order_type == "STOP_MARKET", Order.status == "OPEN")):
            db.add(OrderEvent(order_id=order.id, event_type="PROTECTION_UPDATED", details={
                "stop_price": str(stop), "effective_after": (aware(candle.timestamp)+timedelta(seconds=candle.timeframe_seconds)).isoformat(),
                "simulated": True, "causal": "completed-close only"}))
    if rules.dynamic_target_atr_multiple is not None and atr_value is not None and not rules.targets:
        target = round_increment(position.entry_price+side.sign*atr_value*rules.dynamic_target_atr_multiple,
                                 instrument.tick_size, up=side is Side.SELL)
        if target > 0 and (target-candle.close)*side.sign > 0 and target != position.take_profit_price:
            position.take_profit_price = target
            refresh_brackets(db, position)
            for order in db.scalars(select(Order).where(Order.position_id == position.id, Order.order_type == "TAKE_PROFIT", Order.status == "OPEN")):
                db.add(OrderEvent(order_id=order.id, event_type="PROTECTION_UPDATED", details={
                    "take_profit_price": str(target), "effective_after": at.isoformat(), "simulated": True}))
    return False
