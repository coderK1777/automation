"""Durable PAPER-only executor. There is no exchange order placement path."""
import hashlib
import uuid
from datetime import timedelta
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR

from sqlalchemy import select
from sqlalchemy.orm import Session

from tradebot.config import get_settings
from tradebot.db import aware, utcnow
from tradebot.models import Candle, EquitySnapshot, ExecutorLease, Fill, Instrument, Order, OrderEvent, PaperAccount, Position, Signal, Strategy
from tradebot.repository import notify, open_positions, risk_config, unrealized

ZERO = Decimal(0)


class PaperRejected(ValueError):
    pass


def committed_position_risk(position: Position) -> Decimal:
    from tradebot_core import Instrument as CoreInstrument, PaperFillModel, Side
    instrument = CoreInstrument.model_validate(position.instrument_snapshot)
    entry_side = Side(position.side)
    settings = get_settings()
    fee_rate = Decimal(settings.paper_fee_rate)
    model = PaperFillModel(fee_rate=fee_rate, slippage_bps=Decimal(settings.paper_slippage_bps))
    stop_fill = model.execution_price(instrument, entry_side.opposite, position.stop_price)
    return max(ZERO, -instrument.pnl(position.entry_price, stop_fill, position.quantity, entry_side)) + instrument.fee(position.entry_price, position.quantity, fee_rate) + instrument.fee(stop_fill, position.quantity, fee_rate)


def lock_account(db: Session) -> PaperAccount:
    # PostgreSQL row lock serializes API, worker, emergency, and lease updates.
    # SQLite is restricted to isolated tests; it has no production guarantees.
    account = db.scalar(select(PaperAccount).where(PaperAccount.id == 1).with_for_update())
    if account is None:
        raise PaperRejected("Initialize a paper account first")
    if account.mode != "PAPER":
        raise PaperRejected("Executor only supports PAPER accounts")
    return account


def core_instrument(row: Instrument):
    from tradebot_core import Instrument as CoreInstrument
    value = dict(row.metadata_json)
    paper_model = False
    if "product_id" not in value:
        # Percent fields are not silently interpreted as fractions. Ingestion
        # must supply a verified normalized margin fraction to enable sizing.
        paper_model = bool(value.get("linear_economics_verified"))
        value = {
            "product_id": row.id, "symbol": row.symbol, "contract_type": "LINEAR",
            "underlying_asset": value.get("underlying_asset"), "quote_asset": value.get("quote_asset"),
            "settlement_asset": value.get("settlement_asset"), "contract_size": value.get("contract_value"),
            "tick_size": value.get("tick_size"), "quantity_step": "1" if paper_model else value.get("qty_step"),
            "min_quantity": "1" if paper_model else value.get("min_qty"),
            "initial_margin_rate": "0.5" if paper_model else value.get("initial_margin_rate"),
            "metadata_verified": row.verified or paper_model, "metadata_timestamp": aware(row.updated_at),
            "trading_enabled": value.get("trading_status") == "operational" and value.get("state") == "live",
        }
    value["metadata_verified"] = row.verified or paper_model
    value["metadata_timestamp"] = aware(row.updated_at)
    if paper_model:
        value["verification_scope"] = "PAPER_MODEL"
        value["assumptions"] = ["Linear multiplier, tick size, contract unit and same-currency settlement verified from actual Delta India public metadata.",
                                "Internal PAPER minimum quantity is one whole contract with integer increments; actual exchange minimum remains uncertified.",
                                "Conservative internal PAPER margin fraction is 50%; actual exchange margin units and tiers remain uncertified."]
    instrument = CoreInstrument.model_validate(value)
    if not instrument.metadata_verified or not instrument.trading_enabled:
        raise PaperRejected("Verified, enabled contract economics are required")
    if (utcnow() - instrument.metadata_timestamp).total_seconds() > 86400:
        raise PaperRejected("Contract metadata is stale")
    if instrument.conversion_timestamp and (utcnow() - instrument.conversion_timestamp).total_seconds() > 300:
        raise PaperRejected("Settlement conversion rate is stale")
    return instrument


