"""Isolated observed-data/recovery tests; exchange transports are offline mocks."""
import asyncio
from datetime import timedelta
from decimal import Decimal
import json
import logging

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from tradebot.data_models import CandleRevision, HistoricalDataset, MarketDataGap, MarketFeedState
from tradebot.data_provenance import DELTA_HISTORY_SOURCE, canonical_decimal, create_dataset, dataset_candles, record_revision, track_gaps
from tradebot.db import Base, utcnow
from tradebot import market, market_stream, telemetry
from tradebot.models import Candle, PaperAccount
from tradebot.notifications_models import TelegramOutbox  # noqa: F401


@pytest.fixture
def observed_db(tmp_path):
    engine = create_engine("sqlite:///"+str(tmp_path/"observed-data.db"))
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as db:
        yield db, factory
    engine.dispose()


def bar(timestamp, close="100"):
    value = Decimal(close)
    return Candle(symbol="BTCUSD", timeframe_seconds=60, timestamp=timestamp, open=value,
                  high=value+1, low=value-1, close=value, volume=Decimal("10"))


def ticker_event(stamp=None, price="100"):
    stamp = stamp or utcnow()
    return {"type": "ticker", "sy": "BTCUSD", "ts": int(stamp.timestamp()*1_000_000), "sp": price,
            "d": [{"s": "BTCUSD", "m": price, "m24hc": "1.0", "ohlc": [price, price, price, price],
                   "q": ["101", "2", "99", "3", None]}]}


def test_revision_decimal_canonicalization_never_rounds_to_ambient_precision():
    first = "123456789012345678.123456789011"
    second = "123456789012345678.123456789012"
    assert canonical_decimal(first) == first
    assert canonical_decimal(second) == second
    assert canonical_decimal("1.00000") == "1"
    assert canonical_decimal("-0.000") == "0"


def test_dataset_pins_original_revision_after_exchange_correction(observed_db):
    db, _ = observed_db
    timestamp = utcnow().replace(second=0, microsecond=0)-timedelta(minutes=2)
    row = bar(timestamp)
    db.add(row)
    original = record_revision(db, row, DELTA_HISTORY_SOURCE)
    first = create_dataset(db, "BTCUSD", 60)
    assert first.complete and first.candle_count == 1
    assert create_dataset(db, "BTCUSD", 60).id == first.id
    row.open, row.high, row.low, row.close = Decimal(110), Decimal(111), Decimal(109), Decimal(110)
    corrected = record_revision(db, row, DELTA_HISTORY_SOURCE)
    second = create_dataset(db, "BTCUSD", 60)
    db.commit()
    assert corrected.id != original.id and first.id != second.id
    assert dataset_candles(db, first.id)[0]["close"] == "100"
    assert dataset_candles(db, second.id)[0]["close"] == "110"
    assert db.scalar(select(func.count()).select_from(CandleRevision)) == 2


def test_historical_dataset_and_observation_cannot_be_mutated(observed_db):
    db, _ = observed_db
    row = bar(utcnow().replace(second=0, microsecond=0)-timedelta(minutes=2))
    db.add(row)
    revision = record_revision(db, row, DELTA_HISTORY_SOURCE)
    snapshot = create_dataset(db, "BTCUSD", 60)
    db.commit()
    revision.source = "fabricated-change"
    with pytest.raises(ValueError, match="immutable"):
        db.flush()
    db.rollback()
    stored = db.get(HistoricalDataset, snapshot.id)
    stored.complete = False
    with pytest.raises(ValueError, match="immutable"):
        db.flush()
    db.rollback()


def test_legacy_source_and_missing_bars_are_explicit_never_fabricated(observed_db):
    db, _ = observed_db
    timestamp = utcnow().replace(second=0, microsecond=0)-timedelta(minutes=4)
    db.add_all([bar(timestamp), bar(timestamp+timedelta(minutes=2))])
    snapshot = create_dataset(db, "BTCUSD", 60)
    db.commit()
    assert not snapshot.complete
    assert snapshot.missing_ranges[0]["missing_bars"] == 1
    assert any("unknown" in value for value in snapshot.warnings)
    assert len(dataset_candles(db, snapshot.id)) == 2
    assert db.scalar(select(func.count()).select_from(Candle)) == 2


