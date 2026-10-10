"""Controlled clock measurements; no exchange/signing-clock certification."""
from datetime import timedelta
from email.utils import format_datetime
import time

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from tradebot import clock
from tradebot.db import Base, utcnow
from tradebot_delta import DeltaClient
from tradebot_delta.clock import monotonic_domain


@pytest.fixture
def clock_db(tmp_path):
    engine = create_engine("sqlite:///"+str(tmp_path/"clock-evidence.db"))
    Base.metadata.create_all(engine)
    with sessionmaker(engine, expire_on_commit=False)() as db:
        yield db
    engine.dispose()


def observation(**changes):
    now = utcnow()
    data = {"environment": "production", "source": "https://api.india.delta.exchange", "public_source_verified": True,
            "date_header": format_datetime(now, usegmt=True), "age_header": "0",
            "request_started_at": (now-timedelta(seconds=.1)).isoformat(), "response_received_at": now.isoformat(),
            "round_trip_seconds": .1, "wall_elapsed_seconds": .1, "verification_scope": "CONTROLLED_TEST_FIXTURE",
            "monotonic_sample": time.monotonic(), "monotonic_domain": monotonic_domain()}
    return {**data, **changes}


def record(db, value):
    clock.record_clock_observation(db, value)
    db.flush()


def test_clock_missing_evidence_is_not_healthy(clock_db):
    assert clock.clock_healthy(clock_db) is False
    assert clock.clock_status(clock_db)["issues"] == ["CLOCK_EVIDENCE_MISSING"]


def test_clock_date_quantization_and_half_rtt_are_included(clock_db):
    record(clock_db, observation())
    state = clock.clock_status(clock_db)
    assert state["healthy"]
    assert state["sample"]["uncertainty_seconds"] == pytest.approx(.55)
    assert state["sample"]["absolute_offset_bound_seconds"] >= .55
    assert "intermediary" in state["caveat"] and "does not certify" in state["caveat"]


@pytest.mark.parametrize("change", [{"date_header": None}, {"date_header": "invalid-date"}, {"age_header": "20"},
        {"round_trip_seconds": 3, "wall_elapsed_seconds": 3}, {"public_source_verified": False},
        {"source": "https://unrelated.example.com"}])
def test_unusable_clock_samples_cannot_create_health(clock_db, change):
    record(clock_db, observation(**change))
    assert clock.clock_healthy(clock_db) is False


@pytest.mark.parametrize("change", [{"date_header": None}, {"date_header": "invalid-date"}, {"age_header": "20"}])
def test_missing_or_cached_attempt_does_not_erase_fresh_usable_evidence(clock_db, change):
    record(clock_db, observation())
    initial = clock.clock_status(clock_db)["sample"]
    record(clock_db, observation(**change))
    state = clock.clock_status(clock_db)
    assert state["healthy"] and state["sample"] == initial
    assert state["last_attempt"]["usable"] is False


def test_clock_usable_evidence_expires(clock_db, monkeypatch):
    record(clock_db, observation())
    now = utcnow()
    monkeypatch.setattr(clock, "utcnow", lambda: now+timedelta(seconds=clock.MAX_AGE_SECONDS+1))
    assert not clock.clock_healthy(clock_db)
    assert "CLOCK_EVIDENCE_STALE" in clock.clock_status(clock_db)["issues"]


def test_backward_local_clock_jump_makes_previous_sample_future(clock_db, monkeypatch):
    record(clock_db, observation())
    now = utcnow()
    monkeypatch.setattr(clock, "utcnow", lambda: now-timedelta(seconds=10))
    assert not clock.clock_healthy(clock_db)
    assert "CLOCK_EVIDENCE_IN_FUTURE" in clock.clock_status(clock_db)["issues"]


@pytest.mark.parametrize("skew", [-30, 30])
def test_measured_offset_blocks_immediately_and_cached_attempt_cannot_clear_it(clock_db, skew):
    record(clock_db, observation())
    date = format_datetime(utcnow()+timedelta(seconds=skew), usegmt=True)
    record(clock_db, observation(date_header=date))
    assert not clock.clock_healthy(clock_db)
    assert "CLOCK_OFFSET_EXCEEDED" in clock.clock_status(clock_db)["issues"]
    record(clock_db, observation(age_header="10"))
    assert not clock.clock_healthy(clock_db)
    record(clock_db, observation())
    assert clock.clock_healthy(clock_db)


def test_roundtrip_wall_clock_jump_blocks_immediately(clock_db):
    record(clock_db, observation())
    now = utcnow()
    record(clock_db, observation(request_started_at=(now-timedelta(seconds=10)).isoformat(),
                               response_received_at=now.isoformat(), wall_elapsed_seconds=10, round_trip_seconds=.1))
    assert not clock.clock_healthy(clock_db)
    assert "WALL_CLOCK_JUMP" in clock.clock_status(clock_db)["issues"]


def test_future_sample_blocks_without_healthy_defaults(clock_db):
    future = utcnow()+timedelta(seconds=10)
    record(clock_db, observation(request_started_at=(future-timedelta(seconds=.1)).isoformat(),
            response_received_at=future.isoformat(), date_header=format_datetime(future, usegmt=True)))
    assert not clock.clock_healthy(clock_db)
    assert "FUTURE_LOCAL_SAMPLE" in clock.clock_status(clock_db)["issues"]


