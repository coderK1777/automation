"""Exercise the built connected UI; authentication values never enter logs."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import dotenv_values
from playwright.sync_api import sync_playwright

root = Path(__file__).resolve().parents[1]
config = dotenv_values(root / ".env")
origin = "http://127.0.0.1:3000"
pages = ["", "strategies", "backtesting", "paper", "terminal", "positions", "orders", "risk", "performance", "journal", "research", "notifications", "integrations", "system-health", "settings", "demo", "live"]


def main():
    with httpx.Client(base_url=origin, trust_env=False, timeout=30, headers={"Origin": config["APP_ORIGIN"]}) as client:
        response = client.post("/api/v1/auth/login", json={"username": config["BOOTSTRAP_USERNAME"], "password": config["BOOTSTRAP_PASSWORD"]})
        assert response.status_code == 200, "Browser validation login failed"
        csrf = response.json()["csrf_token"]
        candidates = client.get("/api/v1/strategies").json()
        strategy = next(s for s in candidates if s["status"] == "VALIDATED")
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path="/usr/bin/chromium", args=["--no-sandbox"])
            context = browser.new_context(viewport={"width": 1440, "height": 1100})
            context.add_cookies([{"name": "tradebot_session", "value": client.cookies.get("tradebot_session"), "url": origin, "httpOnly": True, "sameSite": "Strict"}])
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            for path in pages:
                page.goto(f"{origin}/{path}")
                page.locator("h1").wait_for()
                assert "/login" not in page.url
            page.goto(f"{origin}/backtesting?strategy={strategy['id']}")
            page.get_by_role("button", name="Run backtest", exact=True).wait_for()
            with page.expect_response(lambda r: "/api/v1/backtests" in r.url and r.request.method == "POST") as captured:
                page.get_by_role("button", name="Run backtest", exact=True).click()
            assert captured.value.status == 201
            assert captured.value.json()["status"] == "COMPLETED"
            page.goto(origin)
            page.locator("h1").wait_for()
            page.screenshot(path=str(root / ".runtime/browser-dashboard-final.png"), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            for path in ["", "strategies", "backtesting", "paper", "risk", "terminal"]:
                page.goto(f"{origin}/{path}")
                page.locator("h1").wait_for()
                page.wait_for_timeout(250)
                sizes = page.evaluate("({width:innerWidth, content:document.documentElement.scrollWidth})")
                assert sizes["content"] <= sizes["width"], f"Mobile viewport overflow: /{path}"
            assert not errors, "Browser JavaScript errors occurred"
            page.screenshot(path=str(root / ".runtime/browser-mobile-final.png"), full_page=True)
            browser.close()
        client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf}, json={}).raise_for_status()
    evidence = {"completed_at": datetime.now(timezone.utc).isoformat(), "desktop_routes_checked": len(pages), "mobile_routes_checked": 6,
                "mobile_width_px": 390, "browser": "Chromium", "javascript_errors": 0, "real_data_backtest_via_ui": "COMPLETED", "mode": "PAPER"}
    (root / "docs/validation/browser-2026-10-09.json").write_text(json.dumps(evidence, indent=2) + "\n")
    print("Browser validation passed: 17 routes, real-data backtest, six mobile screens, zero JavaScript errors.")


if __name__ == "__main__":
    main()
