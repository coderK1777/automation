"""Real PostgreSQL locking with declared synthetic inputs in a disposable schema."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from sqlalchemy import select

pytestmark = pytest.mark.postgres


def require_postgres(factory):
    if factory.kw["bind"].dialect.name != "postgresql":
        pytest.skip("Requires isolated real PostgreSQL schema; run scripts/check-postgres.py")


def test_concurrent_executor_lease_has_one_owner(paper_fixture):
    from tradebot.models import ExecutorLease
    from tradebot.paper import claim_lease

    _, factory, _ = paper_fixture
    require_postgres(factory)
    begun, complete = Event(), Event()

    def contender():
        with factory() as second:
            begun.set()
            result = claim_lease(second, "second-worker")
            second.commit()
            complete.set()
            return result

    with factory() as first, ThreadPoolExecutor(max_workers=1) as executor:
        assert claim_lease(first, "first-worker")
        future = executor.submit(contender)
        assert begun.wait(2)
        assert not complete.wait(.1), "Account row lock must serialize the contender"
        first.commit()
        assert future.result(timeout=3) is False
    with factory() as db:
        assert db.get(ExecutorLease, 1).owner == "first-worker"


def test_concurrent_signal_creates_one_position_and_fill(paper_fixture):
    from tradebot.models import Fill, Position, Signal, Strategy
    from tradebot.paper import enter_signal
    from tradebot.repository import candle_rows, core_candles, version
    from tradebot_core import StrategyConfig, evaluate_strategy

    _, factory, identifier = paper_fixture
    require_postgres(factory)
    with factory() as db:
        config = StrategyConfig.model_validate(version(db, db.get(Strategy, identifier)).config)
        rows = candle_rows(db, config.symbol, config.timeframe_seconds)
        generated = evaluate_strategy(config, core_candles(rows))
        assert generated is not None

    def submit():
        with factory() as db:
            latest = candle_rows(db, config.symbol, config.timeframe_seconds)[-1]
            result = enter_signal(db, identifier, config, generated, latest, executor_healthy=True)
            db.commit()
            return result is not None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: submit(), range(2)))
    assert sorted(results) == [False, True]
    with factory() as db:
        assert len(list(db.scalars(select(Position)))) == 1
        assert len(list(db.scalars(select(Fill)))) == 1
        assert len(list(db.scalars(select(Signal)))) == 1
