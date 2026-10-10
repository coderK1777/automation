"""Clock-row locks begin after asynchronous public client closure."""
import asyncio
from datetime import timedelta
from email.utils import format_datetime
import time

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from tradebot import clock, market
from tradebot.data_models import TelemetryState
from tradebot.db import utcnow
from tradebot.models import Candle, Instrument
from tradebot_delta.clock import monotonic_domain


def closed_candle():
    timestamp = utcnow().replace(second=0, microsecond=0)-timedelta(minutes=1)
    return {"timestamp": int(timestamp.timestamp()), "open": "100", "high": "101",
            "low": "99", "close": "100", "volume": "10"}


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["discover", "import_candles"])
async def test_public_client_closes_before_clock_evidence_is_written(backend, monkeypatch, operation):
    import tradebot_delta
    _, factory = backend
    states, writes = [], []

    class ControlledClient:
        def __init__(self, *, environment, clock_observer):
            assert environment == "production"
            self.observer = clock_observer
            self.state = {"closed": False}
            states.append(self.state)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            assert writes == []
            await asyncio.sleep(0)  # Aclose yields while the session is alive.
            assert writes == []
            self.state["closed"] = True

        async def emit(self):
            first = {"owner": self.state, "sequence": 1}
            self.observer(first)
            await asyncio.sleep(0)
            self.observer({"owner": self.state, "sequence": 2})

        async def products(self, *, contract_types):
            assert contract_types == "perpetual_futures"
            await self.emit()
            return [{"id": 27, "symbol": "BTCUSD", "metadata_verified": True}]

        async def candles(self, symbol, resolution, start, end):
            assert symbol == "BTCUSD" and resolution == "1m" and start < end
            await self.emit()
            return [closed_candle()]

    def record_after_close(db, observation):
        assert observation["owner"]["closed"], "Clock-row write happened before async client closure"
        writes.append(observation["sequence"])

    monkeypatch.setattr(tradebot_delta, "DeltaClient", ControlledClient)
    monkeypatch.setattr(market, "record_clock_observation", record_after_close)
    with factory() as db:
        result = await market.discover(db) if operation == "discover" else await market.import_candles(db, "BTCUSD", 60, 2)
        db.commit()
        assert result == 1 and writes == [1, 2]
        assert len(states) == 1 and states[0]["closed"]
        assert db.scalar(select(Instrument if operation == "discover" else Candle)) is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["discover", "import_candles"])
@pytest.mark.parametrize("failure", ["response", "close"])
async def test_observed_clock_failure_is_retained_after_client_failure(backend, monkeypatch, operation, failure):
    import tradebot_delta
    _, factory = backend
    state, written = {"closed": False}, []
    unsafe_observation = {"declared_issue": "CLOCK_OFFSET_EXCEEDED"}

    class ControlledFailureClient:
        def __init__(self, *, environment, clock_observer):
            self.observer = clock_observer

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            assert written == []
            await asyncio.sleep(0)
            state["closed"] = True
            if failure == "close":
                raise RuntimeError("Declared async close failure")

        async def response(self):
            self.observer(unsafe_observation)
            if failure == "response":
                raise ValueError("Declared response parsing failure")
            return []

        async def products(self, **kwargs):
            return await self.response()

        async def candles(self, *args):
            return await self.response()

    def persist_after_unwind(db, observation):
        assert state["closed"], "Clock write occurred before client context unwound"
        written.append(observation)

    monkeypatch.setattr(tradebot_delta, "DeltaClient", ControlledFailureClient)
    monkeypatch.setattr(market, "record_clock_observation", persist_after_unwind)
    with factory() as db:
        with pytest.raises(ValueError if failure == "response" else RuntimeError, match="Declared"):
            if operation == "discover":
                await market.discover(db)
            else:
                await market.import_candles(db, "BTCUSD", 60, 2)
        assert written == [unsafe_observation]
        assert db.scalar(select(Instrument)) is None and db.scalar(select(Candle)) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["discover", "import_candles"])
async def test_unsafe_clock_evidence_survives_failed_market_transaction_rollback(backend, monkeypatch, operation):
    import tradebot_delta
    _, factory = backend
    closed = False

    class FailedResponseClient:
        def __init__(self, *, environment, clock_observer):
            self.observer = clock_observer

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            nonlocal closed
            await asyncio.sleep(0)
            closed = True

        async def fail_after_observation(self):
            now = utcnow()
            self.observer({"environment": "production", "source": "https://api.india.delta.exchange",
                "public_source_verified": True, "verification_scope": "CONTROLLED_TEST_FIXTURE",
                "date_header": format_datetime(now+timedelta(seconds=30), usegmt=True), "age_header": "0",
                "request_started_at": (now-timedelta(seconds=.05)).isoformat(), "response_received_at": now.isoformat(),
                "round_trip_seconds": .05, "wall_elapsed_seconds": .05,
                "monotonic_sample": time.monotonic(), "monotonic_domain": monotonic_domain()})
            raise ValueError("Declared malformed public response")

        async def products(self, **kwargs):
            return await self.fail_after_observation()

        async def candles(self, *args):
            return await self.fail_after_observation()

    monkeypatch.setattr(tradebot_delta, "DeltaClient", FailedResponseClient)
    with factory() as db:
        assert clock.clock_healthy(db)
        # Uncommitted market data must remain isolated from durable clock
        # evidence; the worker's error rollback discards this pending record.
        db.add(Instrument(id=999, symbol="DECLARED_FIXTURE", metadata_json={}, verified=False))
        with pytest.raises(ValueError, match="Declared malformed"):
            if operation == "discover":
                await market.discover(db)
            else:
                await market.import_candles(db, "BTCUSD", 60, 2)
        db.rollback()
    with factory() as db:
        assert closed and db.get(Instrument, 999) is None
        status = clock.clock_status(db)
        assert not status["healthy"] and "CLOCK_OFFSET_EXCEEDED" in status["issues"]
        row = db.get(TelemetryState, "clock:production:"+monotonic_domain())
        assert row.payload["blocking_observation"]["usable"] is False


