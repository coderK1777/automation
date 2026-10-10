"""Authenticated discovery of versioned, observed historical market data."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from tradebot.data_models import CandleRevision, HistoricalDataset, MarketDataGap
from tradebot.data_provenance import create_dataset, dataset_candles
from tradebot.db import get_db
from tradebot.market import RESOLUTIONS
from tradebot.repository import audit, serialize
from tradebot.security import identity, operator

router = APIRouter(prefix="/api/v1", tags=["historical datasets"])


class DatasetInput(BaseModel):
    symbol: str = Field(pattern=r"^[A-Za-z0-9_.:+-]{1,80}$")
    timeframe_seconds: int = 60
    limit: int = Field(default=5000, ge=1, le=100000)

    @field_validator("timeframe_seconds")
    @classmethod
    def supported(cls, value):
        if value not in RESOLUTIONS:
            raise ValueError("Unsupported Delta historical resolution")
        return value


class DatasetOutput(BaseModel):
    id: str
    symbol: str
    timeframe_seconds: int
    source: str
    start_at: datetime
    end_at: datetime
    candle_count: int
    content_hash: str
    complete: bool
    missing_ranges: list[dict]
    warnings: list[str]
    created_at: datetime


@router.get("/market/datasets", response_model=list[DatasetOutput])
def datasets(symbol: str | None = None, limit: int = Query(default=100, ge=1, le=500),
             db: Session = Depends(get_db), principal=Depends(identity)):
    query = select(HistoricalDataset).order_by(HistoricalDataset.created_at.desc()).limit(limit)
    if symbol:
        query = query.where(HistoricalDataset.symbol == symbol)
    return [serialize(row) for row in db.scalars(query)]


@router.post("/market/datasets", response_model=DatasetOutput, status_code=201)
def snapshot_dataset(payload: DatasetInput, db: Session = Depends(get_db), user=Depends(operator)):
    try:
        dataset = create_dataset(db, payload.symbol, payload.timeframe_seconds, payload.limit)
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    audit(db, user.username, "DATASET_SNAPSHOT", dataset.id,
          {"symbol": dataset.symbol, "candles": dataset.candle_count, "complete": dataset.complete})
    db.commit()
    return serialize(dataset)


@router.get("/market/datasets/{identifier}", response_model=DatasetOutput)
def dataset(identifier: str, db: Session = Depends(get_db), principal=Depends(identity)):
    row = db.get(HistoricalDataset, identifier)
    if row is None:
        raise HTTPException(404, "Historical dataset not found")
    return serialize(row)


@router.get("/market/datasets/{identifier}/candles")
def pinned_candles(identifier: str, db: Session = Depends(get_db), principal=Depends(identity)):
    if db.get(HistoricalDataset, identifier) is None:
        raise HTTPException(404, "Historical dataset not found")
    return dataset_candles(db, identifier)


@router.get("/market/gaps")
def gaps(symbol: str | None = None, status: str | None = None,
         limit: int = Query(default=200, ge=1, le=1000), db: Session = Depends(get_db), principal=Depends(identity)):
    query = select(MarketDataGap).order_by(MarketDataGap.detected_at.desc()).limit(limit)
    if symbol:
        query = query.where(MarketDataGap.symbol == symbol)
    if status:
        if status not in ("OPEN", "RESOLVED", "MERGED"):
            raise HTTPException(422, "Unknown missing-data status")
        query = query.where(MarketDataGap.status == status)
    return [serialize(row) for row in db.scalars(query)]


@router.get("/market/revisions")
def revisions(symbol: str | None = None, timeframe_seconds: int | None = None,
              limit: int = Query(default=100, ge=1, le=1000), db: Session = Depends(get_db), principal=Depends(identity)):
    query = select(CandleRevision).order_by(CandleRevision.observed_at.desc(), CandleRevision.id.desc()).limit(limit)
    if symbol:
        query = query.where(CandleRevision.symbol == symbol)
    if timeframe_seconds:
        query = query.where(CandleRevision.timeframe_seconds == timeframe_seconds)
    return [serialize(row) for row in db.scalars(query)]
