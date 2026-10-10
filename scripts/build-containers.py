"""Build verified images using the managed proxy and its public trust bundle."""
from __future__ import annotations

import argparse
import os
import socket
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("target", choices=["api", "web", "all"], default="all", nargs="?")
args = parser.parse_args()
environment = {**os.environ, "DOCKER_CONFIG": os.environ.get("DOCKER_CONFIG", "/workspace/.cache/docker")}
subprocess.run([str(root / ".venv/bin/python"), "scripts/prepare-trust.py"], cwd=root, env=environment, check=True)
base = ["docker", "build", "--secret", "id=platform_ca,src=.runtime/ca-bundle.pem"]
# Docker's inner DNS may not resolve the managed egress proxy. Resolve only
# the configured proxy hostname on the host, retaining the same proxy route.
mapped = set()
for variable in ("HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY"):
    if variable in environment:
        base += ["--build-arg", variable]
    if variable == "NO_PROXY":
        continue
    hostname = urlsplit(environment.get(variable, "")).hostname
    if hostname and hostname not in mapped:
        addresses = socket.getaddrinfo(hostname, None, socket.AF_INET)
        base += ["--add-host", f"{hostname}:{addresses[0][4][0]}"]
        mapped.add(hostname)
for target in (["api", "web"] if args.target == "all" else [args.target]):
    command = [*base]
    if target == "web":
        command += ["--build-arg", "BACKEND_URL=http://api:8000"]
    command += ["-f", f"infrastructure/Dockerfile.{target}", "-t", f"tradebot-{target}:local", "."]
    subprocess.run(command, cwd=root, env=environment, check=True)
