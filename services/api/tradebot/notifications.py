"""Optional Telegram transport; allowlisted commands never mutate trading state."""
import os
from datetime import timedelta
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tradebot.db import aware, get_db, utcnow
from tradebot.models import Audit, Notification, Order, PaperAccount, Position, RiskConfig, Strategy, WorkerHeartbeat
from tradebot.notifications_models import TelegramOutbox, TelegramSettings
from tradebot.repository import account_dict, audit, serialize
from tradebot.security import identity, operator

router = APIRouter(prefix="/api/v1/integrations/telegram", tags=["telegram"])
COMMANDS = ("/status", "/balance", "/positions", "/orders", "/strategies", "/pnl", "/risk", "/health", "/logs")


class TelegramInput(BaseModel):
    enabled: bool = False
    allowed_chat_ids: list[str] = Field(default_factory=list, max_length=100)
    allowed_user_ids: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("allowed_chat_ids", "allowed_user_ids")
    @classmethod
    def identifiers(cls, values):
        if any(not value.lstrip("-").isdigit() or len(value) > 30 for value in values):
            raise ValueError("Telegram IDs must be numeric IDs")
        return sorted(set(values))


class RetryInput(BaseModel):
    action: Literal["RETRY", "CANCEL"]


def configured_token() -> str | None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if ":" not in token or any(word in token.lower() for word in ("placeholder", "your_token", "changeme", "example")):
        return None
    return token


def metadata(db: Session):
    config = db.get(TelegramSettings, 1)
    counts = dict(db.execute(select(TelegramOutbox.status, func.count()).group_by(TelegramOutbox.status)).all())
    return {"enabled": bool(config and config.enabled), "token_configured": configured_token() is not None,
            "delivery_enabled": os.getenv("TELEGRAM_DELIVERY_ENABLED", "false").lower() == "true",
            "allowed_chat_ids": config.allowed_chat_ids if config else [], "allowed_user_ids": config.allowed_user_ids if config else [],
            "outbox_counts": counts, "commands": COMMANDS, "remote_trading_controls": False,
            "verification": "OFFLINE_CONTRACT_TESTED; actual delivery requires a configured token, allowlists and delivery enablement"}


@router.get("")
def status(db: Session = Depends(get_db), principal=Depends(identity)):
    return metadata(db)


@router.put("")
def configure(payload: TelegramInput, db: Session = Depends(get_db), user=Depends(operator)):
    if payload.enabled and (not payload.allowed_chat_ids or not payload.allowed_user_ids):
        raise HTTPException(422, "Enabling Telegram requires both chat and user ID allowlists")
    config = db.get(TelegramSettings, 1)
    if config is None:
        config = TelegramSettings(id=1, last_notification_id=db.scalar(select(func.max(Notification.id))) or 0)
        db.add(config)
    config.enabled, config.allowed_chat_ids, config.allowed_user_ids = payload.enabled, payload.allowed_chat_ids, payload.allowed_user_ids
    config.updated_at = utcnow()
    audit(db, user.username, "TELEGRAM_CONFIGURED", "telegram", {"enabled": payload.enabled, "chat_count": len(payload.allowed_chat_ids), "user_count": len(payload.allowed_user_ids)})
    db.commit()
    return metadata(db)


@router.get("/outbox")
def outbox(limit: int = Query(100, ge=1, le=500), db: Session = Depends(get_db), principal=Depends(identity)):
    return [serialize(row) for row in db.scalars(select(TelegramOutbox).order_by(TelegramOutbox.id.desc()).limit(limit))]


@router.post("/outbox/{identifier}")
def retry(identifier: int, payload: RetryInput, db: Session = Depends(get_db), user=Depends(operator)):
    row = db.get(TelegramOutbox, identifier)
    if not row:
        raise HTTPException(404, "Outbox item not found")
    if row.status == "SENT":
        raise HTTPException(409, "Delivered messages cannot be retried")
    row.status = "PENDING" if payload.action == "RETRY" else "CANCELED"
    if payload.action == "RETRY":
        row.attempts, row.next_attempt_at, row.last_error = 0, utcnow(), None
    audit(db, user.username, "TELEGRAM_OUTBOX_" + payload.action, str(identifier))
    db.commit()
    return serialize(row)


