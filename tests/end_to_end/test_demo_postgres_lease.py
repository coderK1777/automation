"""Real PostgreSQL locking with OFFLINE credential fixtures; no Delta requests."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from tradebot import demo
from tradebot.demo_models import DemoAccount
from tradebot_core.risk import RiskLimits

pytestmark = [pytest.mark.postgres, pytest.mark.mocked]


def test_demo_two_workers_one_postgres_lease(authenticated, monkeypatch):
    _, factory = authenticated
    monkeypatch.setenv("DELTA_DEMO_API_KEY", "offline-demo-concurrency-key")
    monkeypatch.setenv("DELTA_DEMO_API_SECRET", "offline-demo-concurrency-secret")
    with factory() as db:
        if db.bind.dialect.name != "postgresql":
            pytest.skip("Meaningful lease serialization requires real PostgreSQL")
        db.add(DemoAccount(id=1, credential_fingerprint=demo.fingerprint(), connected=True, equity=1000, available_margin=1000,
                           initial_equity=1000, peak_equity=1000, risk_limits=RiskLimits().model_dump(mode="json")))
        db.commit()
    barrier = Barrier(2)
    def claim(owner):
        barrier.wait(timeout=10)
        with factory() as db:
            acquired = demo.claim_lease(db, owner)
            db.commit()
            return acquired
    with ThreadPoolExecutor(max_workers=2) as threads:
        results = list(threads.map(claim, ("offline-worker-a", "offline-worker-b")))
    assert sum(results) == 1
    with factory() as db:
        account = db.get(DemoAccount, 1)
        assert account.owner == ("offline-worker-a" if results[0] else "offline-worker-b")