def sizing_atr(db: Session, config, candle: Candle):
    """Only observations closed by this signal can inform volatility sizing."""
    if config.sizing.mode != "VOLATILITY":
        return None
    from tradebot.repository import core_candles
    from tradebot_core.indicators import atr
    period = config.sizing.volatility_period
    rows = list(reversed(list(db.scalars(select(Candle).where(
        Candle.symbol == config.symbol, Candle.timeframe_seconds == config.timeframe_seconds,
        Candle.timestamp <= candle.timestamp,
    ).order_by(Candle.timestamp.desc()).limit(max(50, period*5))))))
    if any(aware(right.timestamp)-aware(left.timestamp) != timedelta(seconds=config.timeframe_seconds)
           for left, right in zip(rows, rows[1:])):
        raise PaperRejected("Continuous observed candles required for volatility sizing")
    values = atr(core_candles(rows), period)
    if not values or values[-1] is None or values[-1] <= 0:
        raise PaperRejected("Warm positive ATR required for volatility sizing")
    return values[-1]


def snapshot(db: Session, account: PaperAccount):
    equity = account.cash + sum((unrealized(p) for p in open_positions(db)), ZERO)
    account.high_water_equity = max(account.high_water_equity, equity)
    from tradebot.strategy_risk import snapshot_strategy_equities
    snapshot_strategy_equities(db, account)
    db.add(EquitySnapshot(account_id=account.id, equity=equity, cash=account.cash))


def period_start_equity(db: Session, account: PaperAccount, boundary) -> Decimal:
    point = db.scalar(select(EquitySnapshot).where(EquitySnapshot.account_id == account.id, EquitySnapshot.created_at <= boundary)
                      .order_by(EquitySnapshot.created_at.desc()).limit(1))
    if point:
        return point.equity
    if aware(account.created_at) >= boundary:
        return account.initial_balance
    raise PaperRejected("Missing durable period-start equity; reconciliation required")


def period_pnl(db: Session, account: PaperAccount, equity: Decimal):
    from tradebot.expansion_models import PaperMovement
    today = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today - timedelta(days=today.weekday())
    day_equity = period_start_equity(db, account, today)
    week_equity = period_start_equity(db, account, week_start)
    movements = list(db.scalars(select(PaperMovement).where(PaperMovement.account_id == account.id, PaperMovement.kind == "RESET")))
    day_flows = sum((movement.amount for movement in movements if aware(movement.created_at) > today), ZERO)
    week_flows = sum((movement.amount for movement in movements if aware(movement.created_at) > week_start), ZERO)
    return max(Decimal("0.00000001"), day_equity+day_flows), max(Decimal("0.00000001"), week_equity+week_flows), equity-day_equity-day_flows, equity-week_equity-week_flows


def claim_lease(db: Session, owner: str, seconds=120) -> bool:
    lock_account(db)
    lease = db.get(ExecutorLease, 1)
    now = utcnow()
    if lease and lease.owner != owner and aware(lease.expires_at) > now:
        return False
    if lease is None:
        lease = ExecutorLease(account_id=1, owner=owner, expires_at=now + timedelta(seconds=seconds))
        db.add(lease)
    else:
        lease.owner, lease.expires_at = owner, now + timedelta(seconds=seconds)
    return True


