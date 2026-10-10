from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")
    database_url: str = "postgresql+psycopg://tradebot:tradebot@localhost:5432/tradebot"
    redis_url: str = "redis://localhost:6379/0"
    app_origin: str = "http://localhost:3000"
    session_cookie_secure: bool = True
    session_ttl_seconds: int = Field(default=28800, ge=300, le=86400)
    bootstrap_username: str | None = None
    bootstrap_password: str | None = None
    trading_mode: Literal["PAPER"] = "PAPER"
    live_execution_enabled: Literal[False] = False
    paper_fee_rate: str = "0.0005"
    paper_slippage_bps: str = "2"
    worker_poll_seconds: int = Field(default=15, ge=5, le=300)

    @field_validator("live_execution_enabled", mode="before")
    @classmethod
    def parse_disabled_live(cls, value):
        if isinstance(value, str) and value.lower() in ("false", "0", "off", "no"):
            return False
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