@pytest.mark.asyncio
async def test_clock_evidence_does_not_require_a_second_caller_pool_connection(backend, monkeypatch):
    """Authentication may retain the last pooled slot throughout public IO."""
    import tradebot_delta
    _, factory = backend
    engine = create_engine(factory.kw["bind"].url, pool_size=1, max_overflow=0, pool_timeout=.1)

    class ObservedClient:
        def __init__(self, *, environment, clock_observer):
            self.observer = clock_observer

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            await asyncio.sleep(0)

        async def products(self, **kwargs):
            now = utcnow()
            self.observer({"environment": "production", "source": "https://api.india.delta.exchange",
                "public_source_verified": True, "verification_scope": "CONTROLLED_TEST_FIXTURE",
                "date_header": format_datetime(now+timedelta(seconds=30), usegmt=True), "age_header": "0",
                "request_started_at": (now-timedelta(seconds=.05)).isoformat(), "response_received_at": now.isoformat(),
                "round_trip_seconds": .05, "wall_elapsed_seconds": .05,
                "monotonic_sample": time.monotonic(), "monotonic_domain": monotonic_domain()})
            return []

    monkeypatch.setattr(tradebot_delta, "DeltaClient", ObservedClient)
    try:
        with Session(engine) as caller:
            # A normal identity read begins and retains the caller transaction.
            assert caller.scalar(select(TelemetryState)) is not None
            assert engine.pool.checkedout() == 1
            assert await asyncio.wait_for(market.discover(caller), timeout=1) == 0
            caller.rollback()
        with factory() as verifier:
            assert "CLOCK_OFFSET_EXCEEDED" in clock.clock_status(verifier)["issues"]
    finally:
        engine.dispose()


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_concurrent_public_functions_close_before_contending_for_clock_row(backend, monkeypatch):
    """Exercise the real row lock; SQL and coroutine bounds prevent hangs."""
    import tradebot_delta
    _, factory = backend
    with factory() as db:
        if db.get_bind().dialect.name != "postgresql":
            pytest.skip("Real PostgreSQL clock-row contention requires the isolated PostgreSQL test schema")
    closing = asyncio.Event()
    exit_count = 0
    samples = []

    class ControlledClient:
        def __init__(self, *, environment, clock_observer):
            assert environment == "production"
            self.observer = clock_observer
            self.closed = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            nonlocal exit_count
            exit_count += 1
            if exit_count == 2:
                closing.set()
            await asyncio.wait_for(closing.wait(), timeout=2)
            await asyncio.sleep(0)
            self.closed = True

        async def observe(self):
            now = utcnow()
            value = {"environment": "production", "source": "https://api.india.delta.exchange",
                "public_source_verified": True, "verification_scope": "CONTROLLED_TEST_FIXTURE",
                "date_header": format_datetime(now, usegmt=True), "age_header": "0",
                "request_started_at": (now-timedelta(seconds=.05)).isoformat(), "response_received_at": now.isoformat(),
                "round_trip_seconds": .05, "wall_elapsed_seconds": .05,
                "monotonic_sample": time.monotonic(), "monotonic_domain": monotonic_domain()}
            samples.append(value)
            self.observer(value)
            await asyncio.sleep(0)

        async def products(self, **kwargs):
            await self.observe()
            return [{"id": 27, "symbol": "BTCUSD", "metadata_verified": True}]

        async def candles(self, *args):
            await self.observe()
            return [closed_candle()]

    monkeypatch.setattr(tradebot_delta, "DeltaClient", ControlledClient)

    def bounded_clock_write(db, observation):
        # The implementation uses an independent Session for durable evidence,
        # so bounds belong to that Session rather than only the data caller.
        db.execute(text("SET LOCAL lock_timeout = '500ms'"))
        db.execute(text("SET LOCAL statement_timeout = '1500ms'"))
        clock.record_clock_observation(db, observation)

    monkeypatch.setattr(market, "record_clock_observation", bounded_clock_write)

    async def perform(operation):
        with factory() as db:
            # A regressed synchronous clock write fails in 500ms instead of
            # deadlocking the event loop while the first context awaits exit.
            db.execute(text("SET LOCAL lock_timeout = '500ms'"))
            db.execute(text("SET LOCAL statement_timeout = '1500ms'"))
            value = await market.discover(db) if operation == "discover" else await market.import_candles(db, "BTCUSD", 60, 2)
            db.commit()  # No asynchronous yield while owning clock-row locks.
            return value

    assert await asyncio.wait_for(asyncio.gather(perform("discover"), perform("import_candles")), timeout=5) == [1, 1]
    assert exit_count == 2 and len(samples) == 2
    with factory() as db:
        assert clock.clock_healthy(db)
        row = db.get(TelemetryState, "clock:production:"+monotonic_domain())
        assert row.payload["last_observed_monotonic"] == max(sample["monotonic_sample"] for sample in samples)
        assert db.scalar(select(Instrument)).symbol == "BTCUSD"
        assert db.scalar(select(Candle)).close == 100
