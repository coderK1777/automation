import asyncio
import hashlib
import json
import os
from datetime import timedelta
from decimal import Decimal

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import StreamingResponse
from starlette.responses import JSONResponse
from pydantic import ValidationError
from redis import Redis
from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from tradebot import market
from tradebot.backtesting import run_backtest
from tradebot.config import get_settings
from tradebot.db import aware, get_db, session_factory, utcnow
from tradebot.models import Approval, Audit, AuthSession, Backtest, Candle, EquitySnapshot, Fill, Instrument, LoginAttempt, Notification, Order, PaperAccount, Position, Strategy, StrategyVersion, User, WorkerHeartbeat
from tradebot.paper import PaperRejected, close_position, core_instrument, lock_account, reconcile, snapshot
from tradebot.outputs import AccountOutput, AuditOutput, AuthOutput, BacktestOutput, CandleOutput, DashboardOutput, NotificationOutput, OrderOutput, PositionOutput, RiskOutput, StrategyOutput
from tradebot.repository import account_dict, audit, candle_rows, notify, open_positions, position_net_pnl, position_timeframe, risk_config, serialize, strategy_dict, version
from tradebot.schemas import AccountInput, BacktestInput, EmergencyInput, LoginInput, MarketImportInput, RiskInput, SettingsInput, StrategyInput, StrategyUpdate
from tradebot.security import identity, make_session, operator, password_hasher, same_origin

app = FastAPI(title="TradeBot Pro API", version="0.2.0", description="Delta India research, persisted PAPER trading, and separately authorized demo execution. Live execution is disabled.")
PREFIX = "/api/v1"


@app.exception_handler(RequestValidationError)
async def safe_validation_error(request, exception):
    from fastapi.responses import JSONResponse
    # Pydantic's default input echo can reveal a rejected password or token.
    errors = [{key: value for key, value in error.items() if key in ("loc", "msg", "type")} for error in exception.errors()]
    return JSONResponse(status_code=422, content={"detail": errors})


@app.middleware("http")
async def security_headers(request: Request, call_next):
    if request.method in ("POST", "PUT", "PATCH"):
        maximum = 1024 * 1024
        size = request.headers.get("content-length")
        if size and (not size.isdigit() or int(size) > maximum):
            return JSONResponse(status_code=413, content={"detail": "Request body exceeds 1 MiB limit"})
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > maximum:
                return JSONResponse(status_code=413, content={"detail": "Request body exceeds 1 MiB limit"})
        # Starlette BaseHTTPMiddleware replays a cached body downstream.
        request._body = bytes(body)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(PaperRejected)
async def paper_error(request, exception):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=409, content={"detail": str(exception)})


def required(db: Session, model, identifier):
    row = db.get(model, identifier)
    if row is None:
        raise HTTPException(404, "Record not found")
    return row


def parse_config(name, value, revision):
    from tradebot_core import StrategyConfig
    try:
        return StrategyConfig.model_validate({**value, "name": name, "version": revision}).model_dump(mode="json")
    except ValidationError as error:
        errors = [{key: value for key, value in item.items() if key in ("loc", "msg", "type")} for item in error.errors()]
        raise HTTPException(422, errors) from error