def reconcile(db: Session, account: PaperAccount) -> list[str]:
    from tradebot.expansion_models import PaperMovement
    from tradebot.paper_expansion import pending_entries
    issues = []
    positions = open_positions(db)
    if len({position.symbol for position in positions}) != len(positions):
        issues.append("Multiple open positions own the same symbol")
    for position in positions:
        orders = list(db.scalars(select(Order).where(Order.position_id == position.id)))
        entry = [order for order in orders if not order.reduce_only and order.status in ("FILLED", "PARTIALLY_FILLED", "CANCELED", "EXPIRED", "REJECTED")]
        protection = [order for order in orders if order.reduce_only and order.status == "OPEN"]
        entry_fills = list(db.scalars(select(Fill).where(Fill.order_id == entry[0].id))) if len(entry) == 1 else []
        exit_fills = list(db.scalars(select(Fill).join(Order).where(Order.position_id == position.id, Order.reduce_only.is_(True))))
        total_entry = sum((fill.quantity for fill in entry_fills), ZERO)
        total_exit = sum((fill.quantity for fill in exit_fills), ZERO)
        average = sum((fill.price*fill.quantity for fill in entry_fills), ZERO)/total_entry if total_entry else ZERO
        tolerance = Decimal("0.0000000001")
        if not entry_fills:
            issues.append(f"Position {position.id} missing durable entry fill")
        elif (abs(total_entry-total_exit-position.quantity) > tolerance or abs(average-position.entry_price) > tolerance
              or any(fill.position_id != position.id or fill.fee < 0 for fill in entry_fills)
              or entry[0].quantity < total_entry or entry[0].side != position.side
              or entry[0].account_id != account.id or entry[0].mode != "PAPER"):
            issues.append(f"Position {position.id} entry fill/ownership mismatch")
        opposite = "SELL" if position.side == "BUY" else "BUY"
        expected = {"STOP_MARKET": position.stop_price, "TAKE_PROFIT": position.take_profit_price}
        valid_protection = (len(protection) == 2 and {order.order_type for order in protection} == set(expected)
                            and all(order.quantity == position.quantity and order.side == opposite
                                    and order.price == expected[order.order_type] and order.account_id == account.id
                                    and order.strategy_id == position.strategy_id and order.symbol == position.symbol and order.mode == "PAPER" for order in protection))
        if not valid_protection:
            issues.append(f"Position {position.id} missing matching protection")
            position.protection_verified = False
        else:
            position.protection_verified = True
    all_fills = list(db.scalars(select(Fill).join(Order).where(Order.account_id == account.id)))
    movements = list(db.scalars(select(PaperMovement).where(PaperMovement.account_id == account.id)))
    expected_cash = account.initial_balance + sum((fill.realized_pnl-fill.fee for fill in all_fills), ZERO) + sum((movement.amount for movement in movements), ZERO)
    expected_fees = sum((fill.fee for fill in all_fills), ZERO)
    expected_realized = sum((fill.realized_pnl for fill in all_fills), ZERO)
    tolerance = Decimal("0.0000000001")
    if abs(account.cash-expected_cash) > tolerance or abs(account.fees_paid-expected_fees) > tolerance or abs(account.realized_pnl-expected_realized) > tolerance:
        issues.append("Paper cash, fee or realized P&L ledger mismatch")
    unknown = db.scalar(select(Order).where(Order.status == "UNKNOWN").limit(1))
    if unknown:
        issues.append("Unresolved UNKNOWN paper order")
    for pending in pending_entries(db):
        order = db.get(Order, pending.order_id)
        filled = sum((fill.quantity for fill in db.scalars(select(Fill).where(Fill.order_id == order.id))), ZERO)
        if (filled != pending.filled_quantity or filled >= order.quantity or pending.reserved_margin <= 0
                or pending.reserved_risk <= 0 or order.reduce_only or order.mode != "PAPER"):
            issues.append(f"Pending order {order.id} reservation/fill mismatch")
    account.reconciliation_required = bool(issues)
    if issues:
        notify(db, "; ".join(issues), "CRITICAL")
    return issues


