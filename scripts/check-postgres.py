"""Run API/fault/concurrency tests against a disposable PostgreSQL schema."""
from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

root = Path(__file__).resolve().parents[1]
config = {**dotenv_values(root / ".env"), **os.environ}
schema = f"tradebot_test_{uuid.uuid4().hex}"
url = make_url(config["DATABASE_URL"])
admin = create_engine(url)
with admin.begin() as connection:
    connection.execute(text(f'CREATE SCHEMA "{schema}"'))
test_url = url.update_query_dict({"options": f"-csearch_path={schema}"})
try:
    result = subprocess.run([
        str(root / ".venv/bin/python"), "-m", "pytest", "tests/integration", "tests/fault_injection", "tests/end_to_end", "tests/security", "-q", "--tb=short",
    ], cwd=root, env={**os.environ, "TEST_DATABASE_URL": test_url.render_as_string(hide_password=False)})
finally:
    with admin.begin() as connection:
        connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    admin.dispose()
raise SystemExit(result.returncode)
