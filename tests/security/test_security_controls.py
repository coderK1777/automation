import base64
from datetime import timedelta

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from tradebot.security_admin import router, sensitive_operator, totp_code


@pytest.fixture
def controls(authenticated, tmp_path, monkeypatch):
    """Use the existing real auth endpoints; locally mount the standalone router."""
    client, factory = authenticated
    monkeypatch.setenv("TRADEBOT_VAULT_KEY_FILE", str(tmp_path / "vault.key"))
    monkeypatch.setenv("TRADEBOT_VAULT_FILE", str(tmp_path / "credentials.vault"))
    from tradebot.db import Base, get_db
    from tradebot.vault import initialize_vault
    initialize_vault()
    with factory() as db:
        Base.metadata.create_all(db.bind)
    api = FastAPI()
    api.include_router(router)

    @api.post("/sensitive-probe")
    def probe(user=Depends(sensitive_operator)):
        return {"username": user.username}

    def db_dependency():
        with factory() as db:
            try:
                yield db
            except Exception:
                db.rollback()
                raise

    api.dependency_overrides[get_db] = db_dependency
    with TestClient(api) as security_client:
        security_client.cookies.update(client.cookies)
        security_client.headers.update({"X-CSRF-Token": client.headers["X-CSRF-Token"]})
        yield security_client, client, factory


def enable_mfa(client):
    result = client.post("/api/v1/security/reauth", json={"password": "test-only-password"})
    assert result.status_code == 200, result.text
    assert result.json()["mfa_verified"] is False
    response = client.post("/api/v1/security/mfa/enroll")
    assert response.status_code == 200, response.text
    secret = response.json()["secret"]
    confirmed = client.post("/api/v1/security/mfa/confirm", json={"code": totp_code(secret)})
    assert confirmed.status_code == 200, confirmed.text
    return secret, confirmed.json()["recovery_codes"]


@pytest.mark.parametrize("timestamp,expected", [(59, "94287082"), (1111111109, "07081804"), (1111111111, "14050471"), (1234567890, "89005924"), (2000000000, "69279037"), (20000000000, "65353130")])
def test_totp_rfc6238_vectors(timestamp, expected):
    secret = base64.b32encode(b"12345678901234567890").decode()
    assert totp_code(secret, timestamp=timestamp, digits=8) == expected


def test_mfa_recent_auth_and_expiry_fail_closed(controls):
    client, _, factory = controls
    assert client.post("/sensitive-probe").status_code == 403
    assert client.post("/api/v1/security/reauth", json={"password": "test-only-password"}).status_code == 200
    assert client.post("/sensitive-probe").status_code == 403
    _, codes = enable_mfa(client)
    assert len(codes) == 8
    assert client.post("/sensitive-probe").status_code == 200
    status = client.get("/api/v1/security/status").json()
    assert status["mfa_enabled"] and status["reauth"]["mfa_verified"]
    from tradebot.db import utcnow
    from tradebot.security_admin_models import Reauthentication
    with factory() as db:
        recent = db.query(Reauthentication).one()
        recent.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    assert client.post("/sensitive-probe").status_code == 403


def test_replayed_totp_and_recovery_codes_are_rejected(controls):
    client, _, factory = controls
    secret, codes = enable_mfa(client)
    response = client.post("/api/v1/security/reauth", json={"password": "test-only-password", "code": totp_code(secret)})
    assert response.status_code == 401
    accepted = client.post("/api/v1/security/reauth", json={"password": "test-only-password", "recovery_code": codes[0]})
    assert accepted.status_code == 200
    assert client.post("/api/v1/security/reauth", json={"password": "test-only-password", "recovery_code": codes[0]}).status_code == 401
    from tradebot.security_admin_models import MFAState
    with factory() as db:
        state = db.query(MFAState).one()
        assert len(state.recovery_hashes) == 7
        assert secret not in state.secret_ciphertext
        assert all(code not in str(state.recovery_hashes) for code in codes)


def test_csrf_and_rate_limit_are_enforced(controls):
    client, _, _ = controls
    saved = client.headers.pop("X-CSRF-Token")
    assert client.post("/api/v1/security/reauth", json={"password": "test-only-password"}).status_code == 403
    client.headers["X-CSRF-Token"] = saved
    for _ in range(10):
        assert client.post("/api/v1/security/reauth", json={"password": "invalid-fixture-password"}).status_code == 401
    assert client.post("/api/v1/security/reauth", json={"password": "test-only-password"}).status_code == 429


