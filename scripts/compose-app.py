"""Start the Docker app with the managed proxy route when one is configured."""
from __future__ import annotations

import argparse
import os
import socket
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("action", choices=["up", "down", "status"], default="up", nargs="?")
args = parser.parse_args()
environment = {**os.environ, "DOCKER_CONFIG": os.environ.get("DOCKER_CONFIG", "/workspace/.cache/docker")}
environment["TRADEBOT_RUNTIME_UID"] = str(os.getuid() or 10001)
environment["TRADEBOT_RUNTIME_GID"] = str(os.getgid() or 10001)
command = ["docker", "compose", "-f", "docker-compose.yml"]
proxy = urlsplit(environment.get("HTTPS_PROXY", "")).hostname
if proxy:
    environment["TRADEBOT_PROXY_HOST"] = proxy
    environment["TRADEBOT_PROXY_IP"] = socket.getaddrinfo(proxy, None, socket.AF_INET)[0][4][0]
    command += ["-f", "infrastructure/docker-runtime-proxy.yml"]
    subprocess.run([str(root / ".venv/bin/python"), "scripts/prepare-trust.py"], cwd=root, env=environment, check=True)
command += ["--profile", "app"]


def run(arguments):
    subprocess.run([*command, *arguments], cwd=root, env=environment, check=True)


if args.action == "status":
    run(["ps"])
elif args.action == "down":
    run(["stop", "web", "auxiliary", "demo", "worker", "api"])
else:
    # Stop only the host processes registered by our development manager.
    subprocess.run([str(root / ".venv/bin/python"), "scripts/dev.py", "down"], cwd=root, env=environment, check=True)
    subprocess.run([str(root / ".venv/bin/python"), "scripts/prepare-vault.py"], cwd=root, env=environment, check=True)
    run(["up", "-d", "--wait", "postgres", "redis"])
    subprocess.run([str(root / ".venv/bin/python"), "scripts/restore-snapshot.py"], cwd=root, env=environment, check=True)
    run(["run", "--rm", "--no-deps", "api", "python", "-m", "tradebot.cli", "init-db"])
    run(["run", "--rm", "--no-deps", "api", "python", "-m", "tradebot.cli", "bootstrap"])
    run(["up", "--no-build", "-d", "--wait"])
