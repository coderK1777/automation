"""Explicit simulator controls with durable history and attributed funding."""
import hashlib
import uuid
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from tradebot.db import aware, get_db, utcnow
from tradebot.expansion_models import PaperFundingEvent, PaperMovement, PaperPendingEntry
from tradebot.models import EquitySnapshot, Order, PaperAccount, Strategy
from tradebot.paper import lock_account, reconcile, snapshot
from tradebot.paper_expansion import cancel_pending, pending_entries, reservations
from tradebot.repository import account_dict, audit, open_positions, serialize
from tradebot.security import identity, operator

router = APIRouter(prefix="/api/v1/paper", tags=["paper execution"])


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ResetInput(Input):
    confirm: bool
    balance: Decimal = Field(gt=0, le=100000000)
    reason: str = Field(min_length=1, max_length=500)


class FundingInput(Input):
    symbol: str = Field(pattern=r"^[A-Z0-9]{3,32}$")
    timestamp: datetime
    rate: Decimal = Field(gt=-1, lt=1)
    mark_price: Decimal = Field(gt=0)
    source: str = Field(min_length=1, max_length=1000)

    @field_validator("timestamp")
    @classmethod
    def explicit_timezone(cls, value):
        if value.tzinfo is None:
            raise ValueError("Funding timestamp must include its timezone")
        return aware(value)


@router.get("/execution")
def execution(db: Session = Depends(get_db), principal=Depends(identity)):
    margin, risk = reservations(db)
    from tradebot.strategy_risk import strategy_risk_state_output
    return {"mode": "PAPER", "simulated": True, "reserved_margin": str(margin), "reserved_risk": str(risk),
            "pending": [dict(serialize(db.get(Order, entry.order_id)), reservation=serialize(entry)) for entry in pending_entries(db)],
            "strategy_risk_states": strategy_risk_state_output(db, db.get(PaperAccount, 1)),
            "ownership_policy": "One active strategy and position per symbol; opposite owners are rejected.",
            "assumptions": ["Optional whole-candle participation caps are simulated, not observed order-book liquidity.",
                            "Limit orders require penetration beyond adverse costs; a touch does not promise a fill.",
                            "Trailing changes use completed closes and take effect on the next candle.",
                            "Funding is applied only from explicitly attributed events.",
                            "Optional liquidation uses the configured internal isolated linear model, not Delta margin tiers."]}


@router.post("/orders/{identifier}/cancel")
def cancel_order(identifier: str, db: Session = Depends(get_db), user=Depends(operator)):
    lock_account(db)
    pending = db.get(PaperPendingEntry, identifier)
    if pending is None:
        raise HTTPException(409, "Only pending entry intents can be canceled; position protection remains active")
    order = cancel_pending(db, pending)
    audit(db, user.username, "PAPER_ENTRY_CANCEL", identifier)
    db.commit()
    return serialize(order)


@router.post("/account/reset")
def reset_account(payload: ResetInput, db: Session = Depends(get_db), user=Depends(operator)):
    account = lock_account(db)
    if not payload.confirm:
        raise HTTPException(409, "Explicit virtual balance reset confirmation required")
    if open_positions(db) or pending_entries(db) or db.scalar(select(Strategy).where(Strategy.status == "RUNNING").limit(1)):
        raise HTTPException(409, "Stop strategies, cancel pending entries, and close positions before a virtual balance reset")
    if reconcile(db, account):
        raise HTTPException(409, "Reconcile account discrepancies before changing virtual capital")
    movement = PaperMovement(id=uuid.uuid4().hex, account_id=1, kind="RESET", amount=payload.balance-account.cash,
                             details={"previous_cash": str(account.cash), "new_cash": str(payload.balance),
                                      "reason": payload.reason, "actor": user.username, "simulated": True})
    db.add(movement)
    account.cash, account.high_water_equity = payload.balance, payload.balance
    snapshot(db, account)
    audit(db, user.username, "PAPER_BALANCE_RESET", movement.id, movement.details)
    db.commit()
    return {"account": account_dict(db, account), "movement": serialize(movement), "history_preserved": True}


@router.get("/account/history")
def history(limit: int = Query(200, ge=1, le=2000), db: Session = Depends(get_db), principal=Depends(identity)):
    return {"mode": "PAPER", "simulated": True,
            "movements": [serialize(row) for row in db.scalars(select(PaperMovement).order_by(PaperMovement.created_at.desc()).limit(limit))],
            "equity": [serialize(row) for row in db.scalars(select(EquitySnapshot).order_by(EquitySnapshot.created_at.desc()).limit(limit))]}


@router.get("/funding")
def funding_events(db: Session = Depends(get_db), principal=Depends(identity)):
    return [serialize(row) for row in db.scalars(select(PaperFundingEvent).order_by(PaperFundingEvent.timestamp.desc()).limit(1000))]


@router.post("/funding", status_code=201)
def record_funding(payload: FundingInput, db: Session = Depends(get_db), user=Depends(operator)):
    lock_account(db)
    # Applying late events after position reductions needs authoritative
    # quantity-at-funding evidence. Refuse that ambiguity rather than charge
    # the current quantity for a historical observation.
    if payload.timestamp < utcnow():
        raise HTTPException(409, "Schedule attributed funding before its effective time; late events require a quantity-at-time reconciliation workflow")
    identifier = hashlib.sha256(f"{payload.symbol}:{payload.timestamp.isoformat()}".encode()).hexdigest()
    existing = db.get(PaperFundingEvent, identifier)
    if existing:
        if existing.rate != payload.rate or existing.mark_price != payload.mark_price or existing.source != payload.source:
            raise HTTPException(409, "Conflicting funding event requires an explicit revision")
        return serialize(existing)
    event = PaperFundingEvent(id=identifier, **payload.model_dump())
    db.add(event)
    audit(db, user.username, "PAPER_FUNDING_SCHEDULED", identifier, {"source": payload.source, "simulated": True})
    db.commit()
    return serialize(event)
