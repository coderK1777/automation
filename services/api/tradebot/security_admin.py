"""MFA, recent authentication, session administration and audited readiness evidence."""
import base64
import hashlib
import hmac
import re
import secrets
import struct
import time
from datetime import timedelta
from typing import Literal
from urllib.parse import quote, urlencode, urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from tradebot.db import aware, get_db, utcnow
from tradebot.models import Approval, AuthSession, Backtest, Strategy, User
from tradebot.repository import audit
from tradebot.security import identity, operator, password_hasher, same_origin, token_hash
from tradebot.security_admin_models import MFAState, ReadinessEvidence, Reauthentication, SecurityAttempt
from tradebot.vault import VaultError, decrypt_secret, encrypt_secret, vault_status

router = APIRouter(prefix="/api/v1/security", tags=["security"])
REAUTH_SECONDS = 300
REQUIRED_READINESS = (
    "DEMO_INTEGRATION", "SECURITY_REVIEW", "RISK_REVIEW", "BACKUP_RESTORE",
    "FAILURE_RECOVERY", "OPERATIONAL_REVIEW",
)


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FactorInput(Input):
    code: str | None = Field(default=None, pattern=r"^\d{6}$", repr=False)
    recovery_code: str | None = Field(default=None, min_length=16, max_length=64, repr=False)


class ReauthInput(FactorInput):
    password: str = Field(min_length=1, max_length=256, repr=False)


class MFAConfirm(Input):
    code: str = Field(pattern=r"^\d{6}$", repr=False)


class MFADisable(Input):
    confirm: Literal["DISABLE_MFA"]


