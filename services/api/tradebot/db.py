from collections.abc import Generator
from datetime import datetime, timezone
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from tradebot.config import get_settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


class Base(DeclarativeBase):
    pass


@lru_cache
def engine():
    url = get_settings().database_url
    kwargs = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    return create_engine(url, **kwargs)


@lru_cache
def session_factory():
    return sessionmaker(engine(), expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    with session_factory()() as db:
        try:
            yield db
        except Exception:
            db.rollback()
            raise
