"""Persisted paper reports; unavailable evidence stays null instead of invented."""
import csv
import io
import json
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tradebot.db import aware, get_db, utcnow
from tradebot.expansion_models import PaperMovement, PaperPositionState
from tradebot.models import Audit, Candle, EquitySnapshot, Fill, Order, PaperAccount, Position, Strategy, StrategyVersion, WorkerHeartbeat
from tradebot.reporting_models import PerformanceReport
from tradebot.repository import account_dict, audit, notify, position_net_pnl, serialize
from tradebot.security import identity, operator

router = APIRouter(prefix="/api/v1/reports", tags=["reports"])
IST = ZoneInfo("Asia/Calcutta")
Period = Literal["DAILY", "WEEKLY", "MONTHLY"]


class ReportInput(BaseModel):
    period: Period = "DAILY"
    date: date


def period_bounds(period: str, value: date) -> tuple[datetime, datetime]:
    if period == "DAILY":
        start, end = value, value + timedelta(days=1)
    elif period == "WEEKLY":
        start = value - timedelta(days=value.weekday())
        end = start + timedelta(days=7)
    elif period == "MONTHLY":
        start = value.replace(day=1)
        end = date(start.year + (start.month == 12), start.month % 12 + 1, 1)
    else:
        raise ValueError("Unsupported period")
    return datetime.combine(start, time.min, IST).astimezone(timezone.utc), datetime.combine(end, time.min, IST).astimezone(timezone.utc)


def _money(value):
    return None if value is None else str(value)