class UserCreate(Input):
    username: str = Field(min_length=3, max_length=80, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")
    password: str = Field(min_length=12, max_length=256, repr=False)
    role: Literal["operator", "viewer"] = "viewer"


class UserUpdate(Input):
    role: Literal["operator", "viewer"] | None = None
    active: bool | None = None


class PasswordInput(FactorInput):
    current_password: str = Field(min_length=1, max_length=256, repr=False)
    new_password: str = Field(min_length=12, max_length=256, repr=False)


class EvidenceInput(Input):
    version: int = Field(ge=1)
    category: Literal["DEMO_INTEGRATION", "SECURITY_REVIEW", "RISK_REVIEW", "BACKUP_RESTORE", "FAILURE_RECOVERY", "OPERATIONAL_REVIEW"]
    outcome: Literal["PASS", "FAIL", "PENDING"]
    evidence_uri: str = Field(min_length=1, max_length=500)
    evidence_sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    notes: str = Field(default="", max_length=4000)


def totp_code(secret: str, timestamp: float | None = None, digits: int = 6) -> str:
    """RFC 6238 SHA-1 TOTP; the default matches standard authenticator apps."""
    if digits not in (6, 8):
        raise ValueError("TOTP digits must be six or eight")
    normalized = secret.strip().replace(" ", "").upper()
    key = base64.b32decode(normalized + "=" * (-len(normalized) % 8))
    counter = int((time.time() if timestamp is None else timestamp) // 30)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 15
    number = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7fffffff
    return str(number % (10 ** digits)).zfill(digits)


def _matching_counter(secret: str, code: str | None, last_counter: int = -1) -> int | None:
    if not code or not re.fullmatch(r"\d{6}", code):
        return None
    counter = int(time.time() // 30)
    for candidate in (counter, counter - 1, counter + 1):
        if candidate > last_counter and candidate >= 0 and hmac.compare_digest(totp_code(secret, candidate * 30), code):
            return candidate
    return None


def _locked_mfa(db: Session, user: User) -> MFAState | None:
    return db.scalar(select(MFAState).where(MFAState.user_id == user.id).with_for_update())


def _factor(db: Session, user: User, code: str | None, recovery_code: str | None) -> bool:
    state = _locked_mfa(db, user)
    if not state or not state.enabled:
        return True
    if code and recovery_code:
        return False
    if recovery_code:
        fingerprint = token_hash(recovery_code.strip().replace("-", "").upper())
        matching = next((value for value in state.recovery_hashes if hmac.compare_digest(value, fingerprint)), None)
        if matching:
            state.recovery_hashes = [value for value in state.recovery_hashes if value != matching]
            state.updated_at = utcnow()
            audit(db, user.username, "MFA_RECOVERY_USED", "user:" + str(user.id))
            return True
        return False
    try:
        counter = _matching_counter(decrypt_secret(state.secret_ciphertext, "mfa:" + str(user.id)), code, state.last_counter)
    except VaultError as error:
        raise HTTPException(503, "MFA key is unavailable; restore the deployment vault key") from error
    if counter is None:
        return False
    state.last_counter = counter
    state.updated_at = utcnow()
    return True


def verify_login_mfa(db: Session, user: User, code: str | None = None, recovery_code: str | None = None) -> bool:
    """Call only after verifying the password, and commit the login transaction."""
    return _factor(db, user, code, recovery_code)


def _attempt(db: Session, user: User, action: str) -> None:
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(90611, :user_id)"), {"user_id": user.id})
    cutoff = utcnow() - timedelta(minutes=15)
    count = db.scalar(select(func.count()).select_from(SecurityAttempt).where(
        SecurityAttempt.user_id == user.id, SecurityAttempt.action == action, SecurityAttempt.created_at >= cutoff))
    if count >= 10:
        raise HTTPException(429, "Security verification rate limit exceeded; retry after 15 minutes")
    db.execute(delete(SecurityAttempt).where(SecurityAttempt.created_at < cutoff))
    db.add(SecurityAttempt(user_id=user.id, action=action))


def _password_valid(password: str, user: User) -> bool:
    try:
        return password_hasher.verify(password, user.password_hash)
    except Exception:
        return False


def _failure(db: Session, user: User, action: str) -> None:
    audit(db, user.username, action, "user:" + str(user.id))
    db.commit()
    raise HTTPException(401, "Password or second factor is invalid")


def _csrf_identity(request: Request, principal=Depends(identity)) -> tuple[User, AuthSession]:
    user, auth = principal
    same_origin(request)
    if not secrets.compare_digest(request.headers.get("x-csrf-token", ""), auth.csrf_token):
        raise HTTPException(403, "CSRF token required")
    return user, auth


def _reauth_output(row: Reauthentication | None) -> dict | None:
    if not row:
        return None
    valid = aware(row.expires_at) > utcnow()
    return {"password_verified": valid, "mfa_verified": valid and row.mfa_verified_at is not None,
            "expires_at": aware(row.expires_at).isoformat()}


def recent_identity(principal=Depends(_csrf_identity), db: Session = Depends(get_db)) -> User:
    user, auth = principal
    recent = db.get(Reauthentication, auth.id)
    if not recent or aware(recent.expires_at) <= utcnow():
        raise HTTPException(403, "Recent password authentication required; use /api/v1/security/reauth")
    return user


def recent_operator(user=Depends(operator), recent=Depends(recent_identity)) -> User:
    return user


def sensitive_identity(user=Depends(recent_identity), principal=Depends(identity), db: Session = Depends(get_db)) -> User:
    _, auth = principal
    mfa = db.get(MFAState, user.id)
    recent = db.get(Reauthentication, auth.id)
    if not mfa or not mfa.enabled or not recent.mfa_verified_at:
        raise HTTPException(403, "Enabled MFA and recent second-factor authentication required")
    return user


def sensitive_operator(user=Depends(operator), recent=Depends(sensitive_identity)) -> User:
    return user


@router.get("/status")
def status(principal=Depends(identity), db: Session = Depends(get_db)):
    user, auth = principal
    mfa = db.get(MFAState, user.id)
    try:
        vault = vault_status() if user.role == "operator" else {"configured": False, "credentials": []}
    except VaultError:
        vault = {"configured": False, "credentials": [], "error": "Local vault is unavailable or has unsafe permissions"}
    return {"mfa_enabled": bool(mfa and mfa.enabled),
            "pending_enrollment": bool(mfa and mfa.pending_expires_at and aware(mfa.pending_expires_at) > utcnow()),
            "recovery_codes_remaining": len(mfa.recovery_hashes) if mfa else 0,
            "reauth": _reauth_output(db.get(Reauthentication, auth.id)), "vault": vault, "live_enabled": False}


@router.post("/reauth")
def reauthenticate(payload: ReauthInput, principal=Depends(_csrf_identity), db: Session = Depends(get_db)):
    user, auth = principal
    _attempt(db, user, "REAUTH")
    if not _password_valid(payload.password, user) or not _factor(db, user, payload.code, payload.recovery_code):
        _failure(db, user, "REAUTH_FAILED")
    mfa = db.get(MFAState, user.id)
    now = utcnow()
    row = db.get(Reauthentication, auth.id)
    if not row:
        row = Reauthentication(session_id=auth.id)
        db.add(row)
    row.password_verified_at = now
    row.mfa_verified_at = now if mfa and mfa.enabled else None
    row.expires_at = min(now + timedelta(seconds=REAUTH_SECONDS), aware(auth.expires_at))
    audit(db, user.username, "REAUTHENTICATED", "session", {"mfa": row.mfa_verified_at is not None})
    db.commit()
    return _reauth_output(row)


@router.post("/mfa/enroll")
def enroll(user=Depends(recent_identity), db: Session = Depends(get_db)):
    state = _locked_mfa(db, user)
    if state and state.enabled:
        raise HTTPException(409, "MFA is already enabled")
    if not state:
        state = MFAState(user_id=user.id)
        db.add(state)
    secret = base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")
    try:
        state.pending_ciphertext = encrypt_secret(secret, "mfa:" + str(user.id))
    except VaultError as error:
        raise HTTPException(503, "Initialize the local encrypted vault before enrolling MFA") from error
    state.pending_expires_at = utcnow() + timedelta(minutes=10)
    audit(db, user.username, "MFA_ENROLLMENT_STARTED", "user:" + str(user.id))
    db.commit()
    query = urlencode({"secret": secret, "issuer": "TradeBot Pro", "algorithm": "SHA1", "digits": 6, "period": 30})
    return {"secret": secret, "provisioning_uri": "otpauth://totp/TradeBot%20Pro:" + quote(user.username, safe="") + "?" + query,
            "expires_at": aware(state.pending_expires_at).isoformat()}


def _recovery_codes(state: MFAState) -> list[str]:
    codes = [base64.b32encode(secrets.token_bytes(15)).decode().rstrip("=") for _ in range(8)]
    state.recovery_hashes = [token_hash(code) for code in codes]
    return codes


@router.post("/mfa/confirm")
def confirm_mfa(payload: MFAConfirm, user=Depends(recent_identity), principal=Depends(identity), db: Session = Depends(get_db)):
    _attempt(db, user, "MFA_CONFIRM")
    state = _locked_mfa(db, user)
    if not state or not state.pending_expires_at or aware(state.pending_expires_at) <= utcnow():
        raise HTTPException(409, "No current MFA enrollment; begin a new enrollment")
    try:
        counter = _matching_counter(decrypt_secret(state.pending_ciphertext, "mfa:" + str(user.id)), payload.code)
    except VaultError as error:
        raise HTTPException(503, "MFA key is unavailable") from error
    if counter is None:
        _failure(db, user, "MFA_CONFIRM_FAILED")
    state.secret_ciphertext = state.pending_ciphertext
    state.pending_ciphertext = None
    state.pending_expires_at = None
    state.enabled = True
    state.last_counter = counter
    state.updated_at = utcnow()
    codes = _recovery_codes(state)
    _, auth = principal
    recent = db.get(Reauthentication, auth.id)
    recent.mfa_verified_at = utcnow()
    audit(db, user.username, "MFA_ENABLED", "user:" + str(user.id))
    db.commit()
    return {"enabled": True, "recovery_codes": codes, "reauth": _reauth_output(recent)}


@router.post("/mfa/disable")
def disable_mfa(payload: MFADisable, user=Depends(sensitive_identity), db: Session = Depends(get_db)):
    state = _locked_mfa(db, user)
    state.enabled = False
    state.secret_ciphertext = None
    state.recovery_hashes = []
    state.updated_at = utcnow()
    db.execute(delete(Reauthentication).where(Reauthentication.session_id.in_(select(AuthSession.id).where(AuthSession.user_id == user.id))))
    audit(db, user.username, "MFA_DISABLED", "user:" + str(user.id))
    db.commit()
    return {"enabled": False}


@router.post("/mfa/recovery-codes")
def regenerate_recovery(user=Depends(sensitive_identity), db: Session = Depends(get_db)):
    state = _locked_mfa(db, user)
    codes = _recovery_codes(state)
    audit(db, user.username, "MFA_RECOVERY_ROTATED", "user:" + str(user.id))
    db.commit()
    return {"recovery_codes": codes}


def _session_id(auth: AuthSession) -> str:
    return token_hash("display:" + auth.id)[:24]


@router.get("/sessions")
def sessions(principal=Depends(identity), db: Session = Depends(get_db)):
    user, current = principal
    return [{"id": _session_id(auth), "expires_at": aware(auth.expires_at).isoformat(), "current": auth.id == current.id}
            for auth in db.scalars(select(AuthSession).where(AuthSession.user_id == user.id, AuthSession.expires_at > utcnow()))]


def _delete_sessions(db: Session, user_id: int, keep: str | None = None) -> int:
    ids = list(db.scalars(select(AuthSession.id).where(AuthSession.user_id == user_id)))
    ids = [identifier for identifier in ids if identifier != keep]
    if ids:
        db.execute(delete(Reauthentication).where(Reauthentication.session_id.in_(ids)))
        db.execute(delete(AuthSession).where(AuthSession.id.in_(ids)))
    return len(ids)


@router.delete("/sessions/{identifier}")
def revoke_session(identifier: str, principal=Depends(_csrf_identity), db: Session = Depends(get_db)):
    user, current = principal
    row = next((auth for auth in db.scalars(select(AuthSession).where(AuthSession.user_id == user.id))
                if hmac.compare_digest(_session_id(auth), identifier)), None)
    if not row:
        raise HTTPException(404, "Session not found")
    db.execute(delete(Reauthentication).where(Reauthentication.session_id == row.id))
    db.delete(row)
    audit(db, user.username, "SESSION_REVOKED", "user:" + str(user.id), {"current": row.id == current.id})
    db.commit()
    return {"revoked": True, "current": row.id == current.id}


@router.post("/password")
def change_password(payload: PasswordInput, principal=Depends(_csrf_identity), db: Session = Depends(get_db)):
    user, auth = principal
    _attempt(db, user, "PASSWORD")
    if not _password_valid(payload.current_password, user) or not _factor(db, user, payload.code, payload.recovery_code):
        _failure(db, user, "PASSWORD_CHANGE_FAILED")
    if payload.current_password == payload.new_password:
        raise HTTPException(422, "Choose a different new password")
    user.password_hash = password_hasher.hash(payload.new_password)
    revoked = _delete_sessions(db, user.id, keep=auth.id)
    db.execute(delete(Reauthentication).where(Reauthentication.session_id == auth.id))
    audit(db, user.username, "PASSWORD_CHANGED", "user:" + str(user.id), {"sessions_revoked": revoked})
    db.commit()
    return {"changed": True, "sessions_revoked": revoked}


def _user_dict(db: Session, user: User) -> dict:
    mfa = db.get(MFAState, user.id)
    return {"id": user.id, "username": user.username, "role": user.role, "active": user.active, "mfa_enabled": bool(mfa and mfa.enabled)}


@router.get("/users")
def users(principal=Depends(identity), db: Session = Depends(get_db)):
    user, _ = principal
    if user.role != "operator":
        raise HTTPException(403, "Operator role required")
    return [_user_dict(db, item) for item in db.scalars(select(User).order_by(User.id))]


@router.post("/users", status_code=201)
def create_user(payload: UserCreate, user=Depends(sensitive_operator), db: Session = Depends(get_db)):
    if db.scalar(select(User).where(func.lower(User.username) == payload.username.lower())):
        raise HTTPException(409, "Username already exists")
    row = User(username=payload.username, password_hash=password_hasher.hash(payload.password), role=payload.role, active=True)
    db.add(row)
    db.flush()
    audit(db, user.username, "USER_CREATED", "user:" + str(row.id), {"role": row.role})
    db.commit()
    return _user_dict(db, row)


@router.patch("/users/{identifier}")
def update_user(identifier: int, payload: UserUpdate, user=Depends(sensitive_operator), db: Session = Depends(get_db)):
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(90612, 0)"))
    row = db.get(User, identifier)
    if not row:
        raise HTTPException(404, "User not found")
    if row.role == "operator" and row.active and (payload.role == "viewer" or payload.active is False):
        active_operators = db.scalar(select(func.count()).select_from(User).where(User.role == "operator", User.active.is_(True)))
        if active_operators <= 1:
            raise HTTPException(409, "The last active operator cannot be disabled or demoted")
    changed = False
    for key in ("role", "active"):
        value = getattr(payload, key)
        if value is not None and getattr(row, key) != value:
            setattr(row, key, value)
            changed = True
    if changed:
        _delete_sessions(db, row.id)
        audit(db, user.username, "USER_ACCESS_CHANGED", "user:" + str(row.id), {"role": row.role, "active": row.active})
    db.commit()
    return _user_dict(db, row)


@router.delete("/users/{identifier}/sessions")
def revoke_user_sessions(identifier: int, user=Depends(sensitive_operator), db: Session = Depends(get_db)):
    if not db.get(User, identifier):
        raise HTTPException(404, "User not found")
    count = _delete_sessions(db, identifier)
    audit(db, user.username, "USER_SESSIONS_REVOKED", "user:" + str(identifier), {"count": count})
    db.commit()
    return {"sessions_revoked": count}


def _strategy(db: Session, identifier: int) -> Strategy:
    strategy = db.get(Strategy, identifier)
    if not strategy:
        raise HTTPException(404, "Strategy not found")
    return strategy


def _readiness(db: Session, strategy: Strategy, user: User) -> dict:
    records = list(db.scalars(select(ReadinessEvidence).where(ReadinessEvidence.strategy_id == strategy.id,
                         ReadinessEvidence.version == strategy.current_version).order_by(ReadinessEvidence.id)))
    latest = {item.category: item for item in records}
    approvals = list(db.scalars(select(Approval).where(Approval.strategy_id == strategy.id, Approval.version == strategy.current_version)))
    stages = {approval.stage for approval in approvals}
    backtest = db.scalar(select(Backtest).where(Backtest.strategy_id == strategy.id, Backtest.version == strategy.current_version, Backtest.status == "COMPLETED"))
    mfa = db.get(MFAState, user.id)
    gates = {"version_validated": strategy.approved_version == strategy.current_version,
             "backtested": backtest is not None,
             "paper_approved": any("PAPER" in stage.upper() for stage in stages),
             "demo_approved": "DEMO_APPROVED" in stages,
             "operator_mfa": bool(mfa and mfa.enabled)}
    for category in REQUIRED_READINESS:
        gates[category.lower()] = category in latest and latest[category].outcome == "PASS"
    return {"strategy_id": strategy.id, "version": strategy.current_version, "gates": gates,
            "review_complete": all(gates.values()), "live_enabled": False, "activation_available": False,
            "authorization": "Live trading requires separate explicit operator authorization and a verified production executor; activation is unavailable.",
            "evidence": [{"id": item.id, "category": item.category, "outcome": item.outcome,
                "evidence_uri": item.evidence_uri, "evidence_sha256": item.evidence_sha256,
                "notes": item.notes, "actor": item.actor, "created_at": aware(item.created_at).isoformat()} for item in records]}


@router.get("/readiness/{identifier}")
def readiness(identifier: int, principal=Depends(identity), db: Session = Depends(get_db)):
    user, _ = principal
    if user.role != "operator":
        raise HTTPException(403, "Operator role required")
    return _readiness(db, _strategy(db, identifier), user)


@router.post("/readiness/{identifier}/evidence", status_code=201)
def append_evidence(identifier: int, payload: EvidenceInput, user=Depends(sensitive_operator), db: Session = Depends(get_db)):
    strategy = _strategy(db, identifier)
    if payload.version != strategy.current_version:
        raise HTTPException(409, "Evidence must identify the current immutable strategy version")
    uri = urlsplit(payload.evidence_uri)
    if ((uri.scheme and (uri.scheme.lower() != "https" or not uri.hostname)) or uri.username or uri.password
            or payload.evidence_uri.startswith("//") or "\\" in payload.evidence_uri or "@" in payload.evidence_uri):
        raise HTTPException(422, "Use a repository path or HTTPS evidence link without credentials")
    db.add(ReadinessEvidence(strategy_id=identifier, **payload.model_dump(), actor=user.username))
    audit(db, user.username, "READINESS_EVIDENCE_RECORDED", "strategy:" + str(identifier), {"version": payload.version, "category": payload.category, "outcome": payload.outcome})
    db.commit()
    return _readiness(db, strategy, user)
