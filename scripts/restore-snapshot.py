"""Restore retained state only to a genuinely empty development database."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy import create_engine, text

root = Path(__file__).resolve().parents[1]
config = {**dotenv_values(root / ".env"), **os.environ}
backup = root / ".runtime" / "backups" / "snapshot.dump"
engine = create_engine(config["DATABASE_URL"])
with engine.connect() as connection:
    tables = connection.execute(text("select count(*) from information_schema.tables where table_schema='public'" )).scalar()
if not backup.exists() or tables:
    print("Preserving current database; snapshot restore is unnecessary.")
else:
    with backup.open("rb") as source:
        subprocess.run([
            "docker", "compose", "exec", "-T", "postgres", "pg_restore", "--exit-on-error", "--no-owner", "--no-acl",
            "--username", config.get("POSTGRES_USER", "tradebot"),
            "--dbname", config.get("POSTGRES_DB", "tradebot"),
        ], cwd=root, stdin=source, check=True)
    print("Restored retained snapshot into the empty development database.")