@app.post(PREFIX + "/auth/login", response_model=AuthOutput)
def login(payload: LoginInput, request: Request, response: Response, db: Session = Depends(get_db)):
    same_origin(request)
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(447424332332)"))
    source = (request.client.host if request.client else "unknown")[:100]
    since = utcnow() - timedelta(minutes=15)
    buckets = [("ip:" + source, 10), ("user:" + hashlib.sha256(payload.username.lower().encode()).hexdigest(), 10), ("global", 120)]
    for bucket, limit in buckets:
        attempts = db.scalar(select(func.count()).select_from(LoginAttempt).where(LoginAttempt.source == bucket, LoginAttempt.created_at >= since))
        if attempts >= limit:
            raise HTTPException(429, "Login rate limit exceeded; retry after 15 minutes")
    db.execute(delete(LoginAttempt).where(LoginAttempt.created_at < since))
    for bucket, _ in buckets:
        db.add(LoginAttempt(source=bucket))
    db.commit()
    user = db.scalar(select(User).where(User.username == payload.username))
    valid = False
    if user and user.active:
        try:
            valid = password_hasher.verify(payload.password, user.password_hash)
        except Exception:
            valid = False
    if valid:
        from tradebot.security_admin import verify_login_mfa
        valid = verify_login_mfa(db, user, payload.code, payload.recovery_code)
    if not valid:
        audit(db, "anonymous", "LOGIN_FAILED", "session")
        db.commit()
        raise HTTPException(401, "Invalid credentials")
    token, auth = make_session(db, user)
    audit(db, user.username, "LOGIN", "session")
    db.commit()
    response.set_cookie("tradebot_session", token, max_age=get_settings().session_ttl_seconds,
                        httponly=True, secure=get_settings().session_cookie_secure, samesite="strict", path="/")
    return {"username": user.username, "role": user.role, "csrf_token": auth.csrf_token}


@app.get(PREFIX + "/auth/me", response_model=AuthOutput)
def me(principal=Depends(identity)):
    user, auth = principal
    return {"username": user.username, "role": user.role, "csrf_token": auth.csrf_token}


@app.post(PREFIX + "/auth/logout")
def logout(request: Request, response: Response, principal=Depends(identity), db: Session = Depends(get_db)):
    same_origin(request)
    user, auth = principal
    if request.headers.get("x-csrf-token") != auth.csrf_token:
        raise HTTPException(403, "CSRF token required")
    db.delete(auth)
    audit(db, user.username, "LOGOUT", "session")
    db.commit()
    response.delete_cookie("tradebot_session", path="/")
    return {"ok": True}


@app.get(PREFIX + "/health")
def health():
    return {"status": "ok", "mode": "PAPER", "live_enabled": False, "version": "0.1.0"}


def dependency_health(db):
    statuses = {}
    try:
        db.execute(text("SELECT 1"))
        statuses["database"] = "ok"
    except Exception:
        db.rollback()
        statuses["database"] = "unavailable"
    try:
        Redis.from_url(get_settings().redis_url, socket_connect_timeout=2, socket_timeout=2).ping()
        statuses["redis"] = "ok"
    except Exception:
        statuses["redis"] = "unavailable"
    return statuses


@app.get(PREFIX + "/health/ready", include_in_schema=False)
@app.get(PREFIX + "/ready")
def ready(response: Response, db: Session = Depends(get_db)):
    statuses = dependency_health(db)
    good = all(value == "ok" for value in statuses.values())
    if not good:
        response.status_code = 503
    return {"status": "ready" if good else "degraded", **statuses, "trading_ready": False}


def worker_status(db):
    rows = list(db.scalars(select(WorkerHeartbeat).where(~WorkerHeartbeat.id.like("aux:%")).order_by(WorkerHeartbeat.updated_at.desc())))
    workers = [dict(serialize(row), stale=(utcnow() - aware(row.updated_at)).total_seconds() > 90) for row in rows]
    return {"status": "healthy" if any(not row["stale"] and row["status"] == "RUNNING" for row in workers) else "degraded", "workers": workers}


@app.get(PREFIX + "/worker/health")
def worker_health(db: Session = Depends(get_db), principal=Depends(identity)):
    return worker_status(db)


@app.get(PREFIX + "/system-health")
def system_health(db: Session = Depends(get_db), principal=Depends(identity)):
    dependencies = dependency_health(db)
    workers = worker_status(db) if dependencies["database"] == "ok" else {"status": "unavailable", "workers": []}
    return {"status": "healthy" if all(value == "ok" for value in dependencies.values()) and workers["status"] == "healthy" else "degraded",
            **dependencies, "worker": workers, "mode": "PAPER", "live_enabled": False, "trading_ready": False, "updated_at": utcnow().isoformat()}


