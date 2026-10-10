"""Authorized event feeds; public events cannot create orders."""
import hashlib
import ipaddress
import json
import os
import socket
from datetime import datetime, timedelta, timezone
from typing import Literal
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from tradebot.db import aware, get_db, utcnow
from tradebot.news_models import BlackoutRule, NewsEvent, NewsSource, NewsSourceObservation
from tradebot.repository import audit, notify, serialize
from tradebot.security import identity, operator

router = APIRouter(prefix="/api/v1/news", tags=["news"])
Category = Literal["ECONOMIC", "CRYPTO", "MAINTENANCE", "SUSPENSION", "FUNDING", "VOLATILITY"]
Impact = Literal["LOW", "MEDIUM", "HIGH"]
IMPACT = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}


class SourceInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    url: str | None = Field(default=None, max_length=1000)
    authorized: bool
    enabled: bool = True
    required_for_trading: bool = False
    stale_after_seconds: int = Field(default=3600, ge=60, le=604800)
    poll_interval_seconds: int = Field(default=300, ge=30, le=86400)

    @field_validator("url")
    @classmethod
    def secure_url(cls, value):
        if value:
            parsed = urlparse(value)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None, 443):
                raise ValueError("Source URL must be HTTPS with no credentials or custom port")
        return value


class EventInput(BaseModel):
    source_id: int = Field(gt=0)
    external_id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=300)
    category: Category
    impact: Impact = "MEDIUM"
    published_at: datetime
    scheduled_at: datetime | None = None
    symbols: list[str] = Field(default_factory=list, max_length=30)
    source_url: str | None = Field(default=None, max_length=1000)
    details: str = Field(default="", max_length=8000)

    @field_validator("published_at", "scheduled_at")
    @classmethod
    def timezone_required(cls, value):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("Event timestamps require a timezone")
        return value.astimezone(timezone.utc) if value is not None else None

    @field_validator("source_url")
    @classmethod
    def source_url_is_public_reference(cls, value):
        return SourceInput.secure_url(value)

    @field_validator("symbols")
    @classmethod
    def valid_symbols(cls, values):
        if any(not value.isalnum() or not 1 <= len(value) <= 32 for value in values):
            raise ValueError("Symbols must be alphanumeric")
        return sorted(set(value.upper() for value in values))


class BlackoutInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    categories: list[Category] = Field(min_length=1, max_length=6)
    minimum_impact: Impact = "HIGH"
    before_minutes: int = Field(default=30, ge=0, le=10080)
    after_minutes: int = Field(default=30, ge=0, le=10080)
    symbols: list[str] = Field(default_factory=list, max_length=30)
    enabled: bool = True

    @field_validator("symbols")
    @classmethod
    def valid_symbols(cls, values):
        return EventInput.valid_symbols(values)


def event_digest(value) -> str:
    """Content identity covers corrections, independent of source timestamp representation."""
    canonical = {key: getattr(value, key) for key in ("title", "category", "impact", "symbols", "details", "source_url")}
    canonical["symbols"] = sorted(set(canonical["symbols"]))
    for key in ("published_at", "scheduled_at"):
        stamp = getattr(value, key)
        canonical[key] = aware(stamp).astimezone(timezone.utc).isoformat() if stamp is not None else None
    return hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()


def record_event(db: Session, payload: EventInput, *, record_observation: bool = True) -> tuple[NewsEvent, bool]:
    # A source-row lock serializes initial imports and revisions even when the
    # first event has no existing row to lock. Revisions never replace history.
    source = db.scalar(select(NewsSource).where(NewsSource.id == payload.source_id).with_for_update())
    if not source or not source.enabled:
        raise HTTPException(409, "Enabled operator-authorized source required")
    now = utcnow()
    if aware(payload.published_at) > now + timedelta(minutes=5):
        raise HTTPException(422, "Publication timestamp is in the future")
    digest = event_digest(payload)
    existing = db.scalar(select(NewsEvent).where(NewsEvent.source_id == payload.source_id,
                         NewsEvent.external_id == payload.external_id).order_by(NewsEvent.revision.desc()).limit(1))
    source.last_success_at, source.last_error = now, None
    if record_observation:
        db.add(NewsSourceObservation(source_id=source.id, observed_at=now, status="SUCCESS", event_count=1))
    if existing and event_digest(existing) == digest:
        return existing, False
    revision = existing.revision + 1 if existing else 1
    if existing:
        existing.superseded_at = now
    # Equivalent stories retain each publisher's attribution. They do not
    # generate duplicate alerts; source/external-ID repeats reuse their row.
    equivalent = db.scalar(select(NewsEvent).where(NewsEvent.content_hash == digest, NewsEvent.superseded_at.is_(None)).limit(1))
    event = NewsEvent(**payload.model_dump(), content_hash=digest, revision=revision, received_at=now)
    db.add(event)
    if existing or not equivalent:
        prefix = "Corrected" if existing else "Attributed"
        notify(db, f"{prefix} {payload.impact.lower()}-impact {payload.category.lower()} event: {payload.title}", "WARNING" if payload.impact == "HIGH" else "INFO")
    db.flush()
    return event, True


