"""Endpoint integration against isolated SQLite and synthetic candles."""
from decimal import Decimal

from sqlalchemy import select


def test_authentication_csrf_origin_and_revocation(backend):
    client, _ = backend
    assert client.get("/api/v1/dashboard").status_code == 401
    bad_origin = client.post("/api/v1/auth/login", headers={"Origin": "https://attacker.example"}, json={"username": "operator", "password": "test-only-password"})
    assert bad_origin.status_code == 403
    logged_in = client.post("/api/v1/auth/login", json={"username": "operator", "password": "test-only-password"})
    assert logged_in.status_code == 200
    assert "HttpOnly" in logged_in.headers["set-cookie"]
    assert "SameSite=strict" in logged_in.headers["set-cookie"]
    assert client.post("/api/v1/paper/account", json={"initial_balance": "1000"}).status_code == 403
    token = logged_in.json()["csrf_token"]
    assert client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": token}).status_code == 200
    assert client.get("/api/v1/auth/me").status_code == 401


def test_validation_never_echoes_password(backend):
    client, _ = backend
    secret = "synthetic-secret-" * 30
    response = client.post("/api/v1/auth/login", json={"username": "operator", "password": secret})
    assert response.status_code == 422
    assert secret not in response.text
    assert "input" not in response.text


def test_body_limit_rejects_before_json_allocation(backend):
    client, _ = backend
    response = client.post("/api/v1/auth/login", content=b"x"*(1024*1024+1), headers={"Content-Type": "application/json"})
    assert response.status_code == 413


def test_viewer_cannot_write(backend):
    client, _ = backend
    response = client.post("/api/v1/auth/login", json={"username": "viewer", "password": "test-only-password"})
    assert response.status_code == 200
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    assert client.get("/api/v1/strategies").status_code == 200
    assert client.post("/api/v1/paper/account", json={"initial_balance": "10000"}).status_code == 403


def test_login_rate_limit_is_per_username_despite_forwarded_ip(backend):
    client, _ = backend
    for index in range(10):
        response = client.post("/api/v1/auth/login", headers={"X-Forwarded-For": f"198.51.100.{index}"}, json={"username": "operator", "password": "wrong"})
        assert response.status_code == 401
    assert client.post("/api/v1/auth/login", json={"username": "operator", "password": "test-only-password"}).status_code == 429


def test_no_seeded_account_or_fake_pnl(authenticated):
    client, _ = authenticated
    assert client.get("/api/v1/paper/account").json() is None
    dashboard = client.get("/api/v1/dashboard").json()
    assert dashboard["account"] is None
    assert dashboard["equity_curve"] == []
    assert dashboard["mode"] == "PAPER"
    assert dashboard["live_enabled"] is False
    assert client.get("/api/v1/live-readiness").json()["status"] == "BLOCKED"


def test_planning_inr_is_never_usd_balance(authenticated):
    client, _ = authenticated
    account = client.post("/api/v1/paper/account", json={"initial_balance": "1000", "settlement_asset": "USD", "planning_capital_inr": "50000"})
    assert account.status_code == 201
    data = account.json()
    assert Decimal(data["cash"]) == 1000
    assert Decimal(data["planning_capital_inr"]) == 50000
    assert data["usd_inr_rate"] is None
    assert data["reporting_currency"] == "USD"
    assert client.post("/api/v1/paper/account", json={"initial_balance": "2000"}).status_code == 409


def test_promotion_requires_exact_backtested_version(authenticated):
    client, _ = authenticated
    client.post("/api/v1/paper/account", json={"initial_balance": "10000"})
    strategy = client.post("/api/v1/strategies", json={"name": "Versioned", "config": {}}).json()
    sid = strategy["id"]
    assert client.post(f"/api/v1/strategies/{sid}/start").status_code == 409
    assert client.post(f"/api/v1/strategies/{sid}/validate").status_code == 200
    assert client.post(f"/api/v1/strategies/{sid}/start").status_code == 409
    update = client.put(f"/api/v1/strategies/{sid}", json={"config": {"fast_period": 5, "slow_period": 12}})
    assert update.status_code == 200
    assert update.json()["current_version"] == 2
    assert update.json()["approved_version"] is None
    assert len(client.get(f"/api/v1/strategies/{sid}/versions").json()) == 2


def test_persisted_full_paper_workflow_and_restart(paper_fixture):
    from tradebot.models import Fill, Position
    from tradebot.paper import reconcile
    from tradebot.worker import process_paper
    client, factory, sid = paper_fixture
    with factory() as db:
        process_paper(db, executor_healthy=True)
        db.commit()
    positions = client.get("/api/v1/positions").json()
    assert len(positions) == 1
    position = positions[0]
    assert position["status"] == "OPEN"
    assert position["protection_verified"] is True
    orders = client.get("/api/v1/orders").json()
    assert len(orders) == 3
    assert {order["order_type"] for order in orders} == {"MARKET", "STOP_MARKET", "TAKE_PROFIT"}
    assert all(order["mode"] == "PAPER" for order in orders)
    # New DB session emulates worker process recovery; replay same bar must not
    # produce another entry or fill, regardless mutable in-memory state.
    from tradebot.models import PaperAccount
    with factory() as db:
        assert reconcile(db, db.get(PaperAccount, 1)) == []
        process_paper(db, executor_healthy=True)
        db.commit()
        assert len(list(db.scalars(select(Position)))) == 1
        assert len(list(db.scalars(select(Fill)))) == 1
    assert client.post(f"/api/v1/strategies/{sid}/stop").status_code == 200
    assert client.get("/api/v1/positions").json()[0]["status"] == "OPEN"
    closed = client.post(f"/api/v1/positions/{position['id']}/close")
    assert closed.status_code == 200, closed.text
    assert closed.json()["status"] == "CLOSED"
    assert len(client.get("/api/v1/fills").json()) == 2
    assert any(item["action"] == "PAPER_POSITION_CLOSE" for item in client.get("/api/v1/journal").json())
    assert client.get("/api/v1/notifications").json()
    with factory() as db:
        assert reconcile(db, db.get(PaperAccount, 1)) == []


def test_running_strategy_validation_and_edit_blocked(paper_fixture):
    client, _, sid = paper_fixture
    assert client.post(f"/api/v1/strategies/{sid}/validate").status_code == 409
    assert client.put(f"/api/v1/strategies/{sid}", json={"config": {}}).status_code == 409


def test_live_mode_configuration_rejected(monkeypatch):
    import pytest
    from pydantic import ValidationError
    from tradebot.config import Settings
    with pytest.raises(ValidationError):
        Settings(trading_mode="LIVE")
    with pytest.raises(ValidationError):
        Settings(live_execution_enabled=True)
