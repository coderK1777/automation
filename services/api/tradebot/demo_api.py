"""Authenticated controls for the isolated Delta India DEMO account."""
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from tradebot import demo
from tradebot.demo_capabilities import require_limits, require_strategy
from tradebot.db import aware, get_db, utcnow
from tradebot.demo_models import DemoAccount, DemoCashMovement, DemoFill, DemoOrder, DemoOrderEvent, DemoPosition, DemoProduct, DemoStrategy
from tradebot.models import Approval, Audit, Backtest, Strategy, StrategyVersion
from tradebot.security import identity, operator
from tradebot.security_admin import sensitive_operator
from tradebot_core import StrategyConfig
from tradebot_core.risk import RiskLimits

router = APIRouter(prefix="/api/v1/demo", tags=["Delta India DEMO"])


class Confirm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: Literal[True]


class ContractApproval(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    min_quantity: Decimal = Field(gt=0)
    quantity_step: Decimal = Field(gt=0)
    initial_margin_rate: Decimal = Field(gt=0, le=1)
    max_quantity: Decimal = Field(gt=0)
    evidence: str = Field(min_length=20, max_length=4000)


class OrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9:_-]+$")
    symbol: str = Field(pattern=r"^[A-Za-z0-9_.:+-]{1,64}$")
    side: Literal["BUY", "SELL"]
    quantity: int = Field(gt=0, le=100000000, strict=True)
    order_type: Literal["MARKET", "LIMIT"] = "MARKET"
    limit_price: Decimal | None = Field(default=None, gt=0)
    stop_price: Decimal = Field(gt=0)
    target_price: Decimal = Field(gt=0)
    strategy_id: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def valid_limit(self):
        if (self.order_type == "LIMIT") != (self.limit_price is not None):
            raise ValueError("Only LIMIT orders require a limit price")
        return self


def serialize(row):
    result = {}
    for column in row.__table__.columns:
        key = column.name
        if key in ("credential_fingerprint", "owner", "lease_expires_at"):
            continue
        value = getattr(row, key)
        result["metadata" if key == "metadata_json" else key] = str(value) if isinstance(value, Decimal) else value.isoformat() if hasattr(value, "isoformat") else value
    return result


def reject(error: Exception):
    # Deliberately return coordinator error text, never exception repr for raw
    # transports/signing/vault failures which may contain sensitive context.
    if isinstance(error, demo.DemoRejected):
        raise HTTPException(409, str(error)) from None
    raise HTTPException(503, "Demo exchange operation unavailable; execution remains blocked") from None


@router.get("/status")
def status(_=Depends(identity), db: Session = Depends(get_db)):
    account = db.get(DemoAccount, 1)
    worker = {"status": "STOPPED", "updated_at": None, "last_error": None}
    if account:
        fresh = account.worker_updated_at and (utcnow()-aware(account.worker_updated_at)).total_seconds() < 60
        worker = {"status": account.worker_status if fresh else "STALE", "updated_at": account.worker_updated_at.isoformat() if account.worker_updated_at else None, "last_error": account.last_error}
    return {"mode": "DEMO", "credential_configured": demo.credential_configured(), "local_execution_authorized": demo.execution_authorized(),
            "account": serialize(account) if account else None, "worker": worker,
            "actual_private_validation": "CREDENTIAL_AUTHENTICATION_OBSERVED" if account and account.connected else "NOT_RUN_WITHOUT_CREDENTIALS",
            "actual_order_validation": "NOT_CERTIFIED", "production_orders_available": False,
            "credential_configuration": "Secure raw environment variables or encrypted vault CLI; API values are never returned"}


@router.post("/connect")
async def connect(user=Depends(sensitive_operator), db: Session = Depends(get_db)):
    try:
        async with demo.client() as adapter:
            await demo.discover(db, adapter)
            account = await demo.reconcile(db, adapter, connect=True)
        db.add(Audit(actor=user.username, action="demo.connect", entity="demo:1", details={"actual_authenticated_read": True, "mode": "DEMO"}))
        db.commit()
        return serialize(account)
    except Exception as error:
        db.rollback()
        reject(error)


@router.get("/products")
def products(_=Depends(identity), db: Session = Depends(get_db)):
    return [serialize(p) for p in db.scalars(select(DemoProduct).order_by(DemoProduct.symbol))]


@router.post("/products/refresh")
async def refresh(user=Depends(operator), db: Session = Depends(get_db)):
    try:
        # Public discovery works even when credentials are not configured.
        from tradebot_delta import DeltaClient
        async with DeltaClient("demo") as adapter:
            rows = await demo.discover(db, adapter)
        db.commit()
        return [serialize(row) for row in rows]
    except Exception as error:
        db.rollback()
        reject(error)