@app.get(PREFIX + "/dashboard", response_model=DashboardOutput)
def dashboard(db: Session = Depends(get_db), principal=Depends(identity)):
    account = db.get(PaperAccount, 1)
    account_data = account_dict(db, account)
    positions = open_positions(db)
    recent = list(db.scalars(select(Order).order_by(Order.created_at.desc()).limit(10)))
    closed = list(db.scalars(select(Position).where(Position.status == "CLOSED")))
    equity = list(db.scalars(select(EquitySnapshot).order_by(EquitySnapshot.created_at.desc()).limit(300)))
    strategies = list(db.scalars(select(Strategy).order_by(Strategy.id)))
    latest = db.scalar(select(Candle).order_by(Candle.timestamp.desc()).limit(1))
    wins = sum(1 for position in closed if position_net_pnl(db, position) > 0)
    return {"mode": "PAPER", "live_enabled": False, "simulated": True, "account": account_data,
            "metrics": {"active_bots": sum(s.status == "RUNNING" for s in strategies), "total_trades": len(closed), "open_positions": len(positions),
                        "win_rate": str(Decimal(wins) / len(closed) * 100) if closed else None,
                        "drawdown": str((account.high_water_equity - Decimal(account_data["equity"])) / account.high_water_equity) if account else None},
            "strategies": [strategy_dict(db, strategy) for strategy in strategies],
            "positions": [serialize(row) for row in positions], "recent_orders": [serialize(row) for row in recent],
            "equity_curve": [serialize(row) for row in reversed(equity)], "worker": worker_status(db),
            "market_data": {"latest_candle_at": aware(latest.timestamp).isoformat() if latest else None,
                            "stale": not latest or (utcnow() - aware(latest.timestamp)).total_seconds() > 180}, "updated_at": utcnow().isoformat()}


@app.get(PREFIX + "/strategies", response_model=list[StrategyOutput])
def strategies(db: Session = Depends(get_db), principal=Depends(identity)):
    return [strategy_dict(db, row) for row in db.scalars(select(Strategy).order_by(Strategy.id))]


@app.get(PREFIX + "/signals")
def signals(strategy_id: int | None = Query(None, gt=0), limit: int = Query(200, ge=1, le=500),
            db: Session = Depends(get_db), principal=Depends(identity)):
    from tradebot.models import Signal
    query = select(Signal)
    if strategy_id is not None:
        query = query.where(Signal.strategy_id == strategy_id)
    return [dict(serialize(row), mode="PAPER") for row in db.scalars(query.order_by(Signal.created_at.desc()).limit(limit))]


@app.get(PREFIX + "/risk/decisions")
def risk_decisions(strategy_id: int | None = Query(None, gt=0), limit: int = Query(200, ge=1, le=500),
                   db: Session = Depends(get_db), principal=Depends(identity)):
    from tradebot.expansion_models import PaperRiskDecision
    query = select(PaperRiskDecision)
    if strategy_id is not None:
        query = query.where(PaperRiskDecision.strategy_id == strategy_id)
    return [dict(serialize(row), mode="PAPER") for row in db.scalars(query.order_by(PaperRiskDecision.created_at.desc()).limit(limit))]


@app.post(PREFIX + "/strategies", status_code=201, response_model=StrategyOutput)
def create_strategy(payload: StrategyInput, db: Session = Depends(get_db), user=Depends(operator)):
    config = parse_config(payload.name, payload.config, 1)
    strategy = Strategy(name=payload.name, description=payload.description)
    db.add(strategy)
    db.flush()
    db.add(StrategyVersion(strategy_id=strategy.id, version=1, config=config))
    audit(db, user.username, "STRATEGY_CREATE", str(strategy.id))
    db.commit()
    return strategy_dict(db, strategy)


@app.get(PREFIX + "/strategies/{strategy_id}", response_model=StrategyOutput)
def get_strategy(strategy_id: int, db: Session = Depends(get_db), principal=Depends(identity)):
    return strategy_dict(db, required(db, Strategy, strategy_id))