def _entry_gates(db: Session, account: PaperAccount, symbol: str, config, candle: Candle, strategy_id: int):
    from tradebot.paper_expansion import pending_entries, reservations
    risk = risk_config(db)
    positions = open_positions(db)
    if account.emergency_stop or not risk.entries_enabled:
        raise PaperRejected("New entries disabled by emergency or risk controls")
    if account.reconciliation_required or db.scalar(select(Order).where(Order.status == "UNKNOWN").limit(1)):
        raise PaperRejected("Account state uncertain; reconciliation required")
    if (utcnow() - aware(candle.timestamp) - timedelta(seconds=candle.timeframe_seconds)).total_seconds() > risk.stale_after_seconds:
        raise PaperRejected("Market data is stale")
    if aware(candle.timestamp) + timedelta(seconds=candle.timeframe_seconds) > utcnow():
        raise PaperRejected("Entry requires a completed candle")
    if any(position.symbol == symbol for position in positions):
        raise PaperRejected("Symbol already owned by an open managed position")
    if any(db.get(Order, pending.order_id).symbol == symbol for pending in pending_entries(db)):
        raise PaperRejected("Symbol already reserved by a pending entry")
    if "PAPER" not in getattr(config, "allowed_environments", ("PAPER",)):
        raise PaperRejected("Strategy configuration does not allow PAPER execution")
    if getattr(config, "cooldown_bars", 0):
        latest_signal = db.scalar(select(Signal).where(Signal.strategy_id == strategy_id,
                                  Signal.status.in_(("FILLED", "PENDING", "PARTIALLY_FILLED")))
                                  .order_by(Signal.candle_time.desc()).limit(1))
        if latest_signal and aware(candle.timestamp) < aware(latest_signal.candle_time)+timedelta(seconds=config.cooldown_bars*config.timeframe_seconds):
            raise PaperRejected("Strategy cooldown is active")
    from tradebot.news import entry_allowed
    event_gate = entry_allowed(db, symbol, utcnow())
    if not event_gate["allowed"]:
        raise PaperRejected("Event/news blackout: " + ", ".join(reason["kind"] for reason in event_gate["reasons"]))
    if len(positions) >= risk.max_open_positions:
        raise PaperRejected("Maximum open positions reached")
    if any(not position.protection_verified for position in positions):
        raise PaperRejected("Existing position lacks verified protection")
    equity = account.cash + sum((unrealized(p) for p in positions), ZERO)
    if equity <= 0:
        raise PaperRejected("Nonpositive account equity")
    from tradebot.strategy_risk import strategy_risk_reasons
    strategy_reasons = strategy_risk_reasons(db, strategy_id, config, account, equity=equity)
    if strategy_reasons:
        raise PaperRejected(", ".join(strategy_reasons))
    drawdown = (account.high_water_equity - equity) / account.high_water_equity
    if drawdown >= risk.max_drawdown:
        raise PaperRejected("Maximum drawdown reached")
    day_equity, _, day_pnl, _ = period_pnl(db, account, equity)
    if -day_pnl >= day_equity * risk.max_daily_loss:
        raise PaperRejected("Daily loss limit reached")
    if config.risk_fraction > risk.max_risk_per_trade or config.max_leverage > risk.max_leverage:
        raise PaperRejected("Strategy exceeds account risk limits")
    today = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    if getattr(config, "max_trades_per_day", None) is not None:
        trades = list(db.scalars(select(Position).where(Position.strategy_id == strategy_id, Position.opened_at >= today)))
        if len(trades) >= config.max_trades_per_day:
            raise PaperRejected("Strategy daily trade limit reached")
    if getattr(config, "max_daily_loss_fraction", None) is not None:
        from tradebot.expansion_models import PaperMovement
        fills = list(db.scalars(select(Fill).join(Order).where(Order.strategy_id == strategy_id, Fill.created_at >= today)))
        movements = list(db.scalars(select(PaperMovement).join(Position).where(Position.strategy_id == strategy_id,
                        PaperMovement.kind != "RESET", PaperMovement.created_at >= today)))
        strategy_pnl = sum((fill.realized_pnl-fill.fee for fill in fills), ZERO)+sum((movement.amount for movement in movements), ZERO)
        strategy_pnl += sum((unrealized(position) for position in positions if position.strategy_id == strategy_id), ZERO)
        baseline = day_equity*(config.capital_allocation_fraction or Decimal(1))
        if strategy_pnl <= -baseline*config.max_daily_loss_fraction:
            raise PaperRejected("Strategy daily loss limit reached")
    reserved_margin, reserved_risk = reservations(db)
    existing_risk = sum((committed_position_risk(p) for p in positions), ZERO)+reserved_risk
    if existing_risk + equity * config.risk_fraction > equity * risk.max_total_risk:
        raise PaperRejected("Portfolio risk budget exceeded")
    available_margin = equity - sum((p.margin for p in positions), ZERO)-reserved_margin
    if getattr(config, "capital_allocation_fraction", None) is not None:
        owned_margin = sum((position.margin for position in positions if position.strategy_id == strategy_id), ZERO)
        owned_margin += sum((pending.reserved_margin for pending in pending_entries(db) if pending.strategy_id == strategy_id), ZERO)
        available_margin = min(available_margin, equity*config.capital_allocation_fraction-owned_margin)
        if available_margin <= 0:
            raise PaperRejected("Strategy capital allocation exhausted")
    return equity, available_margin