@router.post("/products/{product_id}/verify")
def verify_product(product_id: int, body: ContractApproval, user=Depends(sensitive_operator), db: Session = Depends(get_db)):
    row = db.get(DemoProduct, product_id)
    if not row:
        raise HTTPException(404, "Demo product not found")
    try:
        demo.approve_contract(db, row, body.model_dump(), user.username)
        db.commit()
        return serialize(row)
    except (ValueError, demo.DemoRejected) as error:
        db.rollback()
        reject(demo.DemoRejected(str(error)))


@router.get("/risk")
def risk(_=Depends(identity), db: Session = Depends(get_db)):
    account = db.get(DemoAccount, 1)
    return RiskLimits.model_validate(account.risk_limits if account else {}).model_dump(mode="json")


@router.put("/risk")
def edit_risk(body: RiskLimits, user=Depends(sensitive_operator), db: Session = Depends(get_db)):
    try:
        require_limits(body)
        account = demo.lock_account(db)
        account.risk_limits = body.model_dump(mode="json")
        db.add(Audit(actor=user.username, action="demo.risk.edit", entity="demo:1", details={"risk_limits": account.risk_limits}))
        db.commit()
        return account.risk_limits
    except demo.DemoRejected as error:
        reject(error)


@router.post("/enable")
def enable(body: Confirm, user=Depends(sensitive_operator), db: Session = Depends(get_db)):
    try:
        account = demo.lock_account(db)
        require_limits(RiskLimits.model_validate(account.risk_limits))
        if not demo.execution_authorized():
            raise demo.DemoRejected("DEMO_EXECUTION_AUTHORIZED must be explicitly enabled in the local environment")
        if account.reconciliation_required or not account.last_reconciled_at or (utcnow()-aware(account.last_reconciled_at)).total_seconds() > 30:
            raise demo.DemoRejected("A fresh reconciled demo account is required")
        if account.stream_status != "CONNECTED" or account.worker_status != "RUNNING" or not account.worker_updated_at or (utcnow()-aware(account.worker_updated_at)).total_seconds() > 60:
            raise demo.DemoRejected("Start the dedicated demo worker and verify authenticated private WebSocket health")
        account.entries_enabled, account.emergency_stop = True, False
        db.add(Audit(actor=user.username, action="demo.entries.enable", entity="demo:1", details={"confirm": body.confirm}))
        db.commit()
        return serialize(account)
    except demo.DemoRejected as error:
        reject(error)


@router.post("/disable")
def disable(user=Depends(operator), db: Session = Depends(get_db)):
    try:
        account = demo.lock_account(db)
        demo.block_entries(db, account, user.username)
        db.add(Audit(actor=user.username, action="demo.entries.disable", entity="demo:1", details={"protection_retained": True}))
        db.commit()
        return serialize(account)
    except demo.DemoRejected as error:
        reject(error)


@router.post("/reconcile")
async def reconcile(user=Depends(operator), db: Session = Depends(get_db)):
    try:
        async with demo.client() as adapter:
            await demo.discover(db, adapter)
            account = await demo.reconcile(db, adapter)
        db.add(Audit(actor=user.username, action="demo.reconcile", entity="demo:1", details={"issues": account.issues}))
        db.commit()
        return serialize(account)
    except Exception as error:
        db.rollback()
        reject(error)


@router.get("/orders")
def orders(_=Depends(identity), db: Session = Depends(get_db)):
    return [serialize(row) for row in db.scalars(select(DemoOrder).order_by(DemoOrder.created_at.desc()).limit(500))]


@router.post("/orders", status_code=201)
def create_order(body: OrderRequest, user=Depends(sensitive_operator), db: Session = Depends(get_db)):
    if body.strategy_id:
        raise HTTPException(422, "Manual orders cannot claim a strategy identity; use the exact-version strategy runner")
    try:
        order = demo.queue_order(db, body.model_dump(), user.username)
        db.commit()
        return serialize(order)
    except (ValueError, demo.DemoRejected) as error:
        db.rollback()
        reject(demo.DemoRejected(str(error)))


@router.post("/orders/{order_id}/cancel")
def cancel_order(order_id: str, user=Depends(operator), db: Session = Depends(get_db)):
    try:
        demo.lock_account(db)
    except demo.DemoRejected as error:
        reject(error)
    order = db.get(DemoOrder, order_id)
    if not order:
        raise HTTPException(404, "Demo order not found")
    try:
        demo.request_cancel(db, order, user.username)
        db.commit()
        return serialize(order)
    except demo.DemoRejected as error:
        reject(error)


@router.get("/positions")
def positions(_=Depends(identity), db: Session = Depends(get_db)):
    return [serialize(row) for row in db.scalars(select(DemoPosition).where(DemoPosition.signed_quantity != 0))]