def test_gap_resolves_only_after_actual_missing_observation(observed_db):
    db, _ = observed_db
    timestamp = utcnow().replace(second=0, microsecond=0)-timedelta(minutes=4)
    db.add_all([bar(timestamp), bar(timestamp+timedelta(minutes=2))])
    db.flush()
    track_gaps(db, "BTCUSD", 60, timestamp, timestamp+timedelta(minutes=2))
    row = db.scalar(select(MarketDataGap))
    assert row.status == "OPEN"
    track_gaps(db, "BTCUSD", 60, timestamp, timestamp+timedelta(minutes=2))
    assert db.scalar(select(func.count()).select_from(MarketDataGap)) == 1
    assert row.status == "OPEN"
    db.add(bar(timestamp+timedelta(minutes=1)))
    db.flush()
    assert track_gaps(db, "BTCUSD", 60, timestamp, timestamp+timedelta(minutes=2)) == []
    assert row.status == "RESOLVED" and row.resolved_at is not None


@pytest.mark.asyncio
async def test_incremental_import_retains_exchange_revisions_and_skips_unclosed_bar(observed_db, monkeypatch):
    import tradebot_delta
    db, _ = observed_db
    now = utcnow().replace(second=30, microsecond=0)
    monkeypatch.setattr(market, "utcnow", lambda: now)
    first_time = now.replace(second=0)-timedelta(minutes=1)
    values = [{"timestamp": int(first_time.timestamp()), "open": "100", "high": "101", "low": "99", "close": "100", "volume": "10"},
              {"timestamp": int(now.replace(second=0).timestamp()), "open": "100", "high": "101", "low": "99", "close": "100", "volume": "10"}]

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["environment"] == "production"
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_):
            pass
        async def candles(self, *args):
            return values

    monkeypatch.setattr(tradebot_delta, "DeltaClient", Client)
    assert await market.import_candles(db, "BTCUSD", 60, 2) == 1
    assert await market.import_candles(db, "BTCUSD", 60, 2) == 0
    assert db.scalar(select(func.count()).select_from(CandleRevision)) == 1
    values[0].update(open="110", high="111", low="109", close="110")
    assert await market.import_candles(db, "BTCUSD", 60, 2) == 0
    assert db.scalar(select(func.count()).select_from(Candle)) == 1
    assert db.scalar(select(func.count()).select_from(CandleRevision)) == 2
    assert db.scalar(select(Candle)).close == Decimal(110)


def test_feed_duplicates_are_idempotent_and_reordering_requires_recovery(observed_db):
    db, _ = observed_db
    event = ticker_event()
    assert market_stream.store_event(db, event)
    db.flush()
    assert not market_stream.store_event(db, event)
    assert market_stream.feed_health(db, "BTCUSD")["healthy"]
    reordered = ticker_event(utcnow()-timedelta(seconds=1))
    with pytest.raises(ValueError, match="ordering"):
        market_stream.store_event(db, reordered)
    db.flush()
    state = db.scalar(select(MarketFeedState))
    assert state.status == "RECOVERING" and state.gap_count == 1
    assert not market_stream.feed_health(db, "BTCUSD")["healthy"]


def test_fresh_arrival_cannot_hide_an_old_exchange_event(observed_db):
    db, _ = observed_db
    market_stream.store_event(db, ticker_event(utcnow()-timedelta(seconds=60)))
    db.flush()
    row = market_stream.feed_rows(db)[0]
    assert row["status"] == "STALE" and row["age_seconds"] >= 60
    assert not market_stream.feed_health(db, "BTCUSD")["healthy"]


def test_intrabar_ws_candle_is_display_only_and_never_creates_history(observed_db):
    db, _ = observed_db
    event = {"type": "candlestick_1m", "res": "1m", "sy": "BTCUSD", "ts": int(utcnow().timestamp()*1_000_000),
             "o": "100", "h": "101", "l": "99", "c": "100", "v": "2"}
    market_stream.store_event(db, event)
    db.flush()
    assert market_stream.feed_rows(db)[0]["payload"]["closed"] is False
    assert db.scalar(select(func.count()).select_from(Candle)) == 0
    assert db.scalar(select(func.count()).select_from(CandleRevision)) == 0


