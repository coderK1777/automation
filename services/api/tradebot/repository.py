from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from tradebot.db import aware
from tradebot.models import Audit, Candle, Notification, PaperAccount, Position, RiskConfig, Strategy, StrategyVersion


def serialize(row) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for column in inspect(row).mapper.column_attrs:
        value = getattr(row, column.key)
        if isinstance(value, Decimal):
            value = str(value)
        elif isinstance(value, datetime):
            value = aware(value).isoformat()
        data[column.key] = value
    return data


def audit(db: Session, actor: str, action: str, entity: str, details=None):
    db.add(Audit(actor=actor, action=action, entity=entity, details=details or {}))


def notify(db: Session, message: str, severity="INFO"):
    db.add(Notification(message=message, severity=severity))


def position_net_pnl(db: Session, position: Position) -> Decimal:
    """Observed closed-trade result, including fill and attributed financing costs."""
    from tradebot.models import Fill
    from tradebot.expansion_models import PaperMovement
    fees = sum(db.scalars(select(Fill.fee).where(Fill.position_id == position.id)), Decimal(0))
    movements = sum(db.scalars(select(PaperMovement.amount).where(
        PaperMovement.position_id == position.id,
        PaperMovement.kind.in_(("FUNDING", "LIQUIDATION_FEE")),
    )), Decimal(0))
    return position.realized_pnl - fees + movements


def correlated_exposure(db: Session, symbol: str, groups: list[list[str]], pending_ratios=None) -> Decimal:
    """Conservative gross exposure in operator-declared groups, including reservations."""
    from tradebot.models import Order
    from tradebot.paper_expansion import pending_entries
    relevant = [set(group) for group in groups if symbol in group]
    if not relevant:
        return Decimal(0)
    totals = dict.fromkeys(set().union(*relevant), Decimal(0))
    for position in open_positions(db):
        if position.symbol in totals:
            totals[position.symbol] += position.mark_price*position.quantity*position.contract_size*position.conversion_rate
    for pending in pending_entries(db):
        order = db.get(Order, pending.order_id)
        if order.symbol in totals:
            ratio = (pending_ratios or {}).get(pending.order_id, Decimal(1))
            totals[order.symbol] += pending.reserved_margin*Decimal(pending.config["max_leverage"])*ratio
    return max((sum((totals[item] for item in group), Decimal(0)) for group in relevant), default=Decimal(0))


def version(db: Session, strategy: Strategy) -> StrategyVersion:
    return db.scalar(select(StrategyVersion).where(
        StrategyVersion.strategy_id == strategy.id,
        StrategyVersion.version == strategy.current_version,
    ))


def strategy_dict(db: Session, strategy: Strategy):
    data = serialize(strategy)
    data["version"] = strategy.current_version
    data["config"] = version(db, strategy).config
    data["mode"] = "PAPER"
    return data


def risk_config(db: Session) -> RiskConfig:
    value = db.get(RiskConfig, 1)
    if value is None:
        value = RiskConfig(id=1)
        db.add(value)
        db.flush()
    return value


def open_positions(db: Session):
    return list(db.scalars(select(Position).where(Position.status == "OPEN")))


def unrealized(position: Position) -> Decimal:
    direction = Decimal(1) if position.side == "BUY" else Decimal(-1)
    return direction * (position.mark_price - position.entry_price) * position.quantity * position.contract_size * position.conversion_rate


def position_timeframe(db: Session, position: Position) -> int:
    from tradebot.expansion_models import PaperPositionState
    state = db.get(PaperPositionState, position.id)
    if state and state.policy.get("timeframe_seconds"):
        return state.policy["timeframe_seconds"]
    return version(db, db.get(Strategy, position.strategy_id)).config["timeframe_seconds"]


def account_dict(db: Session, account: PaperAccount | None):
    if account is None:
        return None
    positions = open_positions(db)
    pnl = sum((unrealized(position) for position in positions), Decimal(0))
    margin = sum((position.margin for position in positions), Decimal(0))
    from tradebot.paper_expansion import reservations
    reserved_margin, reserved_risk = reservations(db)
    result = serialize(account)
    result.update(equity=str(account.cash + pnl), unrealized_pnl=str(pnl), margin_used=str(margin),
                  available_margin=str(account.cash + pnl - margin - reserved_margin),
                  reserved_margin=str(reserved_margin), reserved_risk=str(reserved_risk), simulated=True,
                  reporting_currency=account.settlement_asset,
                  planning_note="INR capital is a separate planning input. FX conversion requires an explicit rate.")
    return result


def candle_rows(db: Session, symbol: str, timeframe: int, limit: int = 2000):
    rows = list(db.scalars(select(Candle).where(Candle.symbol == symbol, Candle.timeframe_seconds == timeframe)
                          .order_by(Candle.timestamp.desc()).limit(limit)))
    rows.reverse()
    return rows


def core_candles(rows):
    from tradebot_core import Candle as CoreCandle
    return [CoreCandle(timestamp=aware(row.timestamp), timeframe_seconds=row.timeframe_seconds,
                      open=row.open, high=row.high, low=row.low, close=row.close, volume=row.volume, closed=True) for row in rows]