def entry_allowed(db: Session, symbol: str, at: datetime | None = None) -> dict:
    """Only events known at the decision timestamp may block that entry."""
    now = aware(at or utcnow())
    reasons = []
    sources = list(db.scalars(select(NewsSource).where(NewsSource.enabled.is_(True))))
    enabled_ids = {source.id for source in sources}
    for source in sources:
        last_observed = db.scalar(select(NewsSourceObservation.observed_at).where(NewsSourceObservation.source_id == source.id,
                                  NewsSourceObservation.status == "SUCCESS", NewsSourceObservation.observed_at <= now)
                                  .order_by(NewsSourceObservation.observed_at.desc()).limit(1))
        if source.required_for_trading and (not last_observed or (now - aware(last_observed)).total_seconds() > source.stale_after_seconds):
            reasons.append({"kind": "STALE_REQUIRED_SOURCE", "source_id": source.id, "source": source.name})
    rules = list(db.scalars(select(BlackoutRule).where(BlackoutRule.enabled.is_(True))))
    if rules:
        horizon = max(max(rule.before_minutes, rule.after_minutes) for rule in rules)
        events = db.scalars(select(NewsEvent).where(NewsEvent.published_at <= now, NewsEvent.received_at <= now,
                          NewsEvent.published_at >= now - timedelta(days=365),
                          or_(NewsEvent.superseded_at.is_(None), NewsEvent.superseded_at > now)))
        for event in events:
            if event.source_id not in enabled_ids or (event.symbols and symbol not in event.symbols):
                continue
            timestamp = aware(event.scheduled_at or event.published_at)
            if abs((timestamp - now).total_seconds()) > horizon * 60:
                continue
            for rule in rules:
                if (rule.symbols and symbol not in rule.symbols) or event.category not in rule.categories or IMPACT[event.impact] < IMPACT[rule.minimum_impact]:
                    continue
                if timestamp - timedelta(minutes=rule.before_minutes) <= now <= timestamp + timedelta(minutes=rule.after_minutes):
                    reasons.append({"kind": "EVENT_BLACKOUT", "event_id": event.id, "event_revision": event.revision, "rule_id": rule.id, "title": event.title,
                                    "starts_at": (timestamp - timedelta(minutes=rule.before_minutes)).isoformat(),
                                    "ends_at": (timestamp + timedelta(minutes=rule.after_minutes)).isoformat()})
    return {"allowed": not reasons, "symbol": symbol, "at": now.isoformat(), "reasons": reasons}


@router.get("/sources")
def sources(db: Session = Depends(get_db), principal=Depends(identity)):
    return [serialize(row) for row in db.scalars(select(NewsSource).order_by(NewsSource.id))]


@router.post("/sources", status_code=201)
def create_source(payload: SourceInput, db: Session = Depends(get_db), user=Depends(operator)):
    if not payload.authorized:
        raise HTTPException(422, "Confirm that this source is authorized for use")
    if db.scalar(select(NewsSource).where(NewsSource.name == payload.name)):
        raise HTTPException(409, "Source name already exists")
    source = NewsSource(**payload.model_dump(exclude={"authorized"}), authorized_by=user.username)
    db.add(source)
    db.flush()
    audit(db, user.username, "NEWS_SOURCE_AUTHORIZED", str(source.id), {"name": source.name})
    db.commit()
    return serialize(source)


@router.put("/sources/{identifier}")
def update_source(identifier: int, payload: SourceInput, db: Session = Depends(get_db), user=Depends(operator)):
    source = db.get(NewsSource, identifier)
    if not source:
        raise HTTPException(404, "Source not found")
    if not payload.authorized:
        raise HTTPException(422, "Source authorization required")
    for key, value in payload.model_dump(exclude={"authorized"}).items():
        setattr(source, key, value)
    source.authorized_by = user.username
    audit(db, user.username, "NEWS_SOURCE_UPDATED", str(identifier))
    db.commit()
    return serialize(source)