def test_last_operator_user_controls_and_session_revocation(controls):
    client, auth_client, _ = controls
    enable_mfa(client)
    listed = client.get("/api/v1/security/users").json()
    operator = next(user for user in listed if user["username"] == "operator")
    assert client.patch(f'/api/v1/security/users/{operator["id"]}', json={"active": False}).status_code == 409
    assert client.patch(f'/api/v1/security/users/{operator["id"]}', json={"role": "viewer"}).status_code == 409
    created = client.post("/api/v1/security/users", json={"username": "new-viewer", "password": "long-fixture-password", "role": "viewer"})
    assert created.status_code == 201, created.text
    assert "password" not in created.text
    assert client.post("/api/v1/security/users", json={"username": "NEW-VIEWER", "password": "long-fixture-password"}).status_code == 409
    sessions = client.get("/api/v1/security/sessions").json()
    assert sessions[0]["current"]
    assert len(sessions[0]["id"]) == 24
    assert client.delete("/api/v1/security/sessions/unknown").status_code == 404
    assert client.delete('/api/v1/security/sessions/' + sessions[0]["id"]).status_code == 200
    assert auth_client.get("/api/v1/auth/me").status_code == 401


def test_versioned_readiness_evidence_never_enables_live(controls):
    client, auth_client, _ = controls
    enable_mfa(client)
    strategy = auth_client.post("/api/v1/strategies", json={"name": "Readiness test", "config": {"kind": "EMA_CROSS", "symbol": "BTCUSD", "timeframe_seconds": 60, "fast_period": 2, "slow_period": 3}})
    assert strategy.status_code == 201, strategy.text
    identifier = strategy.json()["id"]
    evidence = {"version": 1, "category": "SECURITY_REVIEW", "outcome": "PASS", "evidence_uri": "docs/security.md", "evidence_sha256": "a" * 64}
    result = client.post(f"/api/v1/security/readiness/{identifier}/evidence", json=evidence)
    assert result.status_code == 201
    assert result.json()["gates"]["security_review"]
    assert result.json()["gates"]["demo_approved"] is False
    assert result.json()["live_enabled"] is False
    assert result.json()["activation_available"] is False
    assert result.json()["review_complete"] is False
    evidence["version"] = 2
    assert client.post(f"/api/v1/security/readiness/{identifier}/evidence", json=evidence).status_code == 409


def test_factor_login_requires_recovery_and_consumes_it(controls):
    client, auth_client, factory = controls
    _, codes = enable_mfa(client)
    from tradebot.models import User
    from tradebot.security_admin import verify_login_mfa
    with factory() as db:
        user = db.query(User).filter_by(username="operator").one()
        assert verify_login_mfa(db, user) is False
        assert verify_login_mfa(db, user, recovery_code=codes[0]) is True
        db.commit()
        assert verify_login_mfa(db, user, recovery_code=codes[0]) is False
    # Existing session remains usable; MFA is checked on future logins.
    assert auth_client.get("/api/v1/auth/me").status_code == 200
    assert auth_client.post("/api/v1/auth/login", json={"username": "operator", "password": "test-only-password"}).status_code == 401
    accepted = auth_client.post("/api/v1/auth/login", json={"username": "operator", "password": "test-only-password", "recovery_code": codes[1]})
    assert accepted.status_code == 200, accepted.text
    assert auth_client.post("/api/v1/auth/login", json={"username": "operator", "password": "test-only-password", "recovery_code": codes[1]}).status_code == 401


def test_password_rotation_revokes_other_sessions_and_retains_mfa(controls):
    client, auth_client, _ = controls
    second = auth_client.post("/api/v1/auth/login", json={"username": "operator", "password": "test-only-password"})
    assert second.status_code == 200
    _, codes = enable_mfa(client)
    rotated = client.post("/api/v1/security/password", json={"current_password": "test-only-password", "new_password": "rotated-long-fixture-password", "recovery_code": codes[0]})
    assert rotated.status_code == 200, rotated.text
    assert rotated.json()["sessions_revoked"] == 1
    assert auth_client.get("/api/v1/auth/me").status_code == 401
    current = client.get("/api/v1/security/status").json()
    assert current["mfa_enabled"] is True
    assert current["reauth"] is None
    assert auth_client.post("/api/v1/auth/login", json={"username": "operator", "password": "test-only-password", "recovery_code": codes[1]}).status_code == 401
    assert auth_client.post("/api/v1/auth/login", json={"username": "operator", "password": "rotated-long-fixture-password", "recovery_code": codes[1]}).status_code == 200
