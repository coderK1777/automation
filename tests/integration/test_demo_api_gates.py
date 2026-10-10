"""Application authorization tests; private execution uses no real Delta calls."""
import pytest


def test_demo_status_never_exposes_credential_material(authenticated, monkeypatch):
    client, _ = authenticated
    monkeypatch.setenv("DELTA_DEMO_API_KEY", "offline-api-sensitive-key")
    monkeypatch.setenv("DELTA_DEMO_API_SECRET", "offline-api-sensitive-secret")
    monkeypatch.setenv("DEMO_EXECUTION_AUTHORIZED", "false")
    response = client.get("/api/v1/demo/status")
    assert response.status_code == 200
    assert response.json()["credential_configured"] is True
    assert response.json()["local_execution_authorized"] is False
    assert response.json()["production_orders_available"] is False
    assert "offline-api-sensitive" not in response.text


@pytest.mark.parametrize("path,payload", [("/connect", {}), ("/enable", {"confirm": True}),
                                            ("/strategies/1/start", {"confirm": True}),
                                            ("/orders", {"idempotency_key": "offline-entry-api", "symbol": "BTCUSD", "side": "BUY", "quantity": 1, "stop_price": "80000", "target_price": "85000"}),
                                            ("/products/84/verify", {"min_quantity": "1", "quantity_step": "1", "initial_margin_rate": ".5", "max_quantity": "10", "evidence": "OFFLINE official evidence fixture"})])
def test_demo_sensitive_actions_require_recent_password_and_mfa(authenticated, path, payload):
    client, _ = authenticated
    response = client.post("/api/v1/demo"+path, json=payload)
    assert response.status_code == 403
    assert "Recent password" in response.json()["detail"]


def test_demo_entry_csrf_is_enforced_before_state_access(authenticated):
    client, _ = authenticated
    client.headers.pop("X-CSRF-Token")
    response = client.post("/api/v1/demo/disable")
    assert response.status_code == 403 and "CSRF" in response.json()["detail"]


def test_public_demo_discovery_works_without_private_credentials(authenticated, monkeypatch):
    import tradebot_delta
    client, _ = authenticated
    targets = []
    class PublicFixture:
        def __init__(self, environment):
            targets.append(environment)
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_):
            return None
        async def products(self):
            return [{"id": 99984, "symbol": "OFFLINEBTCUSD", "metadata_verified": False, "linear_economics_verified": False}]
    monkeypatch.setattr(tradebot_delta, "DeltaClient", PublicFixture)
    response = client.post("/api/v1/demo/products/refresh")
    assert response.status_code == 200, response.text
    assert targets == ["demo"]
    assert response.json()[0]["verified_contract"] is None
    assert client.get("/api/v1/demo/status").json()["account"] is None


def test_production_order_submission_route_is_absent(authenticated):
    client, _ = authenticated
    assert client.post("/api/v1/live/orders", json={"symbol": "BTCUSD", "quantity": 1}).status_code == 404