def enqueue(db: Session, source_key: str, chat_id: str, text: str):
    existing = db.scalar(select(TelegramOutbox).where(TelegramOutbox.source_key == source_key, TelegramOutbox.chat_id == chat_id))
    if existing:
        return existing
    row = TelegramOutbox(source_key=source_key, chat_id=chat_id, text=text[:3500])
    db.add(row)
    return row


def notification_text(notification) -> str:
    """Preserve explicit source scope; never infer trading mode from message prose.

    Current Notification records have no structured mode field. They must not
    inherit the PAPER command/account scope. Future explicit mode metadata is
    recognized only when it matches a known environment exactly.
    """
    mode = getattr(notification, "mode", None)
    label = mode if mode in ("BACKTEST", "PAPER", "DEMO", "LIVE") else "MODE UNSPECIFIED"
    return f"TradeBot Pro {label} [{notification.severity}]\n{notification.message}"


def enqueue_notifications(db: Session):
    config = db.scalar(select(TelegramSettings).where(TelegramSettings.id == 1).with_for_update())
    if not config or not config.enabled:
        return
    rows = list(db.scalars(select(Notification).where(Notification.id > config.last_notification_id).order_by(Notification.id).limit(100)))
    for notification in rows:
        for chat in config.allowed_chat_ids:
            enqueue(db, f"notification:{notification.id}", chat, notification_text(notification))
        config.last_notification_id = notification.id


def command_response(db: Session, command: str) -> str:
    """Read-only summaries. Never echo credential values or raw sensitive logs."""
    command = command.split("@", 1)[0].lower()
    account = db.get(PaperAccount, 1)
    value = account_dict(db, account)
    if command == "/status":
        return f"TradeBot Pro PAPER; live disabled. Account: {'initialized' if account else 'not initialized'}. Emergency stop: {bool(account and account.emergency_stop)}."
    if command in ("/balance", "/pnl"):
        if not value:
            return "PAPER account is not initialized."
        return f"PAPER {value['settlement_asset']} equity {value['equity']}; cash {value['cash']}; realized P&L {value['realized_pnl']}; unrealized P&L {value['unrealized_pnl']}; fees {value['fees_paid']}."
    if command == "/positions":
        rows = list(db.scalars(select(Position).where(Position.status == "OPEN").limit(20)))
        return "PAPER positions:\n" + ("\n".join(f"{row.symbol} {row.side} {row.quantity} contracts; protection {row.protection_verified}" for row in rows) or "No open positions.")
    if command == "/orders":
        rows = list(db.scalars(select(Order).order_by(Order.created_at.desc()).limit(10)))
        return "PAPER recent orders:\n" + ("\n".join(f"{row.symbol} {row.side} {row.order_type} {row.status}" for row in rows) or "No orders.")
    if command == "/strategies":
        rows = list(db.scalars(select(Strategy).order_by(Strategy.id).limit(20)))
        return "Strategies:\n" + ("\n".join(f"{row.name} v{row.current_version} {row.status}" for row in rows) or "No strategies.")
    if command == "/risk":
        risk = db.get(RiskConfig, 1)
        if not risk:
            return "PAPER risk configuration has not been initialized."
        return f"PAPER risk: entries enabled {risk.entries_enabled}; per-trade {risk.max_risk_per_trade}; portfolio {risk.max_total_risk}; daily loss {risk.max_daily_loss}; drawdown {risk.max_drawdown}; leverage {risk.max_leverage}."
    if command == "/health":
        rows = list(db.scalars(select(WorkerHeartbeat).order_by(WorkerHeartbeat.updated_at.desc()).limit(5)))
        return "Worker health:\n" + ("\n".join(f"{row.status}; updated {aware(row.updated_at).isoformat()}" for row in rows) or "No worker heartbeat.")
    if command == "/logs":
        rows = list(db.scalars(select(Audit).order_by(Audit.created_at.desc()).limit(10)))
        return "Recent audit actions:\n" + ("\n".join(f"{aware(row.created_at).isoformat()} {row.action}" for row in rows) or "No audit events.")
    return "Read-only commands: " + " ".join(COMMANDS)