@router.get("/events")
def events(limit: int = Query(100, ge=1, le=500), include_history: bool = False, db: Session = Depends(get_db), principal=Depends(identity)):
    query = select(NewsEvent)
    if not include_history:
        query = query.where(NewsEvent.superseded_at.is_(None))
    return [serialize(row) for row in db.scalars(query.order_by(NewsEvent.published_at.desc(), NewsEvent.id.desc()).limit(limit))]


@router.get("/events/{identifier}/history")
def event_history(identifier: int, db: Session = Depends(get_db), principal=Depends(identity)):
    event = db.get(NewsEvent, identifier)
    if not event:
        raise HTTPException(404, "Event not found")
    return [serialize(row) for row in db.scalars(select(NewsEvent).where(NewsEvent.source_id == event.source_id,
                       NewsEvent.external_id == event.external_id).order_by(NewsEvent.revision))]


@router.post("/events", status_code=201)
def create_event(payload: EventInput, db: Session = Depends(get_db), user=Depends(operator)):
    event, created = record_event(db, payload)
    audit(db, user.username, "NEWS_EVENT_IMPORTED", str(event.id), {"deduplicated": not created, "revision": event.revision})
    db.commit()
    return {"event": serialize(event), "deduplicated": not created}


@router.get("/blackouts")
def blackouts(db: Session = Depends(get_db), principal=Depends(identity)):
    return [serialize(row) for row in db.scalars(select(BlackoutRule).order_by(BlackoutRule.id))]


@router.post("/blackouts", status_code=201)
def create_blackout(payload: BlackoutInput, db: Session = Depends(get_db), user=Depends(operator)):
    rule = BlackoutRule(**payload.model_dump())
    db.add(rule)
    db.flush()
    audit(db, user.username, "NEWS_BLACKOUT_CREATED", str(rule.id))
    db.commit()
    return serialize(rule)


@router.put("/blackouts/{identifier}")
def update_blackout(identifier: int, payload: BlackoutInput, db: Session = Depends(get_db), user=Depends(operator)):
    rule = db.get(BlackoutRule, identifier)
    if not rule:
        raise HTTPException(404, "Blackout not found")
    for key, value in payload.model_dump().items():
        setattr(rule, key, value)
    audit(db, user.username, "NEWS_BLACKOUT_UPDATED", str(identifier))
    db.commit()
    return serialize(rule)


@router.get("/entry-check")
def check_entry(symbol: str = Query("BTCUSD", max_length=32), db: Session = Depends(get_db), principal=Depends(identity)):
    return entry_allowed(db, symbol.upper())


def _authorized_public_url(url: str):
    parsed = urlparse(url)
    allowed = {value.strip().lower() for value in os.getenv("NEWS_ALLOWED_HOSTS", "").split(",") if value.strip()}
    if parsed.hostname not in allowed:
        raise ValueError("News host requires NEWS_ALLOWED_HOSTS authorization")
    addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ValueError("News host must resolve only to public addresses")


async def poll_source(db: Session, source: NewsSource, transport=None):
    """Optional bounded JSON adapter. Disabled without an operator URL + host allowlist."""
    if not source.enabled or not source.url:
        return
    source.last_attempt_at = utcnow()
    try:
        _authorized_public_url(source.url)
        async with httpx.AsyncClient(timeout=10, follow_redirects=False, transport=transport) as client:
            async with client.stream("GET", source.url) as response:
                response.raise_for_status()
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > 512_000:
                        raise ValueError("News response exceeds limit")
        data = json.loads(body)
        values = data.get("events") if isinstance(data, dict) else data
        if not isinstance(values, list) or len(values) > 100:
            raise ValueError("News adapter requires at most 100 attributed JSON events")
        # All payloads validate before any data is persisted.
        payloads = [EventInput.model_validate({**value, "source_id": source.id}) for value in values]
        with db.begin_nested():
            for payload in payloads:
                record_event(db, payload, record_observation=False)
            source.last_success_at, source.last_error = utcnow(), None
            db.add(NewsSourceObservation(source_id=source.id, observed_at=source.last_success_at, status="SUCCESS", event_count=len(payloads)))
    except Exception:
        # Do not save provider payloads/URLs/tokens into logs.
        source.last_error = "Authorized feed polling failed; inspect provider schema/connectivity"
        db.add(NewsSourceObservation(source_id=source.id, status="ERROR", event_count=0))


async def news_tick(db: Session):
    """Poll only explicitly authorized, configured feeds at their bounded interval."""
    now = utcnow()
    for source in db.scalars(select(NewsSource).where(NewsSource.enabled.is_(True), NewsSource.url.is_not(None))):
        if source.last_attempt_at is None or (now - aware(source.last_attempt_at)).total_seconds() >= source.poll_interval_seconds:
            await poll_source(db, source)