def evidence_analytics(db: Session, account: PaperAccount, start: datetime, end: datetime, closed: list[Position]) -> dict:
    """Stored entry benchmarks and transparent historical allocation reconstruction."""
    entry_positions = list(db.scalars(select(Position).where(Position.account_id == account.id, Position.opened_at >= start, Position.opened_at < end)))
    samples = []
    for position in entry_positions:
        state = db.get(PaperPositionState, position.id)
        observations = state.entry_observations if state else {}
        try:
            reference = Decimal(observations["reference_price"])
            fill = Decimal(observations["fill_price"])
            if not reference.is_finite() or not fill.is_finite() or reference <= 0 or fill <= 0:
                continue
            direction = Decimal(1) if position.side == "BUY" else Decimal(-1)
            samples.append({"position_id": position.id, "symbol": position.symbol, "reference_price": str(reference),
                            "fill_price": str(fill), "adverse_price_difference_bps": str(direction * (fill - reference) / reference * 10000)})
        except (KeyError, ArithmeticError, ValueError):
            continue
    quality = {"benchmark": "Stored PAPER entry reference and first fill; includes adverse rounding/spread/slippage or limit improvement",
               "covered_positions": len(samples), "total_entry_positions": len(entry_positions), "samples": samples,
               "average_adverse_difference_bps": str(sum((Decimal(row["adverse_price_difference_bps"]) for row in samples), Decimal(0)) / len(samples)) if samples else None,
               "latency": None, "partial_fill_benchmark_coverage": "Only the stored first fill reference; later partial entries lack separate benchmark observations"}
    regimes = {}
    unattributed = 0
    for position in closed:
        state = db.get(PaperPositionState, position.id)
        tag = state.entry_observations.get("regime") if state else None
        label = tag.get("primary") if isinstance(tag, dict) else tag
        if not label:
            unattributed += 1
            continue
        row = regimes.setdefault(label, {"trades": 0, "gross_realized_pnl": Decimal(0), "fees": Decimal(0), "liquidation_fees": Decimal(0), "funding": Decimal(0)})
        row["trades"] += 1
        row["gross_realized_pnl"] += position.realized_pnl
        row["fees"] += sum((fill.fee for fill in db.scalars(select(Fill).where(Fill.position_id == position.id))), Decimal(0))
        row["funding"] += sum((move.amount for move in db.scalars(select(PaperMovement).where(PaperMovement.position_id == position.id,
                               PaperMovement.kind == "FUNDING"))), Decimal(0))
        row["liquidation_fees"] -= sum((move.amount for move in db.scalars(select(PaperMovement).where(PaperMovement.position_id == position.id,
                               PaperMovement.kind == "LIQUIDATION_FEE"))), Decimal(0))
    regime_summary = {"covered_trades": len(closed) - unattributed, "unattributed_trades": unattributed,
                      "regimes": [{"regime": key, "trades": value["trades"], "gross_realized_pnl": str(value["gross_realized_pnl"]),
                                   "fees": str(value["fees"] + value["liquidation_fees"]), "liquidation_fees": str(value["liquidation_fees"]), "funding": str(value["funding"]),
                                   "net_realized_pnl": str(value["gross_realized_pnl"] - value["fees"] - value["liquidation_fees"] + value["funding"])} for key, value in regimes.items()]}
    allocations, unavailable = [], []
    candidates = db.scalars(select(Position).where(Position.account_id == account.id, Position.opened_at < end))
    from tradebot_core import Instrument as CoreInstrument
    for position in candidates:
        rows = list(db.execute(select(Fill, Order).join(Order, Fill.order_id == Order.id).where(Fill.position_id == position.id,
                               Order.account_id == account.id, Order.mode == "PAPER", Fill.created_at < end)))
        quantity = sum(((-fill.quantity if order.reduce_only else fill.quantity) for fill, order in rows), Decimal(0))
        if quantity <= 0:
            if quantity < 0:
                unavailable.append(position.id)
            continue
        try:
            state = db.get(PaperPositionState, position.id)
            strategy_version = db.scalar(select(StrategyVersion).where(StrategyVersion.strategy_id == position.strategy_id,
                                           StrategyVersion.version == state.strategy_version)) if state else None
            if strategy_version is None:
                raise ValueError("strategy version unavailable")
            timeframe = int(strategy_version.config["timeframe_seconds"])
            mark = db.scalar(select(Candle).where(Candle.symbol == position.symbol, Candle.timeframe_seconds == timeframe,
                              Candle.timestamp <= end - timedelta(seconds=timeframe)).order_by(Candle.timestamp.desc()).limit(1))
            if mark is None:
                raise ValueError("historical mark unavailable")
            instrument = CoreInstrument.model_validate(position.instrument_snapshot)
            margin = instrument.margin(mark.close, quantity, Decimal(str(strategy_version.config["max_leverage"])))
            allocations.append({"position_id": position.id, "strategy_id": position.strategy_id, "strategy_version": state.strategy_version,
                                "symbol": position.symbol, "side": position.side, "quantity": str(quantity), "margin": str(margin),
                                "notional": str(instrument.notional(mark.close, quantity)), "mark_price": str(mark.close),
                                "mark_candle_at": aware(mark.timestamp).isoformat()})
        except (ValueError, KeyError, ArithmeticError):
            unavailable.append(position.id)
    allocation = {"as_of": end.isoformat(), "method": "PAPER quantities reconstructed from persisted fills, immutable instrument/version and the last closed historical candle",
                  "positions": allocations, "margin_used": str(sum((Decimal(row["margin"]) for row in allocations), Decimal(0))),
                  "unavailable_position_ids": unavailable}
    return {"execution_quality": quality, "market_regime_performance": regime_summary, "capital_allocation": allocation}


