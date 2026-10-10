"""Reports, authorized feeds and notifications use independent transactions.

Recovery and protection never wait for this process. Telegram delivery remains
disabled until both secure configuration and the stored allowlists enable it.
"""
import asyncio
import logging
import os
import signal
import uuid

from tradebot import model_registry  # noqa: F401
from tradebot.db import session_factory
from tradebot.telemetry import configure_logging, record_process_start
from tradebot.worker import heartbeat

logger = logging.getLogger("tradebot.auxiliary")


async def tick():
    from tradebot import news, notifications, reporting
    with session_factory()() as db:
        reporting.report_tick(db)
        notifications.enqueue_notifications(db)
        db.commit()
    with session_factory()() as db:
        await news.news_tick(db)
        db.commit()
    # Each transport commits separately. Notification delivery uncertainty
    # remains in the outbox rather than being retried as an execution action.
    enabled = os.getenv("TELEGRAM_DELIVERY_ENABLED", "false").lower() == "true"
    with session_factory()() as db:
        await notifications.deliver_pending(db, allow_network=enabled)
        db.commit()
    with session_factory()() as db:
        await notifications.poll_commands(db, allow_network=enabled)
        db.commit()


async def run():
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stopped.set)
    owner = "aux:"+uuid.uuid4().hex
    record_process_start("auxiliary-worker")
    while not stopped.is_set():
        try:
            await tick()
            heartbeat(owner, "RUNNING")
        except Exception:
            logger.exception("Auxiliary cycle failed")
            try:
                heartbeat(owner, "ERROR", "Auxiliary cycle failed; trading protection remains independently managed")
            except Exception:
                logger.error("Auxiliary heartbeat unavailable")
        try:
            await asyncio.wait_for(stopped.wait(), timeout=30)
        except TimeoutError:
            pass
    heartbeat(owner, "STOPPED")


if __name__ == "__main__":
    configure_logging("auxiliary-worker")
    asyncio.run(run())