def accept_update(db: Session, update: dict) -> bool:
    config = db.get(TelegramSettings, 1)
    if not config or not config.enabled:
        return False
    identifier = update.get("update_id")
    if not isinstance(identifier, int) or identifier <= config.last_update_id:
        return False
    message = update.get("message", {})
    chat, sender = message.get("chat", {}), message.get("from", {})
    # Both identifiers must match. Edited/channel/anonymous/service updates are ignored.
    valid = (str(chat.get("id")) in config.allowed_chat_ids and str(sender.get("id")) in config.allowed_user_ids
             and not sender.get("is_bot") and isinstance(message.get("text"), str))
    if valid:
        command = message["text"].strip().split(maxsplit=1)[0] if message["text"].strip() else ""
        if command.split("@", 1)[0].lower() in COMMANDS:
            enqueue(db, f"command:{identifier}", str(chat["id"]), command_response(db, command))
    config.last_update_id = identifier
    return bool(valid)


async def deliver_pending(db: Session, transport=None, allow_network: bool = False):
    """At-least-once alerts: Telegram has no client-message idempotency key."""
    config = db.get(TelegramSettings, 1)
    token = configured_token()
    enabled = os.getenv("TELEGRAM_DELIVERY_ENABLED", "false").lower() == "true"
    if not config or not config.enabled or not token or not enabled or not allow_network:
        return
    now = utcnow()
    rows = list(db.scalars(select(TelegramOutbox).where(TelegramOutbox.status.in_(["PENDING", "SENDING"]),
                        TelegramOutbox.next_attempt_at <= now).order_by(TelegramOutbox.id).limit(10).with_for_update(skip_locked=True)))
    claims = []
    for row in rows:
        if row.chat_id not in config.allowed_chat_ids:
            row.status = "CANCELED"
            continue
        row.status, row.attempts = "SENDING", row.attempts + 1
        row.next_attempt_at = now + timedelta(seconds=60)
        claims.append(row.id)
    # Persist the attempt before IO so a worker crash can retry after its lease expires.
    db.commit()
    async with httpx.AsyncClient(timeout=10, transport=transport) as client:
        for identifier in claims:
            row = db.get(TelegramOutbox, identifier)
            try:
                response = await client.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": row.chat_id, "text": row.text, "disable_web_page_preview": True})
                payload = response.json()
                if response.status_code == 200 and payload.get("ok") is True:
                    row.status, row.sent_at, row.last_error = "SENT", utcnow(), None
                else:
                    retry_after = payload.get("parameters", {}).get("retry_after", 0)
                    if not isinstance(retry_after, int):
                        retry_after = 0
                    row.status = "FAILED" if row.attempts >= 8 else "PENDING"
                    row.next_attempt_at = utcnow() + timedelta(seconds=max(min(retry_after, 3600), min(3600, 2 ** row.attempts * 5)))
                    row.last_error = f"Telegram rejected delivery (HTTP {response.status_code}); attempt {row.attempts}"
            except Exception:
                row.status = "FAILED" if row.attempts >= 8 else "PENDING"
                row.next_attempt_at = utcnow() + timedelta(seconds=min(3600, 2 ** row.attempts * 5))
                row.last_error = "Telegram delivery outcome uncertain; retry can duplicate an alert"
            db.commit()


async def poll_commands(db: Session, transport=None, allow_network: bool = False):
    config = db.get(TelegramSettings, 1)
    token = configured_token()
    if not config or not config.enabled or not token or not allow_network or os.getenv("TELEGRAM_DELIVERY_ENABLED", "false").lower() != "true":
        return
    try:
        async with httpx.AsyncClient(timeout=10, transport=transport) as client:
            response = await client.post(f"https://api.telegram.org/bot{token}/getUpdates", json={"offset": config.last_update_id + 1, "timeout": 0, "limit": 50, "allowed_updates": ["message"]})
            data = response.json()
            if response.status_code != 200 or data.get("ok") is not True or not isinstance(data.get("result"), list):
                return
            for update in sorted(data["result"], key=lambda value: value.get("update_id", 0)):
                accept_update(db, update)
        db.commit()
    except Exception:
        db.rollback()
        # Command polling failure has no effect on trading/recovery transactions.
