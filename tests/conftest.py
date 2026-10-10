"""Backend tests use a fresh isolated SQLite file and declared market fixtures."""
from datetime import timedelta
from decimal import Decimal
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def backend(tmp_path, monkeypatch):
    from tradebot.config import get_settings
    test_url = os.getenv("TEST_DATABASE_URL", "sqlite:///" + str(tmp_path / "paper-test.db"))
    monkeypatch.setenv("DATABASE_URL", test_url)
    monkeypatch.setenv("APP_ORIGIN", "http://testserver")
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "false")
    monkeypatch.setenv("LIVE_EXECUTION_ENABLED", "false")
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    get_settings.cache_clear()
    from tradebot.app import app
    from tradebot.db import Base, get_db
    from tradebot.models import User
    from tradebot.security import password_hasher
    if not test_url.startswith("sqlite"):
        # A PostgreSQL run must use a separately created schema. Never clean
        # application public tables merely because someone passed a URL.
        assert "search_path" in test_url and "tradebot_test" in test_url, "TEST_DATABASE_URL requires an isolated tradebot_test schema"
    test_engine = create_engine(test_url, connect_args={"check_same_thread": False} if test_url.startswith("sqlite") else {})
    Base.metadata.drop_all(test_engine)
    Base.metadata.create_all(test_engine)
    factory = sessionmaker(test_engine, expire_on_commit=False)
    import tradebot.app as application
    monkeypatch.setattr(application, "session_factory", lambda: factory)
    with factory() as db:
        db.add(User(username="operator", password_hash=password_hasher.hash("test-only-password"), role="operator"))
        db.add(User(username="viewer", password_hash=password_hasher.hash("test-only-password"), role="viewer"))
        # Controlled clock evidence belongs only to this isolated test DB.
        # Application/cloud bootstrap never creates a healthy clock default.
        from email.utils import format_datetime
        from tradebot.clock import record_clock_observation
        from tradebot_delta.clock import monotonic_domain
        import time
        from tradebot.db import utcnow
        sampled = utcnow()
        record_clock_observation(db, {"environment": "production", "source": "https://api.india.delta.exchange",
                "public_source_verified": True, "date_header": format_datetime(sampled, usegmt=True), "age_header": "0",
                "request_started_at": (sampled-timedelta(seconds=.05)).isoformat(), "response_received_at": sampled.isoformat(),
                "round_trip_seconds": .05, "wall_elapsed_seconds": .05, "verification_scope": "CONTROLLED_TEST_FIXTURE",
                "monotonic_sample": time.monotonic(), "monotonic_domain": monotonic_domain()})
        db.commit()

    def test_db():
        with factory() as db:
            try:
                yield db
            except Exception:
                db.rollback()
                raise

    app.dependency_overrides[get_db] = test_db
    with TestClient(app) as client:
        yield client, factory
    app.dependency_overrides.clear()
    Base.metadata.drop_all(test_engine)
    test_engine.dispose()
    get_settings.cache_clear()


@pytest.fixture
def authenticated(backend):
    client, factory = backend
    login = client.post("/api/v1/auth/login", json={"username": "operator", "password": "test-only-password"})
    assert login.status_code == 200, login.text
    client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
    return client, factory


@pytest.fixture
def paper_fixture(authenticated):
    """Synthetic OHLC and verified contract fixture: never inserted in app DB."""
    from tradebot.db import utcnow
    from tradebot.models import Candle, Instrument
    from tradebot_core import Instrument as CoreInstrument
    client, factory = authenticated
    response = client.post("/api/v1/paper/account", json={"initial_balance": "10000", "settlement_asset": "USD", "planning_capital_inr": "50000"})
    assert response.status_code == 201, response.text
    contract = CoreInstrument(product_id=27, symbol="BTCUSD", underlying_asset="BTC", quote_asset="USD", settlement_asset="USD",
                              contract_size=Decimal("0.001"), tick_size=Decimal("0.5"), quantity_step=Decimal(1), min_quantity=Decimal(1),
                              initial_margin_rate=Decimal("0.5"), metadata_verified=True, metadata_timestamp=utcnow())
    latest = utcnow().replace(second=0, microsecond=0) - timedelta(minutes=1)
    with factory() as db:
        db.add(Instrument(id=27, symbol="BTCUSD", metadata_json=contract.model_dump(mode="json"), verified=True))
        for index in range(80):
            price = Decimal(60000+index*50)
            db.add(Candle(symbol="BTCUSD", timeframe_seconds=60, timestamp=latest-timedelta(minutes=79-index),
                          open=price, high=price+20, low=price-20, close=price, volume=Decimal(100)))
        db.commit()
    strategy = client.post("/api/v1/strategies", json={"name": "Synthetic test breakout", "config": {"kind": "BREAKOUT", "symbol": "BTCUSD", "timeframe_seconds": 60, "lookback": 3, "fast_period": 2, "slow_period": 3}})
    assert strategy.status_code == 201, strategy.text
    identifier = strategy.json()["id"]
    assert client.post(f"/api/v1/strategies/{identifier}/validate").status_code == 200
    run = client.post("/api/v1/backtests", json={"strategy_id": identifier, "initial_capital": "10000", "limit": 80})
    assert run.status_code == 201, run.text
    assert client.post(f"/api/v1/strategies/{identifier}/start").status_code == 200
    return client, factory, identifier
