"""Explicit, securely gated DEMO acceptance runner; never runs at startup/CI.

``--read-only`` checks actual private connectivity with configured demo secrets.
``--order-flow --confirm-demo-orders`` exercises the application coordinator,
requires an already enabled demo account, and prompts locally for operator MFA.
It cannot enable an account or bypass central risk. Secret values are never saved.
"""
import argparse
import asyncio
import getpass
import json
import os
import time
import uuid
from decimal import Decimal, ROUND_FLOOR
from pathlib import Path
from urllib.parse import urlparse

import httpx

from tradebot import demo
from tradebot.db import utcnow


async def readonly() -> list[dict]:
    checks = []
    if not demo.credential_configured():
        return [{"name": "private_authentication", "classification": "blocked", "status": "not_run", "reason": "Demo credentials are not configured securely"}]
    try:
        async with demo.client() as adapter:
            for name, operation in (("private_balances", adapter.balances), ("private_positions", adapter.positions),
                                    ("private_active_orders", adapter.active_orders), ("private_fills", adapter.fills),
                                    ("private_wallet_transactions", adapter.wallet_transactions),
                                    ("demo_product_discovery", adapter.products)):
                try:
                    result = await operation()
                    checks.append({"name": name, "classification": "actual_demo", "status": "passed", "record_count": len(result) if isinstance(result, list) else None})
                except Exception:
                    checks.append({"name": name, "classification": "actual_demo", "status": "failed", "reason": "Exchange response/authentication was not accepted; inspect secure local diagnostics"})
            for attempt in range(2):
                try:
                    stream = adapter.private_messages(("orders", "positions", "v2/user_trades", "margins"), ("all",), emit_auth=True)
                    message = await asyncio.wait_for(anext(stream), timeout=20)
                    await stream.aclose()
                    passed = message.get("type") == "private_authenticated" and message.get("environment") == "DEMO" and message.get("requires_recovery") is True
                    checks.append({"name": "private_ws_authentication" if attempt == 0 else "private_ws_reauthentication", "classification": "actual_demo", "status": "passed" if passed else "failed", "reconciliation_required": True})
                except Exception:
                    checks.append({"name": "private_ws_authentication" if attempt == 0 else "private_ws_reauthentication", "classification": "actual_demo", "status": "failed", "reason": "Authenticated India demo WebSocket was not acknowledged"})
    except Exception:
        checks.append({"name": "private_client_configuration", "classification": "blocked", "status": "not_run", "reason": "Secure local demo client configuration is unavailable"})
    return checks


