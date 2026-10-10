"""Manage only the local development processes recorded by this script."""
from __future__ import annotations

import argparse
import json
import os
import signal
import shutil
import subprocess
import time
from pathlib import Path

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".runtime"
RUNTIME.mkdir(exist_ok=True, mode=0o700)
ENV = {**os.environ, **{k: v for k, v in dotenv_values(ROOT / ".env").items() if v is not None}}
ENV["NEXT_TELEMETRY_DISABLED"] = "1"
ENV.setdefault("DOCKER_CONFIG", "/workspace/.cache/docker")
PYTHON = str(ROOT / ".venv" / "bin" / "python")


def fingerprint(pid: int) -> str | None:
    try:
        # /proc fields after ')' begin at field 3; field 22 is process start time.
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return fields[19] if fields[0] not in {"Z", "X"} else None
    except (OSError, IndexError):
        return None


def record(name: str) -> dict | None:
    path = RUNTIME / f"{name}.pid.json"
    try:
        item = json.loads(path.read_text())
        if item["start_time"] is not None and item["start_time"] == fingerprint(item["pid"]):
            return item
    except (OSError, ValueError, KeyError):
        pass
    return None


def start(name: str, command: list[str], cwd: Path = ROOT, overrides: dict | None = None) -> None:
    if record(name):
        print(f"{name}: already running")
        return
    fd = os.open(RUNTIME / f"{name}.log", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "ab") as logfile:
        process = subprocess.Popen(command, cwd=cwd, env={**ENV, **(overrides or {})}, stdin=subprocess.DEVNULL,
                                   stdout=logfile, stderr=subprocess.STDOUT, start_new_session=True)
    (RUNTIME / f"{name}.pid.json").write_text(json.dumps({"pid": process.pid, "start_time": fingerprint(process.pid)}))
    print(f"{name}: started")


def stop(name: str) -> None:
    item = record(name)
    if item:
        os.killpg(item["pid"], signal.SIGTERM)
        print(f"{name}: stopped")
    (RUNTIME / f"{name}.pid.json").unlink(missing_ok=True)


def wait_for(url: str, name: str) -> None:
    for _ in range(50):
        try:
            response = httpx.get(url, timeout=2, trust_env=False)
            if response.status_code == 200:
                print(f"{name}: HTTP readiness verified")
                return
        except httpx.HTTPError:
            pass
        if not record(name):
            break
        time.sleep(.4)
    raise SystemExit(f"{name}: readiness failed; inspect .runtime/{name}.log")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["up", "down", "status", "restart"], default="up", nargs="?")
    args = parser.parse_args()
    if args.action in {"down", "restart"}:
        for name in ("web", "auxiliary", "demo", "worker", "api"):
            stop(name)
        if args.action == "down":
            return
        time.sleep(.5)
    if args.action == "status":
        for name in ("api", "worker", "demo", "auxiliary", "web"):
            print(f"{name}: {'running' if record(name) else 'stopped'}")
        return
    subprocess.run(["docker", "compose", "up", "-d", "--wait", "postgres", "redis"], cwd=ROOT, env=ENV, check=True)
    subprocess.run([PYTHON, "scripts/restore-snapshot.py"], cwd=ROOT, env=ENV, check=True)
    subprocess.run([str(ROOT / ".venv/bin/alembic"), "-c", "services/api/alembic.ini", "upgrade", "head"], cwd=ROOT, env=ENV, check=True)
    subprocess.run([PYTHON, "-m", "tradebot.cli", "bootstrap"], cwd=ROOT, env=ENV, check=True)
    subprocess.run([PYTHON, "-m", "tradebot.vault", "init"], cwd=ROOT, env=ENV, check=True)
    start("api", [str(ROOT / ".venv/bin/uvicorn"), "tradebot.app:app", "--host", "127.0.0.1", "--port", "8000", "--no-proxy-headers"])
    wait_for("http://127.0.0.1:8000/api/v1/health/ready", "api")
    start("worker", [PYTHON, "-m", "tradebot.worker"])
    start("demo", [PYTHON, "-m", "tradebot.demo_worker"])
    start("auxiliary", [PYTHON, "-m", "tradebot.auxiliary_worker"])
    standalone = ROOT / "apps/web/.next/standalone"
    if (standalone / "server.js").exists():
        shutil.copytree(ROOT / "apps/web/.next/static", standalone / ".next/static", dirs_exist_ok=True)
        start("web", ["node", "server.js"], standalone, {"HOSTNAME": "127.0.0.1", "PORT": "3000"})
    else:
        start("web", ["npm", "run", "start", "--", "--hostname", "127.0.0.1", "--port", "3000"], ROOT / "apps/web")
    wait_for("http://127.0.0.1:3000", "web")


if __name__ == "__main__":
    main()