@app.get(PREFIX + "/strategies/{strategy_id}/versions")
def get_versions(strategy_id: int, db: Session = Depends(get_db), principal=Depends(identity)):
    required(db, Strategy, strategy_id)
    return [serialize(row) for row in db.scalars(select(StrategyVersion).where(StrategyVersion.strategy_id == strategy_id).order_by(StrategyVersion.version))]


@app.put(PREFIX + "/strategies/{strategy_id}", response_model=StrategyOutput)
def update_strategy(strategy_id: int, payload: StrategyUpdate, db: Session = Depends(get_db), user=Depends(operator)):
    if db.get(PaperAccount, 1):
        lock_account(db)
    strategy = required(db, Strategy, strategy_id)
    if strategy.status == "RUNNING" or any(row.strategy_id == strategy_id for row in open_positions(db)):
        raise HTTPException(409, "Pause the strategy and close its managed positions before editing")
    name = payload.name or strategy.name
    old_config = version(db, strategy).config
    strategy.current_version += 1
    config = parse_config(name, payload.config if payload.config is not None else old_config, strategy.current_version)
    strategy.name, strategy.status, strategy.approved_version = name, "DRAFT", None
    if payload.description is not None:
        strategy.description = payload.description
    db.add(StrategyVersion(strategy_id=strategy.id, version=strategy.current_version, config=config))
    audit(db, user.username, "STRATEGY_VERSION", str(strategy.id), {"version": strategy.current_version})
    db.commit()
    return strategy_dict(db, strategy)


@app.post(PREFIX + "/strategies/{strategy_id}/validate", response_model=StrategyOutput)
def validate_strategy(strategy_id: int, db: Session = Depends(get_db), user=Depends(operator)):
    if db.get(PaperAccount, 1):
        lock_account(db)
    strategy = required(db, Strategy, strategy_id)
    if strategy.status == "RUNNING":
        raise HTTPException(409, "Pause the running strategy before validating a version")
    saved = version(db, strategy)
    parse_config(strategy.name, saved.config, strategy.current_version)
    saved.validated, strategy.status = True, "VALIDATED"
    audit(db, user.username, "STRATEGY_VALIDATE", str(strategy.id), {"version": strategy.current_version})
    db.commit()
    return strategy_dict(db, strategy)


@app.post(PREFIX + "/strategies/{strategy_id}/start", response_model=StrategyOutput)
def start_strategy(strategy_id: int, db: Session = Depends(get_db), user=Depends(operator)):
    account = lock_account(db)
    strategy = required(db, Strategy, strategy_id)
    if account.emergency_stop or not risk_config(db).entries_enabled or reconcile(db, account):
        raise HTTPException(409, "Account emergency, risk, or reconciliation gate blocks start")
    saved = version(db, strategy)
    backtest = db.scalar(select(Backtest).where(Backtest.strategy_id == strategy.id, Backtest.version == strategy.current_version, Backtest.status == "COMPLETED").order_by(Backtest.id.desc()))
    if not saved.validated or backtest is None:
        raise HTTPException(409, "Validate and complete a backtest for this exact version before paper approval")
    instrument = db.scalar(select(Instrument).where(Instrument.symbol == saved.config["symbol"]))
    if instrument is None:
        raise HTTPException(409, "Verified instrument required")
    try:
        economics = core_instrument(instrument)
        if economics.settlement_asset != account.settlement_asset:
            raise ValueError("Paper account and instrument settlement currencies differ")
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    for other in db.scalars(select(Strategy).where(Strategy.status == "RUNNING", Strategy.id != strategy.id)):
        if version(db, other).config["symbol"] == saved.config["symbol"]:
            raise HTTPException(409, "Symbol exclusively owned by another running strategy")
    strategy.status, strategy.approved_version, strategy.last_error = "RUNNING", strategy.current_version, None
    db.add(Approval(strategy_id=strategy.id, version=strategy.current_version, stage="PAPER_APPROVED", evidence={"backtest_id": backtest.id, "dataset_hash": backtest.dataset_hash}, actor=user.username))
    audit(db, user.username, "PAPER_START", str(strategy.id), {"version": strategy.current_version, "backtest_id": backtest.id})
    db.commit()
    return strategy_dict(db, strategy)