@pytest.mark.parametrize("fault", ["crossed", "unordered", "negative_size"])
def test_invalid_book_snapshot_is_rejected(observed_db, fault):
    db, _ = observed_db
    event = {"type": "ob_l2", "sy": "BTCUSD", "ts": int(utcnow().timestamp()*1_000_000),
             "a": [["101", "1"], ["102", "2"]], "b": [["99", "1"], ["98", "2"]]}
    if fault == "crossed":
        event["b"][0][0] = "101"
    elif fault == "unordered":
        event["a"].reverse()
    else:
        event["a"][0][1] = "-1"
    with pytest.raises(ValueError):
        market_stream.store_event(db, event)
    assert db.scalar(select(func.count()).select_from(MarketFeedState)) == 0


@pytest.mark.asyncio
async def test_rest_snapshot_does_not_claim_websocket_continuity(observed_db):
    db, factory = observed_db

    class Client:
        async def ticker(self, symbol):
            return {"symbol": symbol, "close": "100", "mark_price": "100", "quotes": {"best_bid": "99", "best_ask": "101"}}

    await market_stream._rest_recovery(Client(), factory, "ticker", ["BTCUSD"])
    row = market_stream.feed_rows(db)[0]
    assert row["status"] == "REST_SNAPSHOT"
    assert not market_stream.feed_health(db, "BTCUSD")["healthy"]


def test_central_producer_lease_blocks_second_worker_until_expiry(observed_db):
    db, factory = observed_db
    assert market_stream.claim_stream_lease(db, "first")
    db.commit()
    with factory() as contender:
        assert not market_stream.claim_stream_lease(contender, "second")
        contender.commit()
    from tradebot.data_models import MarketStreamLease
    lease = db.get(MarketStreamLease, market_stream.LEASE_ID)
    lease.expires_at = utcnow()-timedelta(seconds=1)
    db.commit()
    with factory() as contender:
        assert market_stream.claim_stream_lease(contender, "second")
        contender.commit()


@pytest.mark.asyncio
async def test_snapshot_stream_rechecks_session_revocation_without_retaining_auth_transaction(observed_db, monkeypatch):
    from tradebot.models import AuthSession, User
    db, factory = observed_db
    user = User(username="offline-stream-user", password_hash="unused-test-hash", role="viewer")
    db.add(user)
    db.flush()
    auth = AuthSession(id="offline-stream-session", user_id=user.id, csrf_token="offline-csrf",
                       expires_at=utcnow()+timedelta(minutes=5))
    db.add(auth)
    db.commit()

    class Request:
        async def is_disconnected(self):
            return False

    async def sleep(_):
        return None

    monkeypatch.setattr(market_stream.asyncio, "sleep", sleep)
    response = await market_stream.market_stream(Request(), db, (user, auth))
    first = await anext(response.body_iterator)
    assert "event: snapshot" in first and '"sequence":1' in first
    assert "offline-csrf" not in first and "offline-stream-session" not in first
    with factory() as revoke:
        revoke.delete(revoke.get(AuthSession, auth.id))
        revoke.commit()
    with pytest.raises(StopAsyncIteration):
        await anext(response.body_iterator)


def test_json_logs_redact_structured_and_unstructured_secrets():
    record = logging.LogRecord("test", logging.ERROR, __file__, 1,
            "password=private-pass api-key=private-key Bearer private-token postgresql://user:db-pass@host/db", (), None)
    record.details = {"api_secret": "private-secret", "Authorization": "Bearer private-bearer", "safe": "BTCUSD"}
    result = telemetry.StructuredFormatter().format(record)
    for secret in ("private-pass", "private-key", "private-token", "db-pass", "private-secret", "private-bearer"):
        assert secret not in result
    assert json.loads(result)["details"]["safe"] == "BTCUSD"


