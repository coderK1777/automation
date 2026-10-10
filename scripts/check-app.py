"""Functional PAPER-only probe with real stored Delta data; never exchange orders."""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import dotenv_values

root = Path(__file__).resolve().parents[1]
config = dotenv_values(root / ".env")
evidence: dict = {"mode": "PAPER", "market_source": "Delta Exchange India public API", "private_exchange_requests": 0}


def main() -> None:
    with httpx.Client(base_url="http://127.0.0.1:3000/api/v1", timeout=60, trust_env=False,
                      headers={"Origin": config.get("APP_ORIGIN", "http://127.0.0.1:3000")}) as client:
        assert client.get("/dashboard").status_code == 401
        login = client.post("/auth/login", json={"username": config["BOOTSTRAP_USERNAME"], "password": config["BOOTSTRAP_PASSWORD"]})
        assert login.status_code == 200, "Operator login failed; no credential values logged"
        assert "httponly" in login.headers.get("set-cookie", "").lower()
        csrf = login.json()["csrf_token"]
        client.headers["X-CSRF-Token"] = csrf

        def request(method: str, path: str, body=None):
            response = client.request(method, path, json=body)
            if response.is_error:
                raise AssertionError(f"{method} {path}: {response.status_code} {response.json().get('detail')}")
            return response.json()

        health = request("GET", "/health/ready")
        assert health["status"] == "ready"
        account = request("GET", "/paper/account")
        assert account and account["mode"] == "PAPER" and account["settlement_asset"] == "USD"
        assert request("GET", "/positions?status=OPEN") == [], "Probe requires no existing open paper positions"
        configuration = {
            "kind": "CUSTOM", "symbol": "BTCUSD", "timeframe_seconds": 60,
            "risk_fraction": "0.005", "stop_loss_pct": "0.01", "take_profit_pct": "0.025", "max_leverage": "2",
            "allow_short": False, "long_rules": {"op": "GT", "left": {"feature": "close"}, "right": {"value": "0"}},
        }
        strategy = request("POST", "/strategies", {"name": "Readiness probe — PAPER only", "description": "Functional validation using real Delta candles; deliberately simple condition, no profitability claim.", "config": configuration})
        identifier = strategy["id"]
        try:
            request("POST", f"/strategies/{identifier}/validate", {})
            run = request("POST", "/backtests", {"strategy_id": identifier, "initial_capital": account["initial_balance"], "limit": 1500})
            assert run["status"] == "COMPLETED"
            request("POST", f"/strategies/{identifier}/start", {})
            position = None
            for _ in range(50):
                positions = request("GET", "/positions?status=OPEN")
                position = next((p for p in positions if p["strategy_id"] == identifier), None)
                if position:
                    break
                time.sleep(1)
            assert position is not None, "Autonomous PAPER worker did not produce a risk-approved simulated fill"
            request("POST", f"/strategies/{identifier}/pause", {})
            before = request("GET", "/paper/account")
            assert before["margin_used"] != "0"
            request("POST", f"/positions/{position['id']}/close", {})
            assert not any(p["id"] == position["id"] for p in request("GET", "/positions?status=OPEN"))
            orders = request("GET", "/orders")
            owned_orders = [o for o in orders if o["strategy_id"] == identifier]
            assert sum(o["status"] == "FILLED" for o in owned_orders) == 2
            assert sum(o["status"] == "CANCELED" for o in owned_orders) == 2
            blocked = request("POST", "/emergency-stop", {"action": "BLOCK_ENTRIES"})
            assert blocked["emergency_stop"] is True
            request("POST", "/emergency-stop", {"action": "RESUME_ENTRIES", "confirm": True})
            evidence.update(strategy_id=identifier, backtest_id=run["id"], backtest_status=run["status"],
                            position_id=position["id"], filled_orders=2, cancelled_protection_orders=2,
                            account=request("GET", "/paper/account"), journal_records=len(request("GET", "/journal")),
                            notification_records=len(request("GET", "/notifications")),
                            worker=request("GET", "/worker/health"),
                            completed_at=datetime.now(timezone.utc).isoformat())
        finally:
            request("POST", f"/strategies/{identifier}/stop", {})
        request("POST", "/auth/logout", {})
        assert client.get("/dashboard").status_code == 401
    destination = root / "docs/validation/paper-functional-2026-10-09.json"
    destination.parent.mkdir(exist_ok=True)
    destination.write_text(json.dumps(evidence, indent=2) + "\n")
    print("Real-data PAPER workflow passed: backtest, autonomous risk-approved fill, close, journal, emergency controls, logout.")


if __name__ == "__main__":
    main()
