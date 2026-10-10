#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 - <<'PY'
import os
import secrets
from pathlib import Path
path = Path('.env')
if path.exists():
    print('Preserving existing .env; no credentials changed.')
else:
    pg, redis, password = (secrets.token_hex(24) for _ in range(3))
    contents = '\n'.join([
        'APP_ENV=development', 'TRADING_MODE=PAPER', 'LIVE_EXECUTION_ENABLED=false',
        'SESSION_COOKIE_SECURE=false', 'APP_ORIGIN=http://127.0.0.1:3000',
        'POSTGRES_USER=tradebot', 'POSTGRES_DB=tradebot', f'POSTGRES_PASSWORD={pg}',
        f'REDIS_PASSWORD={redis}',
        f'DATABASE_URL=postgresql+psycopg://tradebot:{pg}@127.0.0.1:5432/tradebot',
        f'REDIS_URL=redis://:{redis}@127.0.0.1:6379/0',
        'BOOTSTRAP_USERNAME=operator', f'BOOTSTRAP_PASSWORD={password}',
        'BACKEND_URL=http://127.0.0.1:8000',
    ]) + '\n'
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write(contents)
    print('Generated local development credentials in ignored .env (mode 600).')
PY