def test_delayed_valid_response_cannot_clear_newer_unsafe_observation(clock_db):
    older_valid = observation()
    record(clock_db, older_valid)
    newer_unsafe = observation(date_header=format_datetime(utcnow()+timedelta(seconds=30), usegmt=True))
    record(clock_db, newer_unsafe)
    record(clock_db, older_valid)
    state = clock.clock_status(clock_db)
    assert not state["healthy"]
    assert "CLOCK_OFFSET_EXCEEDED" in state["issues"]
    assert state["last_attempt"]["monotonic_sample"] == newer_unsafe["monotonic_sample"]
    record(clock_db, observation())
    assert clock.clock_healthy(clock_db)


def test_new_backward_wall_jump_blocks_even_when_wall_timestamp_is_earlier(clock_db):
    older_valid = observation()
    record(clock_db, older_valid)
    now = utcnow()
    newer_jump = observation(request_started_at=(now-timedelta(seconds=.1)).isoformat(),
            response_received_at=(now-timedelta(seconds=10)).isoformat(), wall_elapsed_seconds=-9.9)
    assert newer_jump["response_received_at"] < older_valid["response_received_at"]
    assert newer_jump["monotonic_sample"] > older_valid["monotonic_sample"]
    record(clock_db, newer_jump)
    record(clock_db, older_valid)
    assert not clock.clock_healthy(clock_db)
    assert "WALL_CLOCK_JUMP" in clock.clock_status(clock_db)["issues"]


def test_clock_evidence_is_isolated_by_monotonic_domain(clock_db, monkeypatch):
    record(clock_db, observation())
    assert clock.clock_healthy(clock_db)
    monkeypatch.setattr(clock, "monotonic_domain", lambda: "different-host-or-boot")
    assert clock.clock_status(clock_db)["issues"] == ["CLOCK_EVIDENCE_MISSING"]


@pytest.mark.parametrize("change", [{"monotonic_domain": None}, {"monotonic_domain": "unrelated-domain"},
        {"monotonic_sample": float("nan")}])
def test_unknown_monotonic_provenance_cannot_create_clock_health(clock_db, change):
    record(clock_db, observation(**change))
    assert not clock.clock_healthy(clock_db)


def test_future_monotonic_sample_blocks(clock_db):
    record(clock_db, observation())
    record(clock_db, observation(monotonic_sample=time.monotonic()+10))
    assert not clock.clock_healthy(clock_db)
    assert "FUTURE_MONOTONIC_SAMPLE" in clock.clock_status(clock_db)["issues"]


def test_clock_status_refreshes_existing_session_state(backend):
    from tradebot.data_models import TelemetryState
    _, factory = backend
    with factory() as reader:
        assert clock.clock_healthy(reader)
        cached_row = reader.get(TelemetryState, "clock:production:"+monotonic_domain())
        assert cached_row.payload["blocking_observation"] is None
        with factory() as writer:
            record(writer, observation(date_header=format_datetime(utcnow()+timedelta(seconds=30), usegmt=True)))
            writer.commit()
        assert not clock.clock_healthy(reader)
        assert cached_row.payload["blocking_observation"] is not None


def test_postgres_row_lock_preserves_newer_unsafe_sample(backend):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    _, factory = backend
    with factory() as db:
        if db.get_bind().dialect.name != "postgresql":
            pytest.skip("PostgreSQL row-lock behavior is verified by the isolated PostgreSQL integration run")
    older_valid = observation()
    newer_unsafe = observation(date_header=format_datetime(utcnow()+timedelta(seconds=30), usegmt=True))
    writer_locked, release_writer, reader_started = Event(), Event(), Event()

    def unsafe_writer():
        with factory() as db:
            record(db, newer_unsafe)
            writer_locked.set()
            assert release_writer.wait(5)
            db.commit()

    def delayed_writer():
        from tradebot.data_models import TelemetryState
        with factory() as db:
            # Load the old row first, then require SELECT FOR UPDATE to
            # refresh that identity after the other transaction commits.
            assert clock.clock_healthy(db)
            cached_row = db.get(TelemetryState, "clock:production:"+monotonic_domain())
            assert cached_row.payload["blocking_observation"] is None
            reader_started.set()
            record(db, older_valid)
            assert cached_row.payload["blocking_observation"] is not None
            db.commit()

    with ThreadPoolExecutor(max_workers=2) as workers:
        first = workers.submit(unsafe_writer)
        assert writer_locked.wait(5)
        second = workers.submit(delayed_writer)
        try:
            assert reader_started.wait(5)
            assert not second.done()
        finally:
            release_writer.set()
        first.result(timeout=5)
        second.result(timeout=5)
    with factory() as db:
        assert not clock.clock_healthy(db)
        assert "CLOCK_OFFSET_EXCEEDED" in clock.clock_status(db)["issues"]


@pytest.mark.asyncio
async def test_mocked_delta_rest_callback_identifies_unverified_transport():
    samples = []
    now = utcnow()
    def transport(request):
        return httpx.Response(200, json={"success": True, "result": {}},
                              headers={"Date": format_datetime(now, usegmt=True), "Age": "0"})
    async with DeltaClient(transport=httpx.MockTransport(transport), clock_observer=samples.append) as client:
        await client.ticker("BTCUSD")
    assert len(samples) == 1
    assert samples[0]["source"] == "https://api.india.delta.exchange"
    assert samples[0]["environment"] == "production"
    assert samples[0]["public_source_verified"] is False
    assert samples[0]["verification_scope"] == "CONTROLLED_TRANSPORT"
    assert samples[0]["monotonic_domain"] == monotonic_domain()
    assert 0 <= samples[0]["monotonic_sample"] <= time.monotonic()
    assert not clock.evaluate_observation(samples[0])["usable"]