@app.post(PREFIX + "/strategies/{strategy_id}/pause", response_model=StrategyOutput)
def pause_strategy(strategy_id: int, db: Session = Depends(get_db), user=Depends(operator)):
    lock_account(db)
    strategy = required(db, Strategy, strategy_id)
    strategy.status = "PAUSED"
    audit(db, user.username, "STRATEGY_PAUSE", str(strategy.id))
    db.commit()
    return strategy_dict(db, strategy)


@app.post(PREFIX + "/strategies/{strategy_id}/stop", response_model=StrategyOutput)
def stop_strategy(strategy_id: int, db: Session = Depends(get_db), user=Depends(operator)):
    lock_account(db)
    strategy = required(db, Strategy, strategy_id)
    strategy.status = "STOPPED"
    audit(db, user.username, "STRATEGY_STOP", str(strategy.id), {"positions_closed": False, "protection_retained": True})
    db.commit()
    return strategy_dict(db, strategy)


@app.post(PREFIX + "/backtests", status_code=201, response_model=BacktestOutput)
def create_backtest(payload: BacktestInput, db: Session = Depends(get_db), user=Depends(operator)):
    strategy = required(db, Strategy, payload.strategy_id)
    if strategy.status == "RUNNING":
        raise HTTPException(409, "Pause strategy before running a new historical evaluation")
    try:
        run = run_backtest(db, strategy, payload.initial_capital, payload.limit,
                           dataset_id=payload.dataset_id, fee_rate=payload.fee_rate,
                           spread_bps=payload.spread_bps, slippage_bps=payload.slippage_bps,
                           funding_events=payload.funding_events)
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    audit(db, user.username, "BACKTEST", str(run.id))
    db.commit()
    return serialize(run)


@app.post(PREFIX + "/strategies/{strategy_id}/backtest", status_code=201, response_model=BacktestOutput)
def strategy_backtest(strategy_id: int, db: Session = Depends(get_db), user=Depends(operator)):
    return create_backtest(BacktestInput(strategy_id=strategy_id), db, user)


@app.get(PREFIX + "/backtests", response_model=list[BacktestOutput])
def backtests(db: Session = Depends(get_db), principal=Depends(identity)):
    return [serialize(row) for row in db.scalars(select(Backtest).order_by(Backtest.id.desc()).limit(200))]


@app.get(PREFIX + "/backtests/{backtest_id}", response_model=BacktestOutput)
def backtest_detail(backtest_id: int, db: Session = Depends(get_db), principal=Depends(identity)):
    return serialize(required(db, Backtest, backtest_id))


@app.get(PREFIX + "/paper/account", response_model=AccountOutput | None)
def get_account(db: Session = Depends(get_db), principal=Depends(identity)):
    return account_dict(db, db.get(PaperAccount, 1))


@app.post(PREFIX + "/paper/account", status_code=201, response_model=AccountOutput)
def initialize_account(payload: AccountInput, db: Session = Depends(get_db), user=Depends(operator)):
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(447424332333)"))
    if db.get(PaperAccount, 1):
        raise HTTPException(409, "Existing paper account is durable; reset is not supported")
    account = PaperAccount(id=1, mode="PAPER", settlement_asset=payload.settlement_asset, initial_balance=payload.initial_balance,
                           cash=payload.initial_balance, high_water_equity=payload.initial_balance,
                           planning_capital_inr=payload.planning_capital_inr, usd_inr_rate=payload.usd_inr_rate)
    db.add(account)
    risk_config(db)
    db.flush()
    snapshot(db, account)
    audit(db, user.username, "PAPER_INITIALIZE", "account", {"settlement_asset": payload.settlement_asset, "simulated": True})
    db.commit()
    return account_dict(db, account)


