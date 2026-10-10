"""DEMO-only period evidence and immutable exports. No exchange request occurs."""
import csv
import hashlib
import io
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from tradebot.db import aware, get_db, utcnow
from tradebot.demo_models import DemoAccount, DemoCashMovement, DemoEquitySnapshot, DemoFill, DemoOrder, DemoPerformanceReport, DemoProtection
from tradebot.models import Audit, Notification
from tradebot.reporting import period_bounds
from tradebot.security import identity, operator
from tradebot_core import Instrument, Side
from tradebot_core.arithmetic import fixed_decimal

router = APIRouter(prefix="/reports", tags=["DEMO native-currency reports"])
D = Decimal
IST = ZoneInfo("Asia/Calcutta")


class ReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    period: Literal["DAILY", "WEEKLY", "MONTHLY"] = "DAILY"
    date: date


def timestamp(value: str) -> datetime | None:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result.astimezone(timezone.utc) if result.tzinfo else None
    except (ValueError, AttributeError):
        return None


@fixed_decimal
def closed_episodes(db: Session, end: datetime, currency: str):
    """Reconstruct only verified managed linear fills, without guessed funding.

    Missing times, external ownership, cross-currency or contradictory quantity
    make coverage incomplete instead of creating a profitable fictional trade.
    """
    orders = {order.id: order for order in db.scalars(select(DemoOrder))}
    protections = {row.exchange_order_id: row for row in db.scalars(select(DemoProtection))}
    events, issues = [], []
    for fill in db.scalars(select(DemoFill)):
        moment = timestamp(fill.exchange_created_at)
        if moment is None:
            issues.append("A fill lacks an authoritative exchange timestamp; it is excluded from period trade attribution")
            continue
        if moment >= end:
            continue
        owner = orders.get(fill.order_id)
        protection = protections.get(fill.protection_order_id)
        if owner is None and protection:
            owner = orders.get(protection.entry_order_id)
        if owner is None or fill.settlement_asset != currency:
            issues.append("Unowned or other-currency fill is excluded from managed performance")
            continue
        events.append((moment, fill.id, fill, owner, protection is not None))
    events.sort(key=lambda row: (row[0], row[1]))
    opened, closed, realized_exits, tainted = {}, [], [], set()
    def taint(product_id):
        tainted.add(product_id)
        current = opened.pop(product_id, None)
        if current:
            realized_exits[:] = [row for row in realized_exits if row["entry_order_id"] != current["order_id"]]
    for moment, _, fill, order, protective in events:
        if fill.product_id in tainted:
            continue
        current = opened.get(fill.product_id)
        protection = protections.get(fill.protection_order_id) if protective else None
        expected_side = Side(order.side).opposite.value if protective else order.side
        if fill.product_id != order.product_id or fill.side != expected_side or fill.quantity <= 0 or (
            protection and protection.product_id != fill.product_id
        ):
            issues.append("Fill identity conflicts with durable order/protection ownership; product episode is excluded")
            taint(fill.product_id)
            continue
        entry = not order.reduce_only and not protective
        if entry:
            try:
                contract = Instrument.model_validate(order.instrument_snapshot)
            except ValueError:
                issues.append("Fill instrument snapshot is invalid; episode attribution unavailable")
                continue
            if contract.verification_scope != "EXCHANGE" or contract.settlement_asset != currency:
                issues.append("Fill lacks verified exchange native-currency economics")
                continue
            if current and (current["order_id"] != order.id or current["side"] != fill.side):
                issues.append("Overlapping strategy entry ownership prevents deterministic episode attribution")
                taint(fill.product_id)
                continue
            if not current:
                current = {"order_id": order.id, "strategy_id": order.strategy_id, "strategy_version": order.strategy_version,
                           "product_id": order.product_id, "symbol": order.symbol, "side": fill.side, "quantity": D(0),
                           "cost": D(0), "entry_quantity": D(0), "entry_fees": D(0), "exit_fees": D(0), "gross_pnl": D(0),
                           "opened_at": moment, "instrument": contract}
                opened[fill.product_id] = current
            current["quantity"] += fill.quantity
            current["entry_quantity"] += fill.quantity
            current["cost"] += fill.price*fill.quantity
            current["entry_fees"] += fill.fee
            continue
        if protective and current and protection.entry_order_id != current["order_id"]:
            issues.append("Protective exit belongs to another entry; product episode attribution is excluded")
            taint(fill.product_id)
            continue
        if not current or fill.side == current["side"] or fill.quantity > current["quantity"]:
            issues.append("Exit fill cannot be matched to a managed open quantity; no P&L is invented")
            taint(fill.product_id)
            continue
        average = current["cost"]/current["quantity"]
        pnl = current["instrument"].pnl(average, fill.price, fill.quantity, Side(current["side"]))
        current["gross_pnl"] += pnl
        current["exit_fees"] += fill.fee
        current["quantity"] -= fill.quantity
        current["cost"] -= average*fill.quantity
        realized_exits.append({"timestamp": moment, "gross_pnl": pnl, "exit_fee": fill.fee, "entry_order_id": current["order_id"]})
        if current["quantity"] == 0:
            closed.append({"entry_order_id": current["order_id"], "strategy_id": current["strategy_id"], "strategy_version": current["strategy_version"],
                           "symbol": current["symbol"], "side": current["side"], "opened_at": current["opened_at"].isoformat(), "closed_at": moment.isoformat(),
                           "entry_quantity": str(current["entry_quantity"]), "gross_pnl": str(current["gross_pnl"]),
                           "entry_fees": str(current["entry_fees"]), "exit_fees": str(current["exit_fees"]),
                           "net_pnl_excluding_funding": str(current["gross_pnl"]-current["entry_fees"]-current["exit_fees"])})
            del opened[fill.product_id]
    return closed, realized_exits, sorted(set(issues))