async def orderflow(base: str, symbol: str) -> list[dict]:
    if not demo.execution_authorized() or not demo.credential_configured():
        return [{"name": "actual_order_lifecycle", "classification": "blocked", "status": "not_run", "reason": "Configured credentials and explicit local DEMO_EXECUTION_AUTHORIZED are required"}]
    parsed = urlparse(base)
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
        raise ValueError("API base must be a plain origin without credentials or paths")
    configured_origin = os.getenv("APP_ORIGIN", "http://127.0.0.1:3000").rstrip("/")
    if base.rstrip("/") != configured_origin and not (parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost") and parsed.port in (3000, 8000)):
        raise ValueError("API origin must match APP_ORIGIN or a loopback application port")
    username = input("Operator username: ").strip()
    password = getpass.getpass("Operator password (never saved): ")
    login_code = getpass.getpass("Current login TOTP code (never saved): ")
    checks, intent_ids = [], []
    prefix = "/api/v1"
    async with httpx.AsyncClient(base_url=base, timeout=20, headers={"Origin": configured_origin}, follow_redirects=False) as api:
        response = await api.post(prefix+"/auth/login", json={"username": username, "password": password, "code": login_code})
        if response.status_code != 200:
            return [{"name": "application_operator_authentication", "classification": "blocked", "status": "not_run", "reason": "Operator authentication/MFA was not accepted"}]
        api.headers["X-CSRF-Token"] = response.json()["csrf_token"]
        second_code = getpass.getpass("Fresh TOTP for sensitive reauthentication (wait for next code if needed): ")
        response = await api.post(prefix+"/security/reauth", json={"password": password, "code": second_code})
        password = login_code = second_code = ""
        if response.status_code != 200:
            await api.post(prefix+"/auth/logout")
            return [{"name": "recent_operator_mfa", "classification": "blocked", "status": "not_run", "reason": "Recent second-factor authentication was not accepted"}]

        async def get(path):
            result = await api.get(prefix+path)
            result.raise_for_status()
            return result.json()

        async def post(path, value=None):
            result = await api.post(prefix+path, json=value or {})
            if result.status_code >= 400:
                raise ValueError("Application rejected the explicitly reviewed DEMO operation")
            return result.json()

        async def wait_order(identity, states):
            deadline = time.monotonic()+90
            while time.monotonic() < deadline:
                found = next((order for order in await get("/demo/orders") if order["id"] == identity), None)
                if found and found["status"] in states:
                    return found
                await asyncio.sleep(2)
            raise ValueError("Demo order did not reach an authoritative expected state within the acceptance timeout")

        try:
            status = await get("/demo/status")
            account = status.get("account") or {}
            if not account.get("entries_enabled") or account.get("reconciliation_required") or status.get("worker", {}).get("status") != "RUNNING" or account.get("stream_status") != "CONNECTED":
                raise ValueError("A previously connected, MFA-enabled, reconciled account and healthy private worker are required")
            if await get("/demo/positions") or any(row["status"] == "RUNNING" for row in await get("/demo/strategies")):
                raise ValueError("Acceptance requires no open positions or running demo strategies")
            if any(order["status"] in demo.ACTIVE for order in await get("/demo/orders")):
                raise ValueError("Acceptance requires no existing active or UNKNOWN demo intents")
            product = next((p for p in await get("/demo/products") if p["symbol"] == symbol), None)
            if not product or not product.get("verified_contract"):
                raise ValueError("Review the actual demo contract minimum/margin evidence before acceptance")
            contract = product["verified_contract"]
            quantity = int(Decimal(contract["min_quantity"]))
            tick = Decimal(contract["tick_size"])
            async with demo.client() as adapter:
                ticker = await adapter.ticker(symbol)
            reference = Decimal(ticker["quotes"]["best_ask"])
            def rounded(value):
                return str((value/tick).to_integral_value(rounding=ROUND_FLOOR)*tick)
            common = {"symbol": symbol, "side": "BUY", "quantity": quantity, "stop_price": rounded(reference*Decimal(".99")), "target_price": rounded(reference*Decimal("1.02"))}
            entry = await post("/demo/orders", {**common, "idempotency_key": "acceptance:market:"+uuid.uuid4().hex, "order_type": "MARKET"})
            intent_ids.append(entry["id"])
            terminal = await wait_order(entry["id"], {"FILLED", "PARTIALLY_FILLED", "REJECTED", "UNKNOWN"})
            if terminal["status"] not in ("FILLED", "PARTIALLY_FILLED"):
                raise ValueError("Minimum market order was rejected or uncertain; no retry is authorized")
            checks.append({"name": "minimum_market_order_and_client_identity", "classification": "actual_demo", "status": "passed", "intent_id": entry["id"], "exchange_order_id": terminal["exchange_order_id"], "filled_quantity": terminal["filled_quantity"]})
            if terminal["status"] == "PARTIALLY_FILLED":
                await post("/demo/orders/"+entry["id"]+"/cancel")
                await wait_order(entry["id"], {"CANCELED", "FILLED", "UNKNOWN"})
            await post("/demo/reconcile")
            position = next((p for p in await get("/demo/positions") if p["product_id"] == product["id"]), None)
            if not position or not position["managed"] or not position["protection_verified"]:
                raise ValueError("Actual entry protection or managed position reconciliation failed")
            checks.append({"name": "actual_stop_target_presence_and_position_reconciliation", "classification": "actual_demo", "status": "passed"})
            closing = await post(f"/demo/positions/{product['id']}/close", {"confirm": True})
            intent_ids.append(closing["id"])
            closed = await wait_order(closing["id"], {"FILLED", "REJECTED", "UNKNOWN"})
            if closed["status"] != "FILLED":
                raise ValueError("Managed close is not confirmed; operator review required")
            checks.append({"name": "managed_reduce_only_close", "classification": "actual_demo", "status": "passed", "intent_id": closing["id"]})
            deadline = time.monotonic()+60
            while time.monotonic() < deadline:
                status = await get("/demo/status")
                if not (status.get("account") or {}).get("reconciliation_required") and not await get("/demo/positions"):
                    break
                await asyncio.sleep(2)
            else:
                raise ValueError("Closed position/protection cleanup did not reconcile")
            resting = await post("/demo/orders", {**common, "idempotency_key": "acceptance:limit:"+uuid.uuid4().hex,
                                                 "order_type": "LIMIT", "limit_price": rounded(reference*Decimal(".995"))})
            intent_ids.append(resting["id"])
            limit = await wait_order(resting["id"], {"OPEN", "PARTIALLY_FILLED", "FILLED", "REJECTED", "UNKNOWN"})
            if limit["status"] not in ("OPEN", "PARTIALLY_FILLED", "FILLED"):
                raise ValueError("Limit order was rejected or uncertain")
            await post("/demo/orders/"+resting["id"]+"/cancel")
            canceled = await wait_order(resting["id"], {"CANCELED", "FILLED", "UNKNOWN"})
            if canceled["status"] == "UNKNOWN":
                raise ValueError("Limit cancellation outcome is unknown; original-ID recovery required")
            checks.append({"name": "limit_order_and_cancel_race", "classification": "actual_demo", "status": "passed", "intent_id": resting["id"], "terminal_state": canceled["status"]})
            checks.append({"name": "actual_protective_stop_target_trigger_and_trailing", "classification": "unrun", "status": "not_run", "reason": "Acceptance verifies presence, not forced market triggers or unverified trailing lifecycle"})
        except Exception:
            checks.append({"name": "actual_order_lifecycle", "classification": "actual_demo", "status": "failed", "reason": "Operation rejected, uncertain or unreconciled; inspect recorded intents and DEMO account issues"})
        finally:
            # This explicit acceptance task owns only its own intents. Never
            # mass-cancel account orders or close external positions.
            for identity in intent_ids:
                try:
                    await post("/demo/orders/"+identity+"/cancel")
                except Exception:
                    pass
            try:
                positions = await get("/demo/positions") if intent_ids else []
                entries = [order for order in await get("/demo/orders") if not order["reduce_only"] and Decimal(order["filled_quantity"]) > 0] if intent_ids else []
                for position in positions:
                    latest = next((order for order in entries if order["product_id"] == position["product_id"]), None)
                    if position["symbol"] == symbol and position["managed"] and latest and latest["id"] in intent_ids:
                        closing = await post(f"/demo/positions/{position['product_id']}/close", {"confirm": True})
                        await wait_order(closing["id"], {"FILLED", "REJECTED", "UNKNOWN"})
                if intent_ids:
                    await post("/demo/disable")
            except Exception:
                checks.append({"name": "acceptance_cleanup", "classification": "actual_demo", "status": "failed", "reason": "Managed exposure/unknown intents may remain; review DEMO urgently. Protection was not removed."})
            await api.post(prefix+"/auth/logout")
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--read-only", action="store_true")
    mode.add_argument("--order-flow", action="store_true")
    parser.add_argument("--confirm-demo-orders", action="store_true", help="Explicitly authorize this minimum-size DEMO acceptance flow")
    parser.add_argument("--api-base", default=os.getenv("APP_ORIGIN", "http://127.0.0.1:3000"))
    parser.add_argument("--symbol", default="BTCUSD")
    parser.add_argument("--output", type=Path, default=Path(".runtime/demo-acceptance.json"))
    args = parser.parse_args()
    if args.order_flow and not args.confirm_demo_orders:
        parser.error("Order flow requires --confirm-demo-orders; no requests have been sent")
    checks = asyncio.run(readonly() if args.read_only else orderflow(args.api_base, args.symbol))
    result = {"checked_at_utc": utcnow().isoformat(), "environment": "DEMO", "production_private_orders": "structurally unavailable",
              "mode": "read_only" if args.read_only else "explicit_minimum_order_flow", "checks": checks,
              "certifies_production_readiness": False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2)+"\n")
    args.output.chmod(0o600)
    print(json.dumps(result, indent=2))
    return 0 if checks and all(item["status"] == "passed" for item in checks) else 2


if __name__ == "__main__":
    raise SystemExit(main())
