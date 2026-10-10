"""Read-only application readiness probe; no market imports or trading actions."""
import argparse
import getpass
from pathlib import Path

import httpx
from dotenv import dotenv_values

root = Path(__file__).resolve().parents[1]
config = dotenv_values(root / ".env")
parser = argparse.ArgumentParser(description=__doc__)
factor = parser.add_mutually_exclusive_group()
factor.add_argument("--mfa", action="store_true", help="Prompt privately for a current authenticator code")
factor.add_argument("--recovery", action="store_true", help="Prompt privately for a one-use recovery code")
args = parser.parse_args()
credentials = {"username": config["BOOTSTRAP_USERNAME"], "password": config["BOOTSTRAP_PASSWORD"]}
if args.mfa:
    credentials["code"] = getpass.getpass("Authenticator code (hidden): ")
elif args.recovery:
    credentials["recovery_code"] = getpass.getpass("Recovery code (hidden; one use): ")
with httpx.Client(base_url="http://127.0.0.1:3000/api/v1", timeout=15, trust_env=False,
                  headers={"Origin": config["APP_ORIGIN"]}) as client:
    response = client.get("/health/ready")
    assert response.status_code == 200 and response.json()["status"] == "ready", "Database/cache/API readiness failed"
    login = client.post("/auth/login", json=credentials)
    assert login.status_code == 200, "Configured local operator authentication failed; inspect secure local settings"
    csrf = login.json()["csrf_token"]
    dashboard = client.get("/dashboard")
    assert dashboard.status_code == 200 and dashboard.json()["mode"] == "PAPER"
    worker = client.get("/worker/health")
    assert worker.status_code == 200 and worker.json()["status"] == "healthy", "Worker needs fresh successful public-data cycle"
    assert client.post("/auth/logout", json={}, headers={"X-CSRF-Token": csrf}).status_code == 200
print("Authenticated web/API, database, Redis and public-data worker readiness passed; live execution disabled.")