@app.get(PREFIX + "/positions", response_model=list[PositionOutput])
def positions(status: str | None = None, db: Session = Depends(get_db), principal=Depends(identity)):
    query = select(Position).order_by(Position.opened_at.desc()).limit(500)
    if status:
        query = query.where(Position.status == status)
    return [serialize(row) for row in db.scalars(query)]


@app.post(PREFIX + "/positions/{position_id}/close", response_model=PositionOutput)
def close(position_id: str, db: Session = Depends(get_db), user=Depends(operator)):
    lock_account(db)
    position = required(db, Position, position_id)
    rows = candle_rows(db, position.symbol, position_timeframe(db, position), 1)
    if not rows or (utcnow() - aware(rows[-1].timestamp) - timedelta(seconds=rows[-1].timeframe_seconds)).total_seconds() > risk_config(db).stale_after_seconds:
        raise HTTPException(409, "Fresh market data required for simulated closure")
    close_position(db, position, rows[-1].close)
    audit(db, user.username, "PAPER_POSITION_CLOSE", position.id)
    db.commit()
    return serialize(position)


@app.get(PREFIX + "/orders", response_model=list[OrderOutput])
def orders(db: Session = Depends(get_db), principal=Depends(identity)):
    return [serialize(row) for row in db.scalars(select(Order).order_by(Order.created_at.desc()).limit(500))]


@app.get(PREFIX + "/fills")
def fills(db: Session = Depends(get_db), principal=Depends(identity)):
    return [serialize(row) for row in db.scalars(select(Fill).order_by(Fill.created_at.desc()).limit(500))]


@app.get(PREFIX + "/risk", response_model=RiskOutput)
def risk(db: Session = Depends(get_db), principal=Depends(identity)):
    value = risk_config(db)
    db.commit()
    account = db.get(PaperAccount, 1)
    return dict(serialize(value), emergency_stop=account.emergency_stop if account else False, reconciliation_required=account.reconciliation_required if account else True)


@app.put(PREFIX + "/risk", response_model=RiskOutput)
def update_risk(payload: RiskInput, db: Session = Depends(get_db), user=Depends(operator)):
    lock_account(db)
    if payload.max_total_risk < payload.max_risk_per_trade:
        raise HTTPException(422, "Portfolio risk must cover per-trade risk")
    value = risk_config(db)
    old = serialize(value)
    for key, item in payload.model_dump().items():
        setattr(value, key, item)
    audit(db, user.username, "RISK_UPDATE", "risk", {"before": old, "after": payload.model_dump(mode="json")})
    db.commit()
    return risk(db, None)


@app.post(PREFIX + "/emergency-stop")
def emergency(payload: EmergencyInput, db: Session = Depends(get_db), user=Depends(operator)):
    from tradebot.paper_expansion import cancel_all_entries
    account = lock_account(db)
    if payload.action == "RESUME_ENTRIES":
        if not payload.confirm or reconcile(db, account):
            raise HTTPException(409, "Explicit confirmation and successful reconciliation required")
        account.emergency_stop = False
        risk_config(db).entries_enabled = True
    elif payload.action == "BLOCK_ENTRIES":
        account.emergency_stop = True
        risk_config(db).entries_enabled = False
        cancel_all_entries(db, "Emergency entry block")
        for strategy in db.scalars(select(Strategy).where(Strategy.status == "RUNNING")):
            strategy.status = "PAUSED"
    else:
        if not payload.confirm:
            raise HTTPException(409, "Explicit flatten confirmation required")
        managed = open_positions(db)
        # Validate every closure first: a stale symbol must not result in a
        # silently partial flatten. Transactions preserve all-or-none state.
        for position in managed:
            strategy = required(db, Strategy, position.strategy_id)
            bars = candle_rows(db, position.symbol, position_timeframe(db, position), 1)
            if not bars or (utcnow() - aware(bars[-1].timestamp) - timedelta(seconds=bars[-1].timeframe_seconds)).total_seconds() > risk_config(db).stale_after_seconds:
                raise HTTPException(409, f"Fresh market data required to flatten {position.symbol}")
        account.emergency_stop, risk_config(db).entries_enabled = True, False
        cancel_all_entries(db, "Emergency flatten")
        for position in managed:
            strategy = required(db, Strategy, position.strategy_id)
            bars = candle_rows(db, position.symbol, position_timeframe(db, position), 1)
            close_position(db, position, bars[-1].close, "EMERGENCY_FLATTEN")
        for strategy in db.scalars(select(Strategy).where(Strategy.status == "RUNNING")):
            strategy.status = "PAUSED"
    audit(db, user.username, payload.action, "account", {"simulated": True})
    notify(db, f"Emergency action: {payload.action}", "WARNING")
    db.commit()
    return {"action": payload.action, "emergency_stop": account.emergency_stop, "open_positions": len(open_positions(db)), "simulated": True}


