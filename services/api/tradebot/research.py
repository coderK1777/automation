"""Bounded document extraction with explicit operator review and source history."""
import hashlib
import re
import json
import subprocess
import sys
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from tradebot.db import get_db, utcnow
from tradebot.models import Strategy, StrategyVersion
from tradebot.repository import audit, serialize, strategy_dict
from tradebot.research_models import ResearchCandidate, ResearchDocument
from tradebot.security import identity, operator
from tradebot_core import StrategyConfig

router = APIRouter(prefix="/api/v1/research", tags=["research"])
MAX_TEXT = 250_000


class DocumentInput(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    source_reference: str = Field(min_length=1, max_length=1000)
    format: Literal["TEXT", "MARKDOWN", "PDF_BASE64"] = "TEXT"
    content: str = Field(min_length=1, max_length=650_000)


class CandidateReview(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    specification: dict[str, Any]


class CandidateApproval(BaseModel):
    confirm: bool
    config: dict[str, Any]


def document_text(payload: DocumentInput) -> tuple[str, int]:
    if payload.format != "PDF_BASE64":
        if len(payload.content) > MAX_TEXT:
            raise HTTPException(413, "Document text exceeds 250,000 characters")
        return payload.content, 1
    try:
        result = subprocess.run([sys.executable, "-m", "tradebot.research_pdf"], input=payload.content.encode(),
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=12, check=True)
        value = json.loads(result.stdout)
        return value["text"], value["pages"]
    except Exception as error:
        raise HTTPException(422, "PDF could not be parsed within resource/text/page limits; encrypted and image-only PDFs require extracted text") from error


def extract_candidates(title: str, text: str, reference: str) -> list[dict]:
    """Extract quotations only. Subjective prose never becomes executable rules."""
    lines = text.splitlines()
    headings = [(i, line.strip("# *\t")) for i, line in enumerate(lines)
                if re.search(r"\b(strategy|system|setup|entry rules|trading method)\b", line, re.I)
                and 3 <= len(line.strip()) <= 140]
    # No fabricated named strategy when no recognizable section is present.
    if not headings:
        headings = [(0, f"{title[:85]} — research notes")]
    candidates = []
    for index, (start, heading) in enumerate(headings[:30]):
        end = headings[index + 1][0] if index + 1 < len(headings) else len(lines)
        excerpt = "\n".join(lines[start:min(end, start + 80)])[:8000]
        indicators = sorted(set(re.findall(r"\b(EMA|SMA|RSI|MACD|ATR|ADX|VWAP|Bollinger|Stochastic|OBV)\b", excerpt, re.I)))
        def quotations(pattern):
            return [line.strip()[:1000] for line in excerpt.splitlines() if re.search(pattern, line, re.I)][:15]
        ambiguities = ["Quoted prose is not an executable condition. Review thresholds, bar timing, units, direction and data availability."]
        entry = quotations(r"\b(buy|sell|enter|entry|long|short)\b")
        stop = quotations(r"\b(stop|invalidation|invalid)\b")
        take = quotations(r"\b(target|take.profit|profit taking)\b")
        missing = [key for key, value in (("entry", entry), ("stop loss", stop), ("profit taking", take)) if not value]
        ambiguities.extend(f"Missing explicit {key} rule." for key in missing)
        if re.search(r"\b(strong|weak|significant|near|good|bad|clear|momentum)\b", excerpt, re.I):
            ambiguities.append("Subjective language detected. Candidate definitions such as RSI > an operator-selected threshold or a closed-bar EMA cross need explicit approval.")
        spec = {"strategy_id": None, "name": heading[:120], "source_title": title, "source_location": f"{reference}; lines {start + 1}–{min(end, start + 80)}",
                "trading_style": None, "supported_markets": [], "preferred_timeframes": quotations(r"\b(\d+\s*(minute|hour|day)|timeframe)\b"),
                "indicators": indicators, "parameters": {}, "market_regime": None,
                "long_entry_rules": quotations(r"\b(buy|long)\b"), "short_entry_rules": quotations(r"\b(sell|short)\b"),
                "confirmation_rules": quotations(r"\b(confirm|confirmation)\b"), "no_trade_rules": quotations(r"\b(avoid|do not trade|no.trade)\b"),
                "invalidation_rules": quotations(r"\b(invalid|invalidate)\b"), "stop_loss_rules": stop, "take_profit_rules": take,
                "trailing_stop_rules": quotations(r"\b(trail|trailing)\b"), "position_sizing": None, "risk_rules": quotations(r"\b(risk|position.size)\b"),
                "exit_rules": quotations(r"\b(exit|close position)\b"), "automation_ambiguities": ambiguities,
                "confidence_in_extraction": "QUOTATIONS_ONLY_OPERATOR_REVIEW_REQUIRED", "pseudocode": None,
                "test_cases": ["Warm-up bars must produce no entry", "Signal must use only closed bars", "Missing data must block entries", "Check long/short threshold boundary manually"],
                "approval_status": "NEEDS_REVIEW"}
        candidates.append({"name": heading[:120], "source_location": spec["source_location"][:200], "excerpt": excerpt, "specification": spec})
    return candidates


def get_record(db, model, identifier):
    row = db.get(model, identifier)
    if row is None:
        raise HTTPException(404, "Research record not found")
    return row


@router.get("/documents")
def documents(db: Session = Depends(get_db), principal=Depends(identity)):
    rows = db.scalars(select(ResearchDocument).order_by(ResearchDocument.id.desc()).limit(100))
    return [{key: value for key, value in serialize(row).items() if key != "content"} for row in rows]


@router.post("/documents", status_code=201)
def import_document(payload: DocumentInput, db: Session = Depends(get_db), user=Depends(operator)):
    text, pages = document_text(payload)
    digest = hashlib.sha256(text.encode()).hexdigest()
    existing = db.scalar(select(ResearchDocument).where(ResearchDocument.content_hash == digest))
    if existing:
        return {"document": serialize(existing), "candidates": [serialize(row) for row in db.scalars(select(ResearchCandidate).where(ResearchCandidate.document_id == existing.id))], "deduplicated": True}
    document = ResearchDocument(title=payload.title, source_reference=payload.source_reference, format=payload.format,
                                content_hash=digest, content=text, page_count=pages, imported_by=user.username)
    db.add(document)
    db.flush()
    for candidate in extract_candidates(payload.title, text, payload.source_reference):
        db.add(ResearchCandidate(document_id=document.id, **candidate))
    audit(db, user.username, "RESEARCH_IMPORTED", str(document.id), {"content_hash": digest, "page_count": pages})
    db.commit()
    return document_detail(document.id, db, None)


@router.get("/documents/{identifier}")
def document_detail(identifier: int, db: Session = Depends(get_db), principal=Depends(identity)):
    document = get_record(db, ResearchDocument, identifier)
    return {"document": serialize(document), "candidates": [serialize(row) for row in db.scalars(select(ResearchCandidate).where(ResearchCandidate.document_id == identifier))]}


@router.get("/candidates/{identifier}")
def candidate_detail(identifier: int, db: Session = Depends(get_db), principal=Depends(identity)):
    return serialize(get_record(db, ResearchCandidate, identifier))


@router.patch("/candidates/{identifier}")
def review_candidate(identifier: int, payload: CandidateReview, db: Session = Depends(get_db), user=Depends(operator)):
    candidate = get_record(db, ResearchCandidate, identifier)
    if candidate.status == "APPROVED":
        raise HTTPException(409, "Approved extraction is immutable; import a revised source for further review")
    # Source attribution cannot be erased by review.
    original = candidate.specification
    spec = {**original, **payload.specification}
    for key in ("source_title", "source_location"):
        spec[key] = original[key]
    spec["approval_status"] = "REVIEWED"
    if len(str(spec)) > 50_000:
        raise HTTPException(413, "Specification exceeds limit")
    candidate.specification = spec
    candidate.name = payload.name or candidate.name
    candidate.status = "REVIEWED"
    candidate.reviewed_by, candidate.reviewed_at = user.username, utcnow()
    audit(db, user.username, "RESEARCH_REVIEWED", str(identifier))
    db.commit()
    return serialize(candidate)


@router.post("/candidates/{identifier}/approve", status_code=201)
def approve_candidate(identifier: int, payload: CandidateApproval, db: Session = Depends(get_db), user=Depends(operator)):
    candidate = db.scalar(select(ResearchCandidate).where(ResearchCandidate.id == identifier).with_for_update())
    if candidate is None:
        raise HTTPException(404, "Research record not found")
    if not payload.confirm:
        raise HTTPException(422, "Explicit operator confirmation is required")
    if candidate.status != "REVIEWED":
        raise HTTPException(409, "Review and resolve the specification before approving")
    unresolved = candidate.specification.get("automation_ambiguities")
    if unresolved:
        raise HTTPException(409, "Resolve automation_ambiguities explicitly during review before approval")
    try:
        config = StrategyConfig.model_validate({**payload.config, "name": candidate.name, "version": 1})
    except ValidationError as error:
        raise HTTPException(422, [{"loc": item["loc"], "msg": item["msg"], "type": item["type"]} for item in error.errors()]) from error
    strategy = Strategy(name=candidate.name, description=f"Research source: {candidate.source_location}", status="DRAFT")
    db.add(strategy)
    db.flush()
    db.add(StrategyVersion(strategy_id=strategy.id, version=1, config=config.model_dump(mode="json"), validated=False))
    candidate.status, candidate.strategy_id = "APPROVED", strategy.id
    candidate.reviewed_by, candidate.reviewed_at = user.username, utcnow()
    candidate.specification = {**candidate.specification, "strategy_id": strategy.id, "approval_status": "APPROVED"}
    audit(db, user.username, "RESEARCH_APPROVED", str(identifier), {"strategy_id": strategy.id, "source_document_id": candidate.document_id})
    db.commit()
    return {"candidate": serialize(candidate), "strategy": strategy_dict(db, strategy), "next_step": "Validate and backtest this immutable Draft version; approval never starts trading."}