def _order(db, *, position: Position, side: str, kind: str, status: str, price: Decimal, signal_id=None, reduce=False):
    oid = uuid.uuid4().hex
    order = Order(id=oid, client_order_id=oid, signal_id=signal_id, strategy_id=position.strategy_id,
                  account_id=position.account_id, position_id=position.id, symbol=position.symbol, side=side,
                  order_type=kind, status=status, quantity=position.quantity, price=price, reduce_only=reduce, mode="PAPER")
    db.add(order)
    db.flush()
    db.add(OrderEvent(order_id=oid, event_type=status, details={"mode": "PAPER", "simulated": True}))
    return order


def enter_signal(db: Session, strategy_id: int, config, generated, candle: Candle, *, executor_healthy: bool = False) -> Position | None:
    from tradebot_core import PaperFillModel, RiskContext, RiskLimits, Side, evaluate_risk, size_position
    # Identity excludes the mutable lookback snapshot. Same strategy/version,
    # bar and direction cannot acquire exposure twice after history repair.
    sid = hashlib.sha256(f"{strategy_id}:{config.version}:{generated.candle_timestamp.isoformat()}:{generated.side}".encode()).hexdigest()
    account = lock_account(db)
    if db.get(Signal, sid):
        return None
    signal = Signal(id=sid, strategy_id=strategy_id, version=config.version, symbol=config.symbol,
                    side=str(generated.side), candle_time=generated.candle_timestamp)
    db.add(signal)
    db.flush()
    risk_record = None
    risk_context = {}
    try:
        with db.begin_nested():
            row = db.scalar(select(Instrument).where(Instrument.symbol == config.symbol))
            if row is None:
                raise PaperRejected("No verified instrument metadata")
            instrument = core_instrument(row)
            if instrument.settlement_asset != account.settlement_asset:
                raise PaperRejected("Instrument settlement does not match the paper account")
            equity, available = _entry_gates(db, account, config.symbol, config, candle, strategy_id)
            if not executor_healthy:
                raise PaperRejected("Healthy lease-owning worker and public data fetch required")
            settings = get_settings()
            fee_rate, slip = Decimal(settings.paper_fee_rate), Decimal(settings.paper_slippage_bps)
            direction = Decimal(1) if generated.side == Side.BUY else Decimal(-1)
            stop = (generated.stop_price / instrument.tick_size).to_integral_value(rounding=ROUND_FLOOR if direction > 0 else ROUND_CEILING) * instrument.tick_size
            target = (generated.target_price / instrument.tick_size).to_integral_value(rounding=ROUND_CEILING if direction > 0 else ROUND_FLOOR) * instrument.tick_size
            sizing = size_position(instrument=instrument, side=generated.side, entry_price=candle.close,
                                   stop_price=stop, equity=equity, risk_fraction=config.risk_fraction,
                                   max_leverage=config.max_leverage, available_margin=available, fee_rate=fee_rate, slippage_bps=slip,
                                   sizing=config.sizing, volatility_atr=sizing_atr(db, config, candle))
            if sizing.quantity <= 0:
                raise PaperRejected(sizing.reason or "Risk budget cannot fund a minimum contract")
            from tradebot.strategy_risk import strategy_liquidation_reasons
            liquidation_reasons = strategy_liquidation_reasons(db, config, instrument=instrument, side=generated.side, size=sizing)
            if liquidation_reasons:
                raise PaperRejected(", ".join(liquidation_reasons))
            positions = open_positions(db)
            risk = risk_config(db)
            day_equity, week_equity, daily_pnl, weekly_pnl = period_pnl(db, account, equity)
            exposure = sum((item.mark_price * item.quantity * item.contract_size * item.conversion_rate for item in positions), ZERO)
            net_exposure = sum((item.mark_price * item.quantity * item.contract_size * item.conversion_rate * (1 if item.side == "BUY" else -1) for item in positions), ZERO)
            committed_risk = sum((committed_position_risk(item) for item in positions), ZERO)
            from tradebot.paper_expansion import pending_entries, reservations, reserved_exposures, queue_entry, state_for
            reserved_margin, reserved_risk = reservations(db)
            reserved_gross, reserved_net = reserved_exposures(db)
            from tradebot.repository import correlated_exposure
            from tradebot.clock import clock_healthy
            context = RiskContext(equity=equity, available_margin=max(ZERO, available),
                                  margin_used=sum((position.margin for position in positions), ZERO)+reserved_margin,
                                  gross_exposure=exposure+reserved_gross, net_exposure=net_exposure+reserved_net, open_position_risk=committed_risk, reserved_risk=reserved_risk,
                                  correlated_exposure=correlated_exposure(db, config.symbol, risk.correlation_groups),
                                  daily_pnl=daily_pnl, weekly_pnl=weekly_pnl, day_start_equity=max(Decimal("0.00000001"), day_equity),
                                  week_start_equity=max(Decimal("0.00000001"), week_equity), peak_equity=account.high_water_equity,
                                  open_positions=len(positions), now=utcnow(), market_timestamp=aware(candle.timestamp)+timedelta(seconds=candle.timeframe_seconds),
                                  pending_orders=len(pending_entries(db)),
                                  account_reconciled=not account.reconciliation_required, instrument_metadata_fresh=True, data_healthy=True,
                                  exchange_healthy=executor_healthy, worker_healthy=executor_healthy, clock_healthy=clock_healthy(db),
                                  database_healthy=True, protective_orders_verified=all(item.protection_verified for item in positions),
                                  has_unknown_orders=False, emergency_stop=account.emergency_stop)
            limits = RiskLimits(max_risk_fraction=risk.max_risk_per_trade, max_risk_per_trade=equity*risk.max_risk_per_trade,
                                max_committed_risk_fraction=risk.max_total_risk, max_daily_loss_fraction=risk.max_daily_loss,
                                max_weekly_loss_fraction=risk.max_weekly_loss,
                                max_drawdown_fraction=risk.max_drawdown, max_leverage=risk.max_leverage,
                                max_open_positions=risk.max_open_positions, market_max_age_seconds=risk.stale_after_seconds,
                                max_pending_orders=risk.max_pending_orders, max_margin_fraction=risk.max_margin_fraction,
                                max_gross_exposure_multiple=risk.max_gross_exposure_multiple,
                                max_net_exposure_multiple=risk.max_net_exposure_multiple,
                                max_correlated_exposure_multiple=risk.max_correlated_exposure_multiple,
                                max_spread_bps=risk.max_spread_bps, max_slippage_bps=risk.max_slippage_bps)
            decision = evaluate_risk(limits=limits, context=context, instrument=instrument, side=generated.side,
                                     size=sizing, target_price=target, leverage=config.max_leverage, fee_rate=fee_rate, slippage_bps=slip)
            risk_record = {**decision.model_dump(mode="json"), "requested_quantity": str(sizing.requested_quantity or sizing.quantity),
                           "sized_quantity": str(sizing.quantity), "limits": limits.model_dump(mode="json")}
            risk_context = context.model_dump(mode="json")
            if not decision.approved:
                raise PaperRejected(", ".join(decision.reasons))
            if getattr(config, "execution", None) is not None:
                queue_entry(db, signal=signal, config=config, instrument=instrument, sizing=sizing, stop=stop, target=target, candle=candle)
                return None
            fill = PaperFillModel(fee_rate=fee_rate, slippage_bps=slip).fill(instrument, generated.side, sizing.quantity, candle.close)
            if not (stop < fill.price < target if direction > 0 else target < fill.price < stop):
                raise PaperRejected("Protection prices do not bracket simulated fill")
            position = Position(id=uuid.uuid4().hex, account_id=1, strategy_id=strategy_id, symbol=config.symbol,
                                side=str(generated.side), quantity=fill.quantity, contract_size=instrument.contract_size,
                                conversion_rate=instrument.settlement_rate, entry_price=fill.price, mark_price=candle.close,
                                instrument_snapshot=instrument.model_dump(mode="json"),
                                last_protection_candle_at=generated.candle_timestamp,
                                stop_price=stop, take_profit_price=target,
                                margin=instrument.margin(fill.price, fill.quantity, config.max_leverage), protection_verified=True)
            db.add(position)
            db.flush()
            state = state_for(db, position, config)
            state.entry_observations = {"reference_price": str(candle.close), "fill_price": str(fill.price),
                                        "fee_rate": str(fee_rate), "slippage_bps": str(slip),
                                        "spread_bps": "1", "source": "closed Delta India candle or declared test fixture"}
            order = _order(db, position=position, side=position.side, kind="MARKET", status="FILLED", price=fill.price, signal_id=sid)
            db.add(Fill(id=hashlib.sha256(order.id.encode()).hexdigest(), order_id=order.id, position_id=position.id,
                        quantity=fill.quantity, price=fill.price, fee=fill.fee))
            opposite = "SELL" if position.side == "BUY" else "BUY"
            _order(db, position=position, side=opposite, kind="STOP_MARKET", status="OPEN", price=stop, reduce=True)
            _order(db, position=position, side=opposite, kind="TAKE_PROFIT", status="OPEN", price=target, reduce=True)
            account.cash -= fill.fee
            account.fees_paid += fill.fee
            signal.status = "FILLED"
            notify(db, f"PAPER {position.side} {position.quantity} {position.symbol}; stop and target verified")
            db.flush()
            snapshot(db, account)
            return position
    except (ValueError, ArithmeticError) as error:
        signal.status, signal.reason = "REJECTED", str(error)
        risk_record = {**(risk_record or {}), "approved": False, "reasons": [str(error)], "approved_quantity": "0"}
        notify(db, f"PAPER signal rejected: {error}", "WARNING")
        return None
    finally:
        if risk_record is not None:
            from tradebot.expansion_models import PaperRiskDecision
            db.add(PaperRiskDecision(signal_id=sid, strategy_id=strategy_id, version=config.version,
                                     approved=risk_record["approved"], decision=risk_record, context=risk_context))