@app.get(PREFIX + "/instruments")
def instruments(db: Session = Depends(get_db), principal=Depends(identity)):
    values = []
    for row in db.scalars(select(Instrument).order_by(Instrument.symbol)):
        value = dict(serialize(row), **row.metadata_json)
        value["paper_model"] = {
            "enabled": bool(row.metadata_json.get("linear_economics_verified")) or row.verified,
            "min_quantity": "1", "quantity_step": "1", "initial_margin_rate": "0.5",
            "description": "Internal PAPER simulation rules. Actual exchange minimum size and margin tiers remain uncertified.",
        }
        values.append(value)
    return values


@app.post(PREFIX + "/instruments/refresh")
async def refresh_instruments(db: Session = Depends(get_db), user=Depends(operator)):
    try:
        count = await market.discover(db)
    except Exception as error:
        raise HTTPException(503, "Delta India public discovery unavailable; inspect worker status") from error
    audit(db, user.username, "INSTRUMENT_DISCOVERY", "market", {"count": count})
    db.commit()
    return {"count": count, "source": "Delta Exchange India production public API"}


@app.get(PREFIX + "/market/candles", response_model=list[CandleOutput])
def candles(symbol: str = "BTCUSD", timeframe_seconds: int = 60, limit: int = Query(default=500, ge=1, le=5000), db: Session = Depends(get_db), principal=Depends(identity)):
    return [serialize(row) for row in candle_rows(db, symbol, timeframe_seconds, limit)]


@app.post(PREFIX + "/market/import")
async def import_market(payload: MarketImportInput, db: Session = Depends(get_db), user=Depends(operator)):
    try:
        count = await market.import_candles(db, payload.symbol, payload.timeframe_seconds, payload.lookback_bars)
    except Exception as error:
        raise HTTPException(503, "Delta India historical data unavailable or invalid; no fabricated candles inserted") from error
    audit(db, user.username, "CANDLE_IMPORT", payload.symbol, {"count": count})
    db.commit()
    return {"imported": count, "symbol": payload.symbol, "timeframe_seconds": payload.timeframe_seconds}


@app.get(PREFIX + "/notifications", response_model=list[NotificationOutput])
def notifications(db: Session = Depends(get_db), principal=Depends(identity)):
    return [serialize(row) for row in db.scalars(select(Notification).order_by(Notification.created_at.desc()).limit(300))]


@app.post(PREFIX + "/notifications/{notification_id}/read")
def read_notification(notification_id: int, db: Session = Depends(get_db), user=Depends(operator)):
    row = required(db, Notification, notification_id)
    row.read = True
    db.commit()
    return serialize(row)


@app.get(PREFIX + "/journal", response_model=list[AuditOutput])
def journal(db: Session = Depends(get_db), principal=Depends(identity)):
    return [serialize(row) for row in db.scalars(select(Audit).order_by(Audit.created_at.desc()).limit(500))]


