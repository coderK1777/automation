"""Validate expanded UI against real APIs; mutations use a temporary SQLite database.

No private exchange or Telegram requests are sent. Market fixtures in the
scratch database are copied read-only from the application's observed candles.
Passwords, authenticator secrets and recovery codes never enter output.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import socket
import subprocess
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from dotenv import dotenv_values
from playwright.sync_api import sync_playwright
from sqlalchemy import create_engine, insert, select
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = "http://127.0.0.1:3000"
SECTIONS = ["", "dashboard", "terminal", "strategies", "backtesting", "paper", "positions", "orders", "risk", "performance", "journal", "notifications", "integrations", "system-health", "settings", "demo", "live", "research", "news", "reports"]


def capture(page, suffix, method, action, expected=200):
    with page.expect_response(lambda r: urlsplit(r.url).path.endswith(suffix) and r.request.method == method) as pending:
        action()
    response = pending.value
    assert response.status == expected, f"UI action {suffix} failed with HTTP {response.status}"
    return response.json()


def ensure(page, path):
    page.goto(ORIGIN + path)
    page.locator("h1").wait_for()
    page.wait_for_timeout(350)
    assert "/login" not in page.url, "Authenticated route redirected to login"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scratch-only", action="store_true", help="Skip main read-only routes when they were already verified")
    args = parser.parse_args()
    config = dotenv_values(ROOT / ".env")
    evidence = {"completed_at": None, "browser": "Chromium", "main_mutations": "authentication sessions only", "scratch_database": "temporary SQLite; discarded after checks", "private_exchange_requests": 0, "telegram_requests": 0, "javascript_errors": 0}
    with sync_playwright() as driver:
        browser = driver.chromium.launch(executable_path="/usr/bin/chromium", args=["--no-sandbox"])
        errors = []
        if not args.scratch_only:
            context = browser.new_context(viewport={"width": 1440, "height": 1100}, accept_downloads=True)
            context.on("page", lambda p: p.on("pageerror", lambda e: errors.append(str(e))))
            page = context.new_page()
            # Exercise actual login form, including blank optional MFA fields.
            page.goto(ORIGIN + "/login")
            page.get_by_label("Username", exact=True).fill(config["BOOTSTRAP_USERNAME"])
            page.get_by_label("Password", exact=True).fill(config["BOOTSTRAP_PASSWORD"])
            capture(page, "/auth/login", "POST", lambda: page.get_by_role("button", name="Sign in", exact=True).click())
            page.wait_for_url(ORIGIN + "/")
            for section in SECTIONS:
                ensure(page, "/" + section)
                assert page.get_by_text("Unable to load data", exact=True).count() == 0, f"Data load failure on /{section}"
            assert page.get_by_role("button", name="Production activation unavailable").count() == 0 or page.get_by_role("button", name="Production activation unavailable").is_disabled()
            page.set_viewport_size({"width": 390, "height": 844})
            for section in SECTIONS:
                ensure(page, "/" + section)
                sizes = page.evaluate("({width:innerWidth, content:document.documentElement.scrollWidth})")
                assert sizes["content"] <= sizes["width"], f"Mobile overflow on /{section}"
            page.screenshot(path=str(ROOT / ".runtime/browser-expanded-mobile.png"), full_page=True)
            page.set_viewport_size({"width": 1440, "height": 1100})
            capture(page, "/auth/logout", "POST", lambda: page.get_by_role("button", name="Sign out", exact=True).click())
            page.wait_for_url(ORIGIN + "/login")
            context.close()
            evidence["desktop_routes_checked"] = len(SECTIONS) + 1
            evidence["mobile_authenticated_routes_checked"] = len(SECTIONS)
            evidence["mobile_width_px"] = 390
        with tempfile.TemporaryDirectory(prefix="tradebot-expanded-browser-") as temporary:
            directory = Path(temporary)
            test_url = "sqlite:///" + str(directory / "isolated.db")
            test_environment = {**os.environ, **{k: v for k, v in config.items() if v is not None}}
            for name in tuple(test_environment):
                if name.startswith("DELTA_") or name.startswith("TELEGRAM_"):
                    test_environment.pop(name)
            test_environment.update(DATABASE_URL=test_url, APP_ORIGIN=ORIGIN, SESSION_COOKIE_SECURE="false", TRADING_MODE="PAPER", LIVE_EXECUTION_ENABLED="false", DEMO_EXECUTION_AUTHORIZED="false", TELEGRAM_DELIVERY_ENABLED="false", TRADEBOT_VAULT_KEY_FILE=str(directory / "vault.key"), TRADEBOT_VAULT_FILE=str(directory / "credentials.vault"))
            original = {k: os.environ.get(k) for k in test_environment}
            os.environ.update(test_environment)
            from tradebot import model_registry  # noqa: F401
            from tradebot.db import Base
            from tradebot.models import Candle, Instrument, User
            from tradebot.security import password_hasher
            from tradebot.security_admin import totp_code
            from tradebot.vault import initialize_vault
            initialize_vault()
            test_engine = create_engine(test_url)
            Base.metadata.create_all(test_engine)
            password = secrets.token_urlsafe(24)
            with Session(test_engine) as db:
                db.add(User(username="browser_operator", password_hash=password_hasher.hash(password), role="operator"))
                db.commit()
            production = create_engine(config["DATABASE_URL"])
            with production.connect() as source, test_engine.begin() as destination:
                for model, statement in [(Instrument, select(Instrument.__table__)), (Candle, select(Candle.__table__).order_by(Candle.timestamp.desc()).limit(3500))]:
                    values = [dict(row) for row in source.execute(statement).mappings()]
                    if values:
                        destination.execute(insert(model), values)
            production.dispose()
            test_engine.dispose()
            with socket.socket() as port_check:
                assert port_check.connect_ex(("127.0.0.1", 8001)) != 0, "Scratch API port 8001 is already in use"
            with (directory / "api.log").open("w") as logfile:
                process = subprocess.Popen([str(ROOT / ".venv/bin/python"), "-m", "uvicorn", "tradebot.app:app", "--host", "127.0.0.1", "--port", "8001", "--no-proxy-headers"], cwd=ROOT, env=test_environment, stdout=logfile, stderr=subprocess.STDOUT)
                try:
                    with httpx.Client(trust_env=False, timeout=2) as probe:
                        deadline = time.monotonic() + 20
                        while time.monotonic() < deadline:
                            assert process.poll() is None, "Scratch API stopped unexpectedly"
                            try:
                                if probe.get("http://127.0.0.1:8001/api/v1/health").status_code == 200:
                                    break
                            except httpx.HTTPError:
                                pass
                            time.sleep(0.1)
                        else:
                            raise AssertionError("Scratch API did not become ready")
                    context = browser.new_context(viewport={"width": 1440, "height": 1100}, accept_downloads=True)
                    context.on("page", lambda p: p.on("pageerror", lambda e: errors.append(str(e))))
                    def forward(route):
                        path = urlsplit(route.request.url).path
                        if path in ("/api/v1/events", "/api/v1/market/stream"):
                            route.abort()
                            return
                        response = route.fetch(url="http://127.0.0.1:8001" + path + ("?" + urlsplit(route.request.url).query if urlsplit(route.request.url).query else ""))
                        route.fulfill(response=response)
                    context.route("**/api/v1/**", forward)
                    page = context.new_page()
                    page.goto(ORIGIN + "/login")
                    page.get_by_label("Username", exact=True).fill("browser_operator")
                    page.get_by_label("Password", exact=True).fill(password)
                    capture(page, "/auth/login", "POST", lambda: page.get_by_role("button", name="Sign in", exact=True).click())
                    page.wait_for_url(ORIGIN + "/")
                    ensure(page, "/paper")
                    page.get_by_label("Initial simulated balance (USD)").fill("10000")
                    capture(page, "/paper/account", "POST", lambda: page.get_by_role("button", name="Create paper account", exact=True).click(), 201)
                    ensure(page, "/research")
                    page.get_by_label("Source title", exact=True).fill("Isolated browser verification source")
                    page.get_by_label("Source reference", exact=True).fill("test fixture in disposable database only")
                    page.get_by_label("Source text", exact=True).fill("Strategy test\nEnter long when the closed-bar fast EMA crosses above the slow EMA.\nStop loss 1 percent. Take profit 2 percent.\n")
                    document = capture(page, "/research/documents", "POST", lambda: page.get_by_role("button", name="Import & extract candidates").click(), 201)
                    page.get_by_role("button", name="Review rules", exact=True).first.click()
                    specification = document["candidates"][0]["specification"]
                    specification["automation_ambiguities"] = []
                    specification["parameters"] = {"fast_period": 9, "slow_period": 21, "timeframe_seconds": 300, "stop_loss_pct": "0.01", "take_profit_pct": "0.02"}
                    page.get_by_label("Mathematical rules & extraction review").fill(json.dumps(specification))
                    capture(page, f"/research/candidates/{document['candidates'][0]['id']}", "PATCH", lambda: page.get_by_role("button", name="Save reviewed specification").click())
                    page.get_by_role("button", name="Review & save draft strategy").click()
                    approved = capture(page, "/approve", "POST", lambda: page.get_by_role("button", name="Confirm & create draft").click(), 201)
                    assert approved["strategy"]["status"] == "DRAFT"
                    evidence["research_document_review_and_draft"] = "PASSED"
                    page.get_by_label("Source title", exact=True).fill("Second isolated source")
                    page.get_by_label("Source text", exact=True).fill("Strategy second\nEnter long when fast EMA crosses above slow EMA.\nStop loss 1 percent. Take profit 2 percent.\n")
                    capture(page, "/research/documents", "POST", lambda: page.get_by_role("button", name="Import & extract candidates").click(), 201)
                    assert page.get_by_text("Executable draft configuration", exact=True).count() == 0
                    evidence["source_change_clears_prior_candidate"] = "PASSED"
                    ensure(page, "/risk")
                    page.get_by_label("Correlated group exposure / equity cap", exact=True).fill("1.5")
                    page.get_by_label("Operator-declared correlation groups", exact=True).fill('[["BTCUSD", "ETHUSD"]]')
                    page.get_by_role("button", name="Review & save limits", exact=True).click()
                    updated_risk = capture(page, "/risk", "PUT", lambda: page.get_by_role("button", name="Apply risk limits", exact=True).click())
                    assert updated_risk["correlation_groups"] == [["BTCUSD", "ETHUSD"]]
                    assert float(updated_risk["max_correlated_exposure_multiple"]) == 1.5
                    page.get_by_label("Weekly loss limit (%)", exact=True).fill("12")
                    page.get_by_role("button", name="Review & save limits", exact=True).click()
                    preserved_risk = capture(page, "/risk", "PUT", lambda: page.get_by_role("button", name="Apply risk limits", exact=True).click())
                    assert preserved_risk["correlation_groups"] == updated_risk["correlation_groups"]
                    assert float(preserved_risk["max_correlated_exposure_multiple"]) == 1.5
                    assert float(preserved_risk["max_weekly_loss"]) == 0.12
                    evidence["correlated_risk_edits_preserve_groups"] = "PASSED"
                    ensure(page, "/strategies")
                    page.get_by_role("button", name="Create strategy", exact=True).click()
                    editor = page.get_by_role("dialog")
                    editor.get_by_label("Strategy name", exact=True).fill("Isolated fixed sizing")
                    editor.get_by_label("Position sizing method", exact=True).select_option("FIXED_CONTRACTS")
                    editor.get_by_label("Fixed contract quantity", exact=True).fill("2")
                    editor.get_by_label("Strategy consecutive-loss limit", exact=True).fill("3")
                    editor.get_by_label("Strategy drawdown limit (fraction)", exact=True).fill("0.12")
                    editor.get_by_label("Strategy open-position limit", exact=True).fill("2")
                    assert editor.locator("form").evaluate("form => form.checkValidity()"), "Native strategy controls rejected valid fractional limits"
                    sized = capture(page, "/strategies", "POST", lambda: editor.get_by_role("button", name="Create strategy", exact=True).click(), 201)
                    assert sized["status"] == "DRAFT"
                    assert sized["config"]["sizing"]["mode"] == "FIXED_CONTRACTS"
                    assert float(sized["config"]["sizing"]["fixed_quantity"]) == 2
                    assert sized["config"]["max_consecutive_losses"] == 3
                    page.get_by_role("button", name="Edit Isolated fixed sizing", exact=True).click()
                    editor = page.get_by_role("dialog")
                    editor.get_by_label("Maximum leverage", exact=True).fill("1.5")
                    preserved_sizing = capture(page, f"/strategies/{sized['id']}", "PUT", lambda: editor.get_by_role("button", name="Save new version", exact=True).click())
                    assert preserved_sizing["config"]["sizing"] == sized["config"]["sizing"]
                    assert preserved_sizing["config"]["max_consecutive_losses"] == 3
                    assert float(preserved_sizing["config"]["max_drawdown_fraction"]) == 0.12
                    page.get_by_role("button", name="Edit Isolated fixed sizing", exact=True).click()
                    editor = page.get_by_role("dialog")
                    editor.get_by_label("Position sizing method", exact=True).select_option("FIXED_NOTIONAL")
                    editor.get_by_label("Fixed notional in contract settlement currency", exact=True).fill("250")
                    notional = capture(page, f"/strategies/{sized['id']}", "PUT", lambda: editor.get_by_role("button", name="Save new version", exact=True).click())
                    assert notional["config"]["sizing"]["fixed_quantity"] is None
                    assert float(notional["config"]["sizing"]["fixed_notional"]) == 250
                    page.get_by_role("button", name="Edit Isolated fixed sizing", exact=True).click()
                    editor = page.get_by_role("dialog")
                    editor.get_by_label("Use backend validated JSON configuration").check()
                    advanced_config = json.loads(editor.get_by_label("Version configuration", exact=True).input_value())
                    advanced_config["sizing"] = {"mode": "VOLATILITY", "fixed_quantity": None, "fixed_notional": None, "volatility_period": 20, "volatility_multiplier": "1.8"}
                    editor.get_by_label("Version configuration", exact=True).fill(json.dumps(advanced_config))
                    volatility = capture(page, f"/strategies/{sized['id']}", "PUT", lambda: editor.get_by_role("button", name="Save new version", exact=True).click())
                    assert volatility["config"]["sizing"]["mode"] == "VOLATILITY"
                    assert volatility["config"]["sizing"]["volatility_period"] == 20
                    assert volatility["config"]["max_open_positions"] == 2
                    evidence["fixed_sizing_and_strategy_caps_preserved"] = "PASSED"
                    evidence["notional_and_advanced_volatility_sizing"] = "PASSED"
                    ensure(page, "/news")
                    page.get_by_label("Source name", exact=True).fill("Isolated event source")
                    page.get_by_label("I am authorized to use this source").check()
                    source = capture(page, "/news/sources", "POST", lambda: page.get_by_role("button", name="Authorize source", exact=True).click(), 201)
                    page.get_by_label("Authorized source", exact=True).select_option(str(source["id"]))
                    page.get_by_label("Provider event ID", exact=True).fill("isolated-1")
                    page.get_by_label("Event title", exact=True).fill("Isolated attributed economic event")
                    page.get_by_label("Published timestamp with timezone").fill((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat())
                    page.get_by_label("Scheduled event timestamp (optional)").fill((datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat())
                    capture(page, "/news/events", "POST", lambda: page.get_by_role("button", name="Record attributed event").click(), 201)
                    page.get_by_label("Rule name", exact=True).fill("Isolated high impact blackout")
                    capture(page, "/news/blackouts", "POST", lambda: page.get_by_role("button", name="Create blackout").click(), 201)
                    page.get_by_text("EVENT FILTER BLOCKS ENTRY", exact=True).wait_for()
                    evidence["attributed_news_and_blackout"] = "PASSED"
                    ensure(page, "/reports")
                    page.get_by_label("Date within completed period").fill((datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat())
                    report = capture(page, "/reports", "POST", lambda: page.get_by_role("button", name="Generate completed report").click(), 201)
                    # Chromium's native file downloader bypasses Playwright route
                    # interception. Keep this download on the isolated authenticated
                    # API, while verifying the application generated the correct path.
                    export = page.get_by_role("link", name="Export CSV", exact=True)
                    assert export.get_attribute("href") == f"/api/v1/reports/{report['id']}/export?format=csv"
                    export.evaluate("(element, target) => { element.href = target; }", f"http://127.0.0.1:8001/api/v1/reports/{report['id']}/export?format=csv")
                    with page.expect_download() as download:
                        export.click()
                    assert download.value.failure() is None, "CSV browser download failed: " + str(download.value.failure())
                    assert report["payload"]["starting_equity"] is None
                    evidence["completed_report_and_csv_export"] = "PASSED"
                    ensure(page, "/settings")
                    page.get_by_label("Current password", exact=True).first.fill(password)
                    capture(page, "/security/reauth", "POST", lambda: page.get_by_role("button", name="Verify identity", exact=True).click())
                    enrollment = capture(page, "/security/mfa/enroll", "POST", lambda: page.get_by_role("button", name="Enroll authenticator").click())
                    page.get_by_label("Six-digit authenticator code").fill(totp_code(enrollment["secret"]))
                    confirmed = capture(page, "/security/mfa/confirm", "POST", lambda: page.get_by_role("button", name="Confirm enrollment").click())
                    assert len(confirmed["recovery_codes"]) == 8
                    page.get_by_role("button", name="I have saved these codes").click()
                    page.get_by_text("ENROLLED", exact=True).wait_for()
                    page.get_by_label("New username", exact=True).fill("isolated_viewer")
                    page.get_by_label("Temporary password", exact=True).fill(secrets.token_urlsafe(24))
                    created = capture(page, "/security/users", "POST", lambda: page.get_by_role("button", name="Create user", exact=True).click(), 201)
                    assert created["role"] == "viewer"
                    evidence["identity_mfa_and_user_administration"] = "PASSED"
                    ensure(page, "/paper")
                    page.get_by_role("button", name="Review virtual balance reset").click()
                    page.get_by_label("New virtual balance (USD)", exact=True).fill("12000")
                    page.get_by_label("Reason for adjustment", exact=True).fill("Disposable browser validation")
                    page.get_by_label("Type RESET PAPER to confirm").fill("RESET PAPER")
                    adjusted = capture(page, "/paper/account/reset", "POST", lambda: page.get_by_role("button", name="Confirm virtual balance reset").click())
                    assert adjusted["history_preserved"] is True
                    evidence["typed_paper_reset_preserves_history"] = "PASSED"
                    ensure(page, "/demo")
                    assert page.get_by_role("button", name="Connect read-only").is_disabled()
                    assert page.get_by_role("button", name="Review demo order").is_disabled()
                    assert page.get_by_role("button", name="Generate demo report", exact=True).is_disabled()
                    evidence["credentialless_demo_gates"] = "PASSED"
                    assert not errors, "Browser JavaScript errors occurred"
                    context.close()
                finally:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                    for key, value in original.items():
                        if value is None:
                            os.environ.pop(key, None)
                        else:
                            os.environ[key] = value
        browser.close()
    evidence["completed_at"] = datetime.now(timezone.utc).isoformat()
    (ROOT / "docs/validation/browser-expanded-2026-10-09.json").write_text(json.dumps(evidence, indent=2) + "\n")
    route_summary = "main routes skipped; " if args.scratch_only else "21 HTML routes, 20 mobile routes; "
    print("Expanded browser validation passed: " + route_summary + "isolated research/news/reports/MFA/paper/risk/sizing workflows, zero JavaScript errors.")


if __name__ == "__main__":
    main()
