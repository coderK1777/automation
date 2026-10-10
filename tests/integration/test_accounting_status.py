"""Financial display and executor status must reflect persisted evidence."""
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select


def test_dashboard_win_rate_includes_paid_costs_and_financing(paper_fixture):
    from tradebot.db import utcnow
    from tradebot.expansion_models import PaperMovement
    from tradebot.models import Position
    from tradebot.paper import close_position
    from tradebot.repository import position_net_pnl
    from tradebot.worker import process_paper
    client, factory, _ = paper_fixture
    with factory() as db:
        process_paper(db, executor_healthy=True)
        position = db.scalar(select(Position).where(Position.status == "OPEN"))
        assert position is not None
        close_position(db, position, position.entry_price * Decimal("1.0006"), "COST_REGRESSION")
        assert position.realized_pnl > 0
        assert position_net_pnl(db, position) < 0
        identifier = position.id
        db.commit()
    assert Decimal(client.get("/api/v1/dashboard").json()["metrics"]["win_rate"]) == 0
    with factory() as db:
        position = db.get(Position, identifier)
        net = position_net_pnl(db, position)
        db.add(PaperMovement(id="synthetic-funding", account_id=1, position_id=identifier, kind="FUNDING", amount=-net + Decimal(2),
                             details={"source": "synthetic-attributed-funding"}, created_at=utcnow()))
        db.add(PaperMovement(id="synthetic-liquidation-cost", account_id=1, position_id=identifier, kind="LIQUIDATION_FEE", amount=Decimal(-3),
                             details={"source": "synthetic-declared-cost"}, created_at=utcnow()))
        db.commit()
    # Financing credit cannot conceal an additional recorded liquidation cost.
    assert Decimal(client.get("/api/v1/dashboard").json()["metrics"]["win_rate"]) == 0


@pytest.mark.parametrize("credentials,age,expected", [(False, 0, False), (True, 120, False), (True, 0, True)])
def test_integrations_requires_current_credentials_and_worker(authenticated, monkeypatch, credentials, age, expected):
    from tradebot import demo
    from tradebot.db import utcnow
    from tradebot.demo_models import DemoAccount
    client, factory = authenticated
    monkeypatch.setattr(demo, "credential_configured", lambda: credentials)
    monkeypatch.setattr(demo, "execution_authorized", lambda: True)
    with factory() as db:
        db.add(DemoAccount(id=1, credential_fingerprint="offline-status-fixture", connected=True,
                           entries_enabled=True, reconciliation_required=False, worker_status="RUNNING",
                           stream_status="CONNECTED", worker_updated_at=utcnow()-timedelta(seconds=age)))
        db.commit()
    observed = client.get("/api/v1/integrations").json()["demo"]
    assert observed["execution_enabled"] is expected
    if not credentials:
        assert observed["status"] == "credentials_required"


def test_correlated_cap_persists_and_records_rejected_signal(paper_fixture):
    from tradebot.models import Order, Position
    from tradebot.worker import process_paper
    client, factory, identifier = paper_fixture
    assert client.put("/api/v1/risk", json={"max_correlated_exposure_multiple": "0.0001"}).status_code == 422
    payload = {"max_correlated_exposure_multiple": "0.0001", "correlation_groups": [["BTCUSD", "ETHUSD"]]}
    saved = client.put("/api/v1/risk", json=payload)
    assert saved.status_code == 200
    assert saved.json()["correlation_groups"] == payload["correlation_groups"]
    with factory() as db:
        process_paper(db, executor_healthy=True)
        db.commit()
        assert list(db.scalars(select(Position))) == []
        assert list(db.scalars(select(Order))) == []
    signal = client.get("/api/v1/signals", params={"strategy_id": identifier}).json()[0]
    decision = client.get("/api/v1/risk/decisions", params={"strategy_id": identifier}).json()[0]
    assert signal["status"] == "REJECTED" and decision["approved"] is False
    assert "CORRELATED_EXPOSURE_LIMIT" in decision["decision"]["reasons"][0]
    assert Decimal(decision["decision"]["requested_quantity"]) > 0
    assert decision["context"]["account_reconciled"] is True


def test_volatility_sizing_does_not_read_future_candles(paper_fixture):
    from tradebot.models import Candle
    from tradebot.paper import sizing_atr
    from tradebot_core import StrategyConfig
    _, factory, _ = paper_fixture
    config = StrategyConfig(name="Synthetic volatility test", symbol="BTCUSD", sizing={"mode": "VOLATILITY", "volatility_period": 14})
    with factory() as db:
        observed = db.scalar(select(Candle).order_by(Candle.timestamp.desc()))
        expected = sizing_atr(db, config, observed)
        db.add(Candle(symbol="BTCUSD", timeframe_seconds=60, timestamp=observed.timestamp+timedelta(minutes=1),
                      open=100000, high=1000000, low=1, close=200000, volume=100))
        db.flush()
        assert sizing_atr(db, config, observed) == expected