@pytest.mark.parametrize("text", [
    "Authorization: Bearer offline~header-token-fixture",
    '{"api-key": "offline-json-key-fixture", "api_secret": "offline-json-secret-fixture", "Cookie": "tradebot_session=offline-cookie-fixture"}',
])
def test_json_logs_redact_complete_auth_headers_and_quoted_credential_keys(text):
    record = logging.LogRecord("test", logging.INFO, __file__, 1, text, (), None)
    body = telemetry.StructuredFormatter().format(record)
    assert "offline" not in body
    assert "[REDACTED]" in body


def test_backup_and_reconciliation_metrics_preserve_last_actual_success(observed_db):
    db, _ = observed_db
    db.add(PaperAccount(id=1, initial_balance=Decimal(1000), cash=Decimal(1000), high_water_equity=Decimal(1000)))
    telemetry.record_backup(True, 123, db=db)
    telemetry.record_backup(False, db=db)
    telemetry.record_reconciliation("PAPER", True, db=db)
    telemetry.record_reconciliation("PAPER", False, db=db)
    db.flush()
    result = telemetry.monitoring(db, None)
    assert result["backup"]["last_attempt_success"] is False
    assert result["backup"]["last_success_bytes"] == 123
    assert result["backup"]["age_seconds"] is not None
    assert result["reconciliation"]["PAPER"]["last_attempt_success"] is False
    assert result["reconciliation"]["PAPER"]["age_seconds"] is not None
    metrics = telemetry.metrics_response(db).body.decode()
    assert "tradebot_database_available 1.0" in metrics
    assert "tradebot_backup_last_attempt_success 0.0" in metrics
    assert "tradebot_worker_healthy 0.0" in metrics


def test_metrics_database_failure_never_reports_healthy():
    class Unavailable:
        def execute(self, *args):
            raise RuntimeError("injected database outage")
    metrics = telemetry.metrics_response(Unavailable()).body.decode()
    assert "tradebot_database_available 0.0" in metrics
    assert "tradebot_worker_healthy 1" not in metrics


@pytest.mark.parametrize("paper_status", ["RUNNING", "ERROR", "STOPPED"])
def test_healthy_auxiliary_worker_cannot_hide_unavailable_paper_worker(observed_db, paper_status):
    from prometheus_client.parser import text_string_to_metric_families
    from tradebot.models import WorkerHeartbeat
    db, _ = observed_db
    db.add_all([WorkerHeartbeat(id="paper-owner", status=paper_status, updated_at=utcnow()-timedelta(minutes=5)),
                WorkerHeartbeat(id="aux:notification-owner", status="RUNNING", updated_at=utcnow())])
    db.flush()
    body = telemetry.metrics_response(db).body.decode()
    samples = {sample.name: sample.value for family in text_string_to_metric_families(body) for sample in family.samples}
    assert samples["tradebot_database_available"] == 1
    assert samples["tradebot_worker_healthy"] == 0
    assert samples["tradebot_auxiliary_worker_healthy"] == 1
    age = samples["tradebot_worker_last_success_age_seconds"]
    assert age >= 300 if paper_status == "RUNNING" else age == -1


@pytest.mark.asyncio
@pytest.mark.parametrize("channel,symbol", [("ob_l2", "BTCUSD"), ("mark_price", "MARK:BTCUSD"), ("funding_rate", "BTCUSD")])
async def test_mocked_new_public_channels_use_documented_public_socket(monkeypatch, channel, symbol):
    from tradebot_delta import DeltaClient, client as adapter
    sent, targets = [], []

    class Socket:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_):
            pass
        async def send(self, value):
            sent.append(json.loads(value))
        async def recv(self):
            await asyncio.sleep(0)
            return json.dumps({"type": channel, "sy": symbol, "ts": int(utcnow().timestamp()*1_000_000)})

    def connect(target, **kwargs):
        targets.append(target)
        return Socket()

    monkeypatch.setattr(adapter, "connect", connect)
    async with DeltaClient(environment="production") as client:
        stream = client.public_messages(channel, [symbol])
        assert (await anext(stream))["type"] == channel
        await stream.aclose()
    assert targets == ["wss://public-socket.india.delta.exchange"]
    assert sent[0]["payload"]["channels"] == [{"name": channel, "symbols": [symbol]}]