@app.get(PREFIX + "/integrations")
def integrations(db: Session = Depends(get_db), principal=Depends(identity)):
    from tradebot import demo
    from tradebot.demo_models import DemoAccount
    from tradebot.notifications import metadata
    public_worker = worker_status(db)
    demo_account = db.get(DemoAccount, 1)
    demo_credentials = demo.credential_configured()
    demo_fresh = bool(demo_account and demo_account.worker_updated_at and
                      (utcnow()-aware(demo_account.worker_updated_at)).total_seconds() <= 90)
    telegram = metadata(db)
    return {"exchange": "Delta Exchange India", "public_environment": "production", "public_data": "available" if public_worker["status"] == "healthy" else "unverified_or_unavailable",
            "demo": {"credentials_present": demo_credentials, "status": "credentials_required" if not demo_credentials else "connected" if demo_account and demo_account.connected else "not_verified",
                     "execution_enabled": bool(demo_credentials and demo.execution_authorized() and demo_account and demo_account.connected and demo_account.entries_enabled
                                               and not demo_account.emergency_stop and not demo_account.reconciliation_required and demo_fresh
                                               and demo_account.worker_status == "RUNNING" and demo_account.stream_status == "CONNECTED"),
                     "actual_order_validation": "NOT_CERTIFIED"},
            "live": {"credentials_present": bool(os.getenv("DELTA_PRODUCTION_API_KEY") and os.getenv("DELTA_PRODUCTION_API_SECRET")), "status": "disabled", "execution_enabled": False},
            "telegram": {**telegram, "credentials_present": telegram["token_configured"],
                         "status": "configured" if telegram["token_configured"] else "token_required"}}


@app.get(PREFIX + "/settings")
def settings(db: Session = Depends(get_db), principal=Depends(identity)):
    account = db.get(PaperAccount, 1)
    return {"mode": "PAPER", "live_enabled": False, "planning_capital_inr": str(account.planning_capital_inr) if account else "50000",
            "usd_inr_rate": str(account.usd_inr_rate) if account and account.usd_inr_rate else None,
            "account_initialized": account is not None, "exchange": "Delta Exchange India"}


@app.put(PREFIX + "/settings")
def update_settings(payload: SettingsInput, db: Session = Depends(get_db), user=Depends(operator)):
    account = lock_account(db)
    account.planning_capital_inr, account.usd_inr_rate = payload.planning_capital_inr, payload.usd_inr_rate
    audit(db, user.username, "PLANNING_SETTINGS_UPDATE", "account", payload.model_dump(mode="json"))
    db.commit()
    return settings(db, None)


@app.get(PREFIX + "/live-readiness")
def live_readiness(principal=Depends(identity)):
    return {"live_enabled": False, "status": "BLOCKED", "execution_implemented": False,
            "missing": ["Verified Delta India demo integration", "Demo order lifecycle and reconciliation acceptance tests", "Independent security review", "Operational backup/restore and failover evidence", "Explicit limited-live authorization"],
            "message": "Demo execution is separately gated; live execution requires independent reviews and explicit authorization."}


@app.get(PREFIX + "/events")
async def events(request: Request, principal=Depends(identity)):
    async def stream():
        sequence = 0
        while not await request.is_disconnected() and aware(principal[1].expires_at) > utcnow():
            sequence += 1
            with session_factory()() as db:
                auth = db.get(AuthSession, principal[1].id)
                user = db.get(User, principal[0].id)
                if not auth or not user or not user.active:
                    break
                snapshot_data = dashboard(db, None)
            yield f"id: {sequence}\nevent: snapshot\ndata: {json.dumps(snapshot_data)}\n\n"
            await asyncio.sleep(5)
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})


@app.get("/metrics")
def metrics(db: Session = Depends(get_db)):
    from tradebot.telemetry import metrics_response
    return metrics_response(db)


from tradebot import model_registry  # noqa: E402,F401
from tradebot import clock, data_api, demo_api, market_stream, news, notifications as notification_service, paper_api, reporting, research, research_api, security_admin  # noqa: E402
from tradebot.telemetry import install_api_telemetry, router as telemetry_router  # noqa: E402

for router_module in (clock, data_api, demo_api, market_stream, news, notification_service, paper_api, reporting, research, research_api, security_admin):
    app.include_router(router_module.router)
install_api_telemetry(app)
app.include_router(telemetry_router)