def close_position(db: Session, position: Position, reference_price: Decimal, reason="OPERATOR", *, execution_at=None):
    account = lock_account(db)
    if position.status != "OPEN":
        return position
    from tradebot.paper_expansion import apply_funding_through, cancel_pending, pending_entries, state_for
    apply_funding_through(db, position, execution_at or utcnow())
    state = state_for(db, position)
    state.exited_quantity += position.quantity
    for pending in pending_entries(db):
        if db.get(Order, pending.order_id).position_id == position.id:
            cancel_pending(db, pending, "Position closed")
    from tradebot_core import Instrument as CoreInstrument, PaperFillModel, Side
    side = Side.SELL if position.side == "BUY" else Side.BUY
    # PAPER close remains possible if product metadata later disappears: use
    # economics captured on durable entry, never guessed replacement metadata.
    settings = get_settings()
    instrument = CoreInstrument.model_validate(position.instrument_snapshot)
    simulated = PaperFillModel(fee_rate=Decimal(settings.paper_fee_rate), slippage_bps=Decimal(settings.paper_slippage_bps)).fill(instrument, side, position.quantity, reference_price)
    price, fee = simulated.price, simulated.fee
    pnl = instrument.pnl(position.entry_price, price, position.quantity, side.opposite)
    order = _order(db, position=position, side=side.value, kind="MARKET", status="FILLED", price=price, reduce=True)
    db.add(Fill(id=hashlib.sha256(order.id.encode()).hexdigest(), order_id=order.id, position_id=position.id,
                quantity=position.quantity, price=price, fee=fee, realized_pnl=pnl,
                created_at=aware(execution_at) if execution_at else utcnow()))
    for protective in db.scalars(select(Order).where(Order.position_id == position.id, Order.status == "OPEN")):
        protective.status = "CANCELED"
        db.add(OrderEvent(order_id=protective.id, event_type="CANCELED", details={"reason": reason}))
    position.status, position.closed_at, position.mark_price = "CLOSED", aware(execution_at) if execution_at else utcnow(), price
    position.realized_pnl += pnl
    account.cash += pnl - fee
    account.realized_pnl += pnl
    account.fees_paid += fee
    notify(db, f"PAPER position {position.symbol} closed ({reason}); realized P&L {pnl} {account.settlement_asset}")
    db.flush()
    snapshot(db, account)
    return position