def build_report(db: Session, account: PaperAccount, period: str, start: datetime, end: datetime) -> dict:
    snapshot_query = select(EquitySnapshot).where(EquitySnapshot.account_id == account.id, EquitySnapshot.created_at >= start,
                                                EquitySnapshot.created_at < end)
    snapshot_count = db.scalar(select(func.count()).select_from(snapshot_query.subquery()))
    snapshots = db.scalars(snapshot_query.order_by(EquitySnapshot.created_at).execution_options(yield_per=1000))
    curve_stride = max(1, (snapshot_count + 4999) // 5000)
    first = db.scalar(select(EquitySnapshot).where(EquitySnapshot.account_id == account.id, EquitySnapshot.created_at <= start)
                      .order_by(EquitySnapshot.created_at.desc()).limit(1))
    last = db.scalar(snapshot_query.order_by(EquitySnapshot.created_at.desc()).limit(1))
    if first is None and aware(account.created_at) >= start and aware(account.created_at) < end:
        # Explicit initial deposit is a documented opening reference only when account created in window.
        start_equity, start_at = account.initial_balance, aware(account.created_at)
        opening_source = "ACCOUNT_INITIAL_DEPOSIT_WITHIN_PERIOD"
    else:
        start_equity, start_at = (first.equity, aware(first.created_at)) if first else (None, None)
        opening_source = "PERSISTED_EQUITY_SNAPSHOT" if first else "UNAVAILABLE"
    end_equity, end_at = (last.equity, aware(last.created_at)) if last else (None, None)
    fills = list(db.execute(select(Fill, Order).join(Order, Fill.order_id == Order.id).where(Order.account_id == account.id,
                           Order.mode == "PAPER", Fill.created_at >= start, Fill.created_at < end)))
    closed = list(db.scalars(select(Position).where(Position.account_id == account.id, Position.status == "CLOSED",
                                                   Position.closed_at >= start, Position.closed_at < end)))
    trading_fees = sum((fill.fee for fill, order in fills), Decimal(0))
    gross = sum((fill.realized_pnl for fill, order in fills), Decimal(0))
    movements = list(db.scalars(select(PaperMovement).where(PaperMovement.account_id == account.id,
                             PaperMovement.created_at >= start, PaperMovement.created_at < end)))
    funding = sum((row.amount for row in movements if row.kind == "FUNDING"), Decimal(0))
    liquidation_fees = -sum((row.amount for row in movements if row.kind == "LIQUIDATION_FEE"), Decimal(0))
    fees = trading_fees + liquidation_fees
    capital_flows = sum((row.amount for row in movements if row.kind == "RESET"), Decimal(0))
    by_strategy = {}
    for fill, order in fills:
        row = by_strategy.setdefault(order.strategy_id, {"realized_pnl": Decimal(0), "fees": Decimal(0), "liquidation_fees": Decimal(0), "funding": Decimal(0), "fills": 0})
        row["realized_pnl"] += fill.realized_pnl
        row["fees"] += fill.fee
        row["fills"] += 1
    for movement in movements:
        if movement.kind not in ("FUNDING", "LIQUIDATION_FEE"):
            continue
        position = db.get(Position, movement.position_id) if movement.position_id else None
        identifier = position.strategy_id if position else None
        values = by_strategy.setdefault(identifier, {"realized_pnl": Decimal(0), "fees": Decimal(0), "liquidation_fees": Decimal(0), "funding": Decimal(0), "fills": 0})
        if movement.kind == "FUNDING":
            values["funding"] += movement.amount
        else:
            values["liquidation_fees"] -= movement.amount
    trade_net = [position_net_pnl(db, position) for position in closed]
    wins, losses = sum(value > 0 for value in trade_net), sum(value < 0 for value in trade_net)
    gain = sum((value for value in trade_net if value > 0), Decimal(0))
    loss = -sum((value for value in trade_net if value < 0), Decimal(0))
    high = start_equity
    drawdown = None
    normalized_curve = []
    reset_movements = sorted((row for row in movements if row.kind == "RESET"), key=lambda row: aware(row.created_at))
    reset_index, capital_before = 0, Decimal(0)
    for point_index, point in enumerate(snapshots):
        while reset_index < len(reset_movements) and aware(reset_movements[reset_index].created_at) <= aware(point.created_at):
            capital_before += reset_movements[reset_index].amount
            reset_index += 1
        normalized = point.equity - capital_before
        high = max(high, normalized) if high is not None else normalized
        if high > 0:
            drawdown = max(drawdown or Decimal(0), (high - normalized) / high)
        if point_index % curve_stride == 0 or point.id == last.id:
            normalized_curve.append({"timestamp": aware(point.created_at).isoformat(), "equity": str(point.equity),
                                     "cash": str(point.cash), "equity_excluding_period_capital_flows": str(normalized)})
    audits = list(db.scalars(select(Audit).where(Audit.created_at >= start, Audit.created_at < end).order_by(Audit.created_at).limit(1000)))
    incidents = [serialize(row) for row in audits if any(term in row.action for term in ("RISK", "RECONCIL", "EMERGENCY", "FAILED", "ERROR"))]
    contributions = []
    for identifier, values in by_strategy.items():
        strategy = db.get(Strategy, identifier) if identifier else None
        contributions.append({"strategy_id": identifier, "strategy_name": strategy.name if strategy else "Unassigned",
                              "gross_realized_pnl": str(values["realized_pnl"]), "fees": str(values["fees"] + values["liquidation_fees"]),
                              "trading_fees": str(values["fees"]), "liquidation_fees": str(values["liquidation_fees"]),
                              "funding": str(values["funding"]), "net_realized_pnl": str(values["realized_pnl"] - values["fees"] - values["liquidation_fees"] + values["funding"]),
                              "net_realized_pnl_excluding_funding": str(values["realized_pnl"] - values["fees"] - values["liquidation_fees"]), "fills": values["fills"]})
    contributions.sort(key=lambda row: Decimal(row["net_realized_pnl"]), reverse=True)
    coverage = []
    if first is None:
        coverage.append("No equity snapshot at or before the period opening; opening return is unavailable or uses an in-period initial deposit.")
    if last is None:
        coverage.append("No closing equity snapshot; ending equity and unrealized P&L are unavailable.")
    elif end - aware(last.created_at) > timedelta(minutes=10):
        coverage.append("Closing snapshot predates the boundary by more than ten minutes; return ends at the recorded timestamp.")
    evidence = evidence_analytics(db, account, start, end, closed)
    coverage.extend(["Funding includes only persisted simulated funding movements; unmodeled exchange charges are not inferred.",
                     "Drawdown uses persisted snapshots and can miss movements between samples.",
                     "Entry execution quality covers stored first-fill benchmarks; no exchange latency or later partial-fill benchmark is inferred."])
    if evidence["market_regime_performance"]["unattributed_trades"]:
        coverage.append("Some closed trades have no stored entry regime and are explicitly unattributed.")
    if evidence["capital_allocation"]["unavailable_position_ids"]:
        coverage.append("Some historical allocations cannot be reconstructed from complete fill, version and mark evidence.")
    return_capital_flows = sum((row.amount for row in reset_movements if end_at and aware(row.created_at) <= end_at), Decimal(0))
    net_return = (end_equity - start_equity - return_capital_flows) / start_equity if start_equity is not None and end_equity is not None and start_equity > 0 else None
    return {"schema_version": 2, "mode": "PAPER", "simulated": True, "account_id": account.id, "currency": account.settlement_asset,
            "period": period, "period_start": start.isoformat(), "period_end": end.isoformat(), "timezone": "Asia/Calcutta",
            "starting_equity": _money(start_equity), "ending_equity": _money(end_equity), "opening_equity_source": opening_source,
            "starting_equity_at": start_at.isoformat() if start_at else None, "ending_equity_at": end_at.isoformat() if end_at else None,
            "total_trades": len(closed), "winning_trades": wins, "losing_trades": losses, "break_even_trades": len(closed) - wins - losses,
            "gross_realized_pnl": str(gross), "net_realized_pnl": str(gross - fees + funding), "net_realized_pnl_excluding_funding": str(gross - fees),
            "unrealized_pnl": str(last.equity - last.cash) if last else None, "fees": str(fees), "trading_fees": str(trading_fees), "liquidation_fees": str(liquidation_fees), "funding": str(funding), "capital_flows": str(capital_flows),
            "net_return": _money(net_return), "win_rate": str(Decimal(wins) / len(closed)) if closed else None,
            "profit_factor": str(gain / loss) if loss > 0 else None, "maximum_drawdown": _money(drawdown),
            "strategy_contributions": contributions, "strategy_rankings": contributions,
            "equity_curve": normalized_curve, "equity_snapshot_count": snapshot_count, "equity_curve_sampling_stride": curve_stride,
            "risk_incidents": incidents, "operational_incidents": incidents,
            **evidence,
            "improvement_opportunities": coverage, "data_quality_warnings": coverage,
            "generated_at": utcnow().isoformat()}


def generate_report(db: Session, period: str, value: date) -> PerformanceReport:
    account = db.get(PaperAccount, 1)
    if account is None:
        raise HTTPException(409, "Initialize a paper account before generating reports")
    start, end = period_bounds(period, value)
    # Complete periods become immutable; open periods are available via analytics instead.
    if end > utcnow():
        raise HTTPException(409, "Reports require a completed period; use analytics for the current account")
    existing = db.scalar(select(PerformanceReport).where(PerformanceReport.account_id == account.id, PerformanceReport.mode == "PAPER",
                         PerformanceReport.period == period, PerformanceReport.period_start == start, PerformanceReport.period_end == end))
    if existing:
        return existing
    report = PerformanceReport(account_id=account.id, mode="PAPER", period=period, period_start=start, period_end=end,
                               payload=build_report(db, account, period, start, end))
    db.add(report)
    db.flush()
    notify(db, f"{period.title()} PAPER report ready for {start.astimezone(IST).date()}; currency {account.settlement_asset}")
    return report


def report_tick(db: Session, now: datetime | None = None):
    """Idempotent daily/weekly/monthly completed-period report scheduling."""
    if not db.get(PaperAccount, 1):
        return
    value = aware(now or utcnow()).astimezone(IST).date()
    account = db.get(PaperAccount, 1)
    for period, previous in (("DAILY", value - timedelta(days=1)), ("WEEKLY", value - timedelta(days=value.weekday() + 1)),
                             ("MONTHLY", value.replace(day=1) - timedelta(days=1))):
        if period_bounds(period, previous)[1] > aware(account.created_at):
            generate_report(db, period, previous)


@router.get("")
def reports(limit: int = Query(100, ge=1, le=500), db: Session = Depends(get_db), principal=Depends(identity)):
    return [serialize(row) for row in db.scalars(select(PerformanceReport).order_by(PerformanceReport.created_at.desc()).limit(limit))]


@router.post("", status_code=201)
def create_report(payload: ReportInput, db: Session = Depends(get_db), user=Depends(operator)):
    report = generate_report(db, payload.period, payload.date)
    audit(db, user.username, "REPORT_GENERATED", str(report.id))
    db.commit()
    return serialize(report)


@router.get("/analytics")
def analytics(days: int = Query(30, ge=1, le=3650), db: Session = Depends(get_db), principal=Depends(identity)):
    account = db.get(PaperAccount, 1)
    if not account:
        return {"account": None, "report": None, "mode": "PAPER"}
    now = utcnow()
    result = build_report(db, account, "ROLLING", now - timedelta(days=days), now)
    return {"account": account_dict(db, account), "report": result, "mode": "PAPER",
            "worker_health": [serialize(row) for row in db.scalars(select(WorkerHeartbeat))]}


@router.get("/{identifier}")
def report_detail(identifier: int, db: Session = Depends(get_db), principal=Depends(identity)):
    report = db.get(PerformanceReport, identifier)
    if not report:
        raise HTTPException(404, "Report not found")
    return serialize(report)


@router.get("/{identifier}/export")
def export_report(identifier: int, format: Literal["json", "csv"] = "json", db: Session = Depends(get_db), principal=Depends(identity)):
    report = db.get(PerformanceReport, identifier)
    if not report:
        raise HTTPException(404, "Report not found")
    headers = {"Content-Disposition": f'attachment; filename="tradebot-paper-{report.period.lower()}-{report.id}.{format}"'}
    if format == "json":
        return Response(json.dumps(report.payload, sort_keys=True, indent=2), media_type="application/json", headers=headers)
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(["section", "key", "value"])
    def safe_cell(value):
        text = str(value) if value is not None else ""
        return "'" + text if text.startswith(("=", "+", "-", "@", "\t", "\r")) else text
    for key, value in report.payload.items():
        if not isinstance(value, (list, dict)):
            writer.writerow(["summary", key, safe_cell(value)])
    for index, row in enumerate(report.payload["strategy_contributions"]):
        for key, value in row.items():
            writer.writerow([f"strategy_{index + 1}", key, safe_cell(value)])
    for row in report.payload["equity_curve"]:
        writer.writerow(["equity", row["timestamp"], row["equity"]])
    return Response(stream.getvalue(), media_type="text/csv", headers=headers)
