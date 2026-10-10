import hashlib
import secrets
from datetime import timedelta

from fastapi import Depends, HTTPException, Request
from pwdlib import PasswordHash
from sqlalchemy.orm import Session

from tradebot.config import get_settings
from tradebot.db import aware, get_db, utcnow
from tradebot.models import AuthSession, User

password_hasher = PasswordHash.recommended()


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def make_session(db: Session, user: User) -> tuple[str, AuthSession]:
    token = secrets.token_urlsafe(32)
    session = AuthSession(
        id=token_hash(token), user_id=user.id, csrf_token=secrets.token_urlsafe(32),
        expires_at=utcnow() + timedelta(seconds=get_settings().session_ttl_seconds),
    )
    db.add(session)
    return token, session


def same_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") != get_settings().app_origin.rstrip("/"):
        raise HTTPException(403, "Untrusted request origin")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "Cross-site request rejected")


def identity(request: Request, db: Session = Depends(get_db)) -> tuple[User, AuthSession]:
    token = request.cookies.get("tradebot_session")
    auth = db.get(AuthSession, token_hash(token)) if token else None
    if not auth or aware(auth.expires_at) <= utcnow():
        raise HTTPException(401, "Authentication required")
    user = db.get(User, auth.user_id)
    if not user or not user.active:
        raise HTTPException(401, "Authentication required")
    return user, auth


def operator(request: Request, principal=Depends(identity)) -> User:
    user, auth = principal
    if user.role != "operator":
        raise HTTPException(403, "Operator role required")
    same_origin(request)
    csrf = request.headers.get("x-csrf-token", "")
    if not secrets.compare_digest(csrf, auth.csrf_token):
        raise HTTPException(403, "CSRF token required")
    return user