def protect_positions(db: Session, history: dict[int, list[Candle]]):
    from tradebot.paper_expansion import advanced_exit, apply_funding_through, pre_protective_exit, state_for
    account = lock_account(db)
    risk = risk_config(db)
    for position in open_positions(db):
        bars = history.get(position.strategy_id, [])
        if not bars:
            account.reconciliation_required = True
            notify(db, f"No closed protective candle history for {position.symbol}; new exposure blocked", "CRITICAL")
            continue
        timeframe = bars[-1].timeframe_seconds
        if position.last_protection_candle_at:
            cursor = aware(position.last_protection_candle_at)
        else:
            opened = aware(position.opened_at)
            aligned = opened.timestamp() // timeframe * timeframe
            from datetime import datetime, timezone
            cursor = datetime.fromtimestamp(aligned, timezone.utc)-timedelta(seconds=timeframe)
        for candle in bars:
            timestamp = aware(candle.timestamp)
            if timestamp <= cursor:
                continue
            if timestamp != cursor + timedelta(seconds=timeframe):
                account.reconciliation_required = True
                notify(db, f"Missing protective candle history for {position.symbol}; cursor retained and new exposure blocked", "CRITICAL")
                break
            if timestamp+timedelta(seconds=timeframe) > utcnow():
                break
            position.mark_price = candle.close
            apply_funding_through(db, position, timestamp)
            long = position.side == "BUY"
            partial_entry_bar = timestamp < aware(position.opened_at)
            if pre_protective_exit(db, position, candle, partial_entry_bar=partial_entry_bar):
                position.last_protection_candle_at = timestamp
                break
            # The entry's first partial bar contains pre-entry price history;
            # its close is the first unambiguous post-entry observation.
            low = high = candle.close if partial_entry_bar else None
            observed_low = low if low is not None else candle.low
            observed_high = high if high is not None else candle.high
            stop_hit = observed_low <= position.stop_price if long else observed_high >= position.stop_price
            target_hit = observed_high >= position.take_profit_price if long else observed_low <= position.take_profit_price
            position.last_protection_candle_at = timestamp
            cursor = timestamp
            if stop_hit:
                price = candle.close if partial_entry_bar else (min(candle.open, position.stop_price) if long else max(candle.open, position.stop_price))
                close_position(db, position, price, "STOP_LOSS_REPLAY", execution_at=timestamp+timedelta(seconds=timeframe) if partial_entry_bar else timestamp)
                break
            state = state_for(db, position)
            custom_targets = bool(state.policy.get("exit_rules", {}).get("targets"))
            if target_hit and not custom_targets:
                close_position(db, position, position.take_profit_price, "TAKE_PROFIT_REPLAY", execution_at=timestamp+timedelta(seconds=timeframe) if partial_entry_bar else timestamp)
                break
            if advanced_exit(db, position, candle, bars, partial_entry_bar=partial_entry_bar):
                break
            from tradebot.strategy_risk import snapshot_strategy_equities
            snapshot_strategy_equities(db, account)
        if position.status == "OPEN" and (utcnow()-aware(bars[-1].timestamp)-timedelta(seconds=timeframe)).total_seconds() <= risk.stale_after_seconds:
            position.mark_price = bars[-1].close
    db.flush()
    snapshot(db, account)
    equity = account.cash + sum((unrealized(position) for position in open_positions(db)), ZERO)
    day_equity, week_equity, day_pnl, week_pnl = period_pnl(db, account, equity)
    if (equity <= account.high_water_equity * (1-risk.max_drawdown) or -day_pnl >= day_equity*risk.max_daily_loss
            or -week_pnl >= week_equity*risk.max_weekly_loss) and risk.entries_enabled:
        risk.entries_enabled = False
        for strategy in db.scalars(select(Strategy).where(Strategy.status == "RUNNING")):
            strategy.status = "PAUSED"
        notify(db, "Portfolio loss/drawdown limit breached: new entries disabled; protective orders retained", "CRITICAL")
