#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export UV_CACHE_DIR="${UV_CACHE_DIR:-/workspace/.cache/uv}"
export DOCKER_CONFIG="${DOCKER_CONFIG:-/workspace/.cache/docker}"
export NEXT_TELEMETRY_DISABLED=1
uv sync --frozen
bash scripts/setup-local.sh
.venv/bin/python -m tradebot.vault init
npm ci --prefix apps/web --cache /workspace/.cache/npm --no-audit --no-fund
npm run build --prefix apps/web
docker compose up -d --wait postgres redis
.venv/bin/python scripts/prepare-trust.py
# dev.py loads local environment without evaluating shell content and starts
# the actual services after migration/bootstrap. Startup is saved separately.
