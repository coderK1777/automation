"""Notification rendering cannot relabel DEMO/shared evidence as PAPER.

These tests inspect isolated persisted outbox rows and never call transport.
"""
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from tradebot.models import Notification
from tradebot.notifications import enqueue_notifications, notification_text
from tradebot.notifications_models import TelegramOutbox, TelegramSettings


@pytest.mark.parametrize("mode", ["BACKTEST", "PAPER", "DEMO", "LIVE"])
def test_explicit_structured_mode_is_preserved(mode):
    notification = SimpleNamespace(mode=mode, severity="WARNING", message="Exact source message")
    text = notification_text(notification)
    assert text == f"TradeBot Pro {mode} [WARNING]\nExact source message"


@pytest.mark.parametrize("mode", [None, "DEMO guess", "SIMULATED", "PAPER\nLIVE", "demo"])
def test_invalid_or_absent_mode_is_never_guessed(mode):
    notification = SimpleNamespace(mode=mode, severity="INFO", message="DEMO is mentioned in shared source text")
    text = notification_text(notification)
    assert text == "TradeBot Pro MODE UNSPECIFIED [INFO]\nDEMO is mentioned in shared source text"


def test_persisted_demo_paper_and_shared_messages_keep_scope_without_transport(backend):
    _, factory = backend
    messages = ["DEMO loss/drawdown limit breached; protection retained", "PAPER position BTCUSD closed", "Authorized source mentions PAPER and DEMO"]
    with factory() as db:
        db.add(TelegramSettings(id=1, enabled=True, allowed_chat_ids=["10"], allowed_user_ids=["20"]))
        for message in messages:
            db.add(Notification(severity="WARNING", message=message))
        db.commit()
        enqueue_notifications(db)
        db.commit()
        enqueue_notifications(db)
        db.commit()
        rows = list(db.scalars(select(TelegramOutbox).order_by(TelegramOutbox.id)))
        assert len(rows) == len(messages)
        assert [row.text.split("\n", 1)[1] for row in rows] == messages
        assert all(row.text.startswith("TradeBot Pro MODE UNSPECIFIED [WARNING]\n") for row in rows)
        assert "PAPER" not in rows[0].text
        assert all(row.status == "PENDING" and row.attempts == 0 for row in rows)