@router.post("/positions/{product_id}/close", status_code=201)
def close_position(product_id: int, body: Confirm, user=Depends(operator), db: Session = Depends(get_db)):
    try:
        order = demo.queue_close(db, product_id, user.username)
        db.commit()
        return serialize(order)
    except demo.DemoRejected as error:
        reject(error)


@router.get("/fills")
def fills(_=Depends(identity), db: Session = Depends(get_db)):
    return [serialize(row) for row in db.scalars(select(DemoFill).order_by(DemoFill.created_at.desc()).limit(500))]


@router.get("/events")
def events(_=Depends(identity), db: Session = Depends(get_db)):
    return [serialize(row) for row in db.scalars(select(DemoOrderEvent).order_by(DemoOrderEvent.created_at.desc()).limit(500))]


@router.get("/cash-movements")
def cash_movements(_=Depends(identity), db: Session = Depends(get_db)):
    return [serialize(row) for row in db.scalars(select(DemoCashMovement).order_by(DemoCashMovement.exchange_created_at.desc()).limit(500))]


@router.get("/strategies")
def strategy_runs(_=Depends(identity), db: Session = Depends(get_db)):
    return [serialize(row) for row in db.scalars(select(DemoStrategy))]


@router.post("/strategies/{strategy_id}/start")
def start_strategy(strategy_id: int, body: Confirm, user=Depends(sensitive_operator), db: Session = Depends(get_db)):
    try:
        account = demo.lock_account(db)
        if not account.entries_enabled or account.reconciliation_required or not demo.execution_authorized():
            raise demo.DemoRejected("Enable the reconciled demo account before starting a strategy")
        strategy = db.get(Strategy, strategy_id)
        saved = db.scalar(select(StrategyVersion).where(StrategyVersion.strategy_id == strategy_id, StrategyVersion.version == strategy.current_version)) if strategy else None
        tested = db.scalar(select(Backtest).where(Backtest.strategy_id == strategy_id, Backtest.version == strategy.current_version, Backtest.status == "COMPLETED")) if strategy else None
        if not saved or not saved.validated or not tested:
            raise demo.DemoRejected("Validate and backtest the exact current strategy version first")
        config = StrategyConfig.model_validate(saved.config)
        require_strategy(config)
        if config.version != strategy.current_version:
            raise demo.DemoRejected("Saved DEMO strategy configuration must match the exact current version")
        require_limits(RiskLimits.model_validate(account.risk_limits))
        product = db.scalar(select(DemoProduct).where(DemoProduct.symbol == config.symbol))
        if not product:
            raise demo.DemoRejected("The strategy symbol is unavailable on Delta India demo")
        demo.core_contract(product)
        row = db.scalar(select(DemoStrategy).where(DemoStrategy.strategy_id == strategy_id))
        if row is None:
            row = DemoStrategy(strategy_id=strategy_id, version=strategy.current_version)
            db.add(row)
        row.version, row.status, row.last_error = strategy.current_version, "RUNNING", None
        db.add(Approval(strategy_id=strategy_id, version=row.version, stage="DEMO", actor=user.username,
                        evidence={"explicit_confirm": body.confirm, "backtest_id": tested.id, "dataset_hash": tested.dataset_hash,
                                  "demo_product_id": product.id, "contract_evidence": product.evidence, "mode": "DEMO",
                                  "actual_exchange_acceptance": "NOT_CERTIFIED_BY_STRATEGY_START"}))
        db.add(Audit(actor=user.username, action="demo.strategy.start", entity=str(strategy_id), details={"version": row.version, "confirm": body.confirm}))
        db.commit()
        return serialize(row)
    except demo.DemoRejected as error:
        reject(error)


@router.post("/strategies/{strategy_id}/pause")
def pause_strategy(strategy_id: int, user=Depends(operator), db: Session = Depends(get_db)):
    try:
        demo.lock_account(db)
    except demo.DemoRejected as error:
        reject(error)
    row = db.scalar(select(DemoStrategy).where(DemoStrategy.strategy_id == strategy_id))
    if not row:
        raise HTTPException(404, "Demo strategy runner not found")
    row.status = "PAUSED"
    for order in db.scalars(select(DemoOrder).where(DemoOrder.strategy_id == strategy_id, DemoOrder.reduce_only.is_(False), DemoOrder.status.in_(demo.ACTIVE))):
        if order.status not in ("UNKNOWN", "SUBMITTING"):
            demo.request_cancel(db, order, user.username)
    db.add(Audit(actor=user.username, action="demo.strategy.pause", entity=str(strategy_id), details={"protection_retained": True}))
    db.commit()
    return serialize(row)


from tradebot import demo_reporting  # noqa: E402
router.include_router(demo_reporting.router)