@fixed_decimal
def period_report(db: Session, period: str, value: date, *, now: datetime | None = None):
    account = db.get(DemoAccount, 1)
    if not account or not account.connected:
        raise HTTPException(409, "Connect an actual demo account before generating DEMO reports")
    now = now or utcnow()
    start, end = period_bounds(period, value)
    effective_end = min(end, now)
    if start > now:
        raise HTTPException(422, "Future DEMO reporting periods have no observations")
    points = list(db.scalars(select(DemoEquitySnapshot).where(DemoEquitySnapshot.created_at < effective_end).order_by(DemoEquitySnapshot.created_at, DemoEquitySnapshot.id)))
    opening = next((point for point in reversed(points) if aware(point.created_at) <= start), None)
    closing = points[-1] if points and aware(points[-1].created_at) >= start else None
    start_equity = opening.equity if opening else account.initial_equity if start <= aware(account.created_at) < effective_end else None
    end_equity = closing.equity if closing else None
    in_period = [point for point in points if start <= aware(point.created_at) < effective_end]
    movements = list(db.scalars(select(DemoCashMovement).where(DemoCashMovement.exchange_created_at >= start,
                                   DemoCashMovement.exchange_created_at < effective_end,
                                   DemoCashMovement.transaction_type != "LOCAL_OBSERVATION_BASELINE").order_by(DemoCashMovement.exchange_created_at, DemoCashMovement.id)))
    totals = {}
    for row in movements:
        key = row.asset_symbol+":"+row.transaction_type
        totals[key] = totals.get(key, D(0))+row.amount
    native = [row for row in movements if row.asset_symbol == account.settlement_asset]
    period_fills = []
    timestamp_missing = 0
    for fill in db.scalars(select(DemoFill)):
        moment = timestamp(fill.exchange_created_at)
        if moment is None:
            timestamp_missing += 1
        elif start <= moment < effective_end:
            period_fills.append(fill)
    closed, exits, issues = closed_episodes(db, effective_end, account.settlement_asset)
    selected = [episode for episode in closed if start <= timestamp(episode["closed_at"]) < effective_end]
    net_values = [D(episode["net_pnl_excluding_funding"]) for episode in selected]
    wins, losses = sum(value > 0 for value in net_values), sum(value < 0 for value in net_values)
    positive, negative = sum((p for p in net_values if p > 0), D(0)), -sum((p for p in net_values if p < 0), D(0))
    sampled = ([start_equity] if start_equity is not None else [])+[p.equity for p in in_period]
    peak, maximum_drawdown = D(0), D(0)
    for equity in sampled:
        peak = max(peak, equity)
        if peak > 0:
            maximum_drawdown = max(maximum_drawdown, (peak-equity)/peak)
    coverage = list(issues)
    if start_equity is None:
        coverage.append("Opening boundary equity is unavailable; equity change and return remain null")
    if opening and (start-aware(opening.created_at)).total_seconds() > 60:
        coverage.append("Opening equity observation is older than 60 seconds from the requested boundary; observed equity change can include pre-period activity")
    if end_equity is None:
        coverage.append("Closing period equity is unavailable")
    if closing and (effective_end-aware(closing.created_at)).total_seconds() > 60:
        coverage.append("Closing equity observation is older than 60 seconds from the requested boundary")
    if timestamp_missing:
        coverage.append("Some fills lack authoritative timestamps and cannot be assigned to periods")
    if account.reconciliation_required:
        coverage.append("Current demo account has reconciliation issues; these figures are observed evidence, not certified reconciled performance")
    coverage.extend(["Closed-trade P&L excludes funding because product payments are not guessed into strategy episodes.",
                     "Sampled drawdown uses observed equity and can include deposits/withdrawals; it is not a flow-adjusted strategy drawdown.",
                     "Equity change includes capital transfers and other wallet activity; it is not presented as trading profit.",
                     "Sharpe, Sortino, annualized return, liquidation modeling and strategy funding attribution require additional verified coverage and remain null."])
    equity_change = end_equity-start_equity if start_equity is not None and end_equity is not None else None
    by_strategy = {}
    for episode in selected:
        key = f"{episode['strategy_id']}@{episode['strategy_version']}" if episode["strategy_id"] else "manual"
        bucket = by_strategy.setdefault(key, {"closed_episodes": 0, "net_pnl_excluding_funding": D(0)})
        bucket["closed_episodes"] += 1
        bucket["net_pnl_excluding_funding"] += D(episode["net_pnl_excluding_funding"])
    observed_times = [aware(point.created_at) for point in in_period]+[aware(row.exchange_created_at) for row in movements]
    observed_times.extend(timestamp(fill.exchange_created_at) for fill in period_fills)
    if start <= aware(account.created_at) < effective_end:
        observed_times.append(aware(account.created_at))
    observed_through = max(observed_times).isoformat() if observed_times else None
    result = {"schema_version": 1, "engine_version": "DEMO_REPORT_V1", "mode": "DEMO", "simulated": False, "exchange": "Delta Exchange India DEMO", "account_id": account.id,
              "currency": account.settlement_asset, "period": period, "timezone": "Asia/Calcutta", "period_start": start.isoformat(), "period_end": end.isoformat(),
              "observed_through": observed_through, "partial_period": end > now, "coverage_start": max(start, aware(account.created_at)).isoformat(),
              "actual_order_acceptance_certified": False, "equity_start": str(start_equity) if start_equity is not None else None,
              "opening_observation_at": aware(opening.created_at).isoformat() if opening else aware(account.created_at).isoformat() if start_equity is not None else None,
              "closing_observation_at": aware(closing.created_at).isoformat() if closing else None,
              "equity_end": str(end_equity) if end_equity is not None else None, "observed_equity_change": str(equity_change) if equity_change is not None else None,
              "closed_episodes": len(selected), "wins": wins, "losses": losses, "win_rate": str(D(wins)/len(selected)) if selected else None,
              "profit_factor_excluding_funding": str(positive/negative) if negative > 0 else None,
              "closed_trade_net_pnl_excluding_funding": str(sum(net_values, D(0))),
              "gross_realized_exit_pnl": str(sum((row["gross_pnl"] for row in exits if start <= row["timestamp"] < effective_end), D(0))),
              "fill_count": len(period_fills), "fees_by_currency": {asset: str(sum((f.fee for f in period_fills if f.settlement_asset == asset), D(0))) for asset in sorted({f.settlement_asset for f in period_fills})},
              "native_cash_change": str(sum((row.amount for row in native), D(0))), "cashflows_by_currency_and_type": {key: str(amount) for key, amount in sorted(totals.items())},
              "funding_cashflow": str(sum((row.amount for row in native if row.transaction_type == "funding"), D(0))),
              "sampled_max_drawdown": str(maximum_drawdown) if len(sampled) >= 2 else None,
              "sharpe": None, "sortino": None, "annualized_return": None, "coverage": coverage,
              "equity_curve": [{"timestamp": aware(p.created_at).isoformat(), "equity": str(p.equity), "available_margin": str(p.available_margin)} for p in in_period],
              "trades": selected, "strategies": {key: {**row, "net_pnl_excluding_funding": str(row["net_pnl_excluding_funding"])} for key, row in by_strategy.items()}}
    # Pin monetary source evidence and snapshots, including episode attribution.
    evidence_fills = [fill for fill in db.scalars(select(DemoFill)) if timestamp(fill.exchange_created_at) is None or timestamp(fill.exchange_created_at) < effective_end]
    evidence_fills.sort(key=lambda fill: fill.id)
    evidence_orders = {fill.order_id for fill in evidence_fills if fill.order_id}
    evidence_protection = [row for row in db.scalars(select(DemoProtection)) if row.exchange_order_id in {fill.protection_order_id for fill in evidence_fills}]
    evidence_protection.sort(key=lambda row: row.exchange_order_id)
    evidence_orders.update(row.entry_order_id for row in evidence_protection)
    evidence = {"result": result, "fills": [{"id": f.id, "order_id": f.order_id, "protection_order_id": f.protection_order_id,
                                             "side": f.side, "timestamp": f.exchange_created_at, "quantity": str(f.quantity), "price": str(f.price), "fee": str(f.fee), "currency": f.settlement_asset} for f in evidence_fills],
                "orders": [{"id": order.id, "strategy_id": order.strategy_id, "version": order.strategy_version, "reduce_only": order.reduce_only,
                            "instrument_snapshot": order.instrument_snapshot} for order in db.scalars(select(DemoOrder).where(DemoOrder.id.in_(evidence_orders)).order_by(DemoOrder.id))],
                "protection": [{"id": row.exchange_order_id, "entry_order_id": row.entry_order_id} for row in evidence_protection],
                "movements": [{"id": row.id, "amount": str(row.amount), "balance": str(row.balance), "asset": row.asset_symbol, "type": row.transaction_type,
                               "timestamp": aware(row.exchange_created_at).isoformat()} for row in movements]}
    result["dataset_hash"] = hashlib.sha256(json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return result


def save_report(db: Session, period: str, value: date, actor: str, *, now=None) -> DemoPerformanceReport:
    # Serialize report compilation with reconciliation and other report writers;
    # a source hash must describe one coherent durable account observation.
    db.scalar(select(DemoAccount).where(DemoAccount.id == 1).with_for_update().execution_options(populate_existing=True))
    result = period_report(db, period, value, now=now)
    start, end = period_bounds(period, value)
    existing = db.scalar(select(DemoPerformanceReport).where(DemoPerformanceReport.account_id == 1, DemoPerformanceReport.period == period,
                            DemoPerformanceReport.period_start == start, DemoPerformanceReport.period_end == end, DemoPerformanceReport.dataset_hash == result["dataset_hash"]))
    if existing:
        return existing
    row = DemoPerformanceReport(account_id=1, period=period, period_start=start, period_end=end, dataset_hash=result["dataset_hash"], result=result)
    db.add(row)
    db.flush()
    db.add(Audit(actor=actor, action="demo.report.generate", entity=str(row.id), details={"mode": "DEMO", "period": period, "dataset_hash": row.dataset_hash}))
    return row


def schedule_reports(db: Session, *, now=None):
    account = db.get(DemoAccount, 1)
    if not account or not account.connected or account.reconciliation_required:
        return
    now = now or utcnow()
    today = now.astimezone(IST).date()
    candidates = [("DAILY", today-timedelta(days=1)), ("WEEKLY", today-timedelta(days=today.weekday()+7)), ("MONTHLY", today.replace(day=1)-timedelta(days=1))]
    for period, value in candidates:
        start, end = period_bounds(period, value)
        if aware(account.created_at) >= end or now-end < timedelta(seconds=60):
            continue
        if db.scalar(select(DemoPerformanceReport).where(DemoPerformanceReport.account_id == 1, DemoPerformanceReport.period == period,
                                                       DemoPerformanceReport.period_start == start, DemoPerformanceReport.period_end == end)):
            continue
        report = save_report(db, period, value, "demo-worker:scheduler", now=now)
        db.add(Notification(severity="INFO", message=f"DEMO {period.lower()} report {report.id} generated in {report.result['currency']}; review native cashflows and coverage before interpreting performance"))


def output(row):
    return {"id": row.id, "mode": "DEMO", "period": row.period, "period_start": aware(row.period_start).isoformat(),
            "period_end": aware(row.period_end).isoformat(), "dataset_hash": row.dataset_hash, "created_at": aware(row.created_at).isoformat(), "result": row.result}


@router.get("")
def reports(_=Depends(identity), db: Session = Depends(get_db)):
    return [output(row) for row in db.scalars(select(DemoPerformanceReport).order_by(DemoPerformanceReport.created_at.desc()).limit(100))]


@router.post("", status_code=201)
def generate(body: ReportRequest, user=Depends(operator), db: Session = Depends(get_db)):
    row = save_report(db, body.period, body.date, user.username)
    db.commit()
    return output(row)


@router.get("/{report_id}")
def report(report_id: int, _=Depends(identity), db: Session = Depends(get_db)):
    row = db.get(DemoPerformanceReport, report_id)
    if row is None:
        raise HTTPException(404, "DEMO report not found")
    return output(row)


@router.get("/{report_id}/export")
def export(report_id: int, format: Literal["json", "csv"] = "json", _=Depends(identity), db: Session = Depends(get_db)):
    row = db.get(DemoPerformanceReport, report_id)
    if row is None:
        raise HTTPException(404, "DEMO report not found")
    if format == "json":
        return Response(json.dumps(output(row), indent=2), media_type="application/json", headers={"Content-Disposition": f'attachment; filename="demo-report-{row.id}.json"'})
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(["mode", "currency", "period", "metric", "value"])
    for key, value in row.result.items():
        if not isinstance(value, (list, dict)):
            writer.writerow(["DEMO", row.result["currency"], row.period, key, value])
    writer.writerow(["DEMO", row.result["currency"], row.period, "coverage", json.dumps(row.result["coverage"])])
    return Response(stream.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="demo-report-{row.id}.csv"'})
