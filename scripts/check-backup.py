"""Restore a backup in a disposable DB and verify actual records, then remove it."""
from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path

from dotenv import dotenv_values
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

root = Path(__file__).resolve().parents[1]
config = {**dotenv_values(root / ".env"), **os.environ}
subprocess.run([str(root / ".venv/bin/python"), "scripts/backup.py"], cwd=root, check=True)
database = f"tradebot_restore_{uuid.uuid4().hex[:16]}"
url = make_url(config["DATABASE_URL"])
admin = create_engine(url, isolation_level="AUTOCOMMIT")
scratch = create_engine(url.set(database=database))
created = False
try:
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{database}"'))
        created = True
    with (root / ".runtime/backups/snapshot.dump").open("rb") as source:
        subprocess.run([
            "docker", "compose", "exec", "-T", "postgres", "pg_restore", "--exit-on-error", "--no-owner", "--no-acl",
            "--username", config.get("POSTGRES_USER", "tradebot"), "--dbname", database,
        ], cwd=root, stdin=source, check=True)
    with scratch.connect() as connection:
        migrations = connection.execute(text("select version_num from alembic_version")).scalars().all()
        users = connection.execute(text("select count(*) from users")).scalar()
        instruments = connection.execute(text("select count(*) from instruments")).scalar()
        expected_head = ScriptDirectory.from_config(Config(str(root / "services/api/alembic.ini"))).get_current_head()
        assert migrations == [expected_head] and users > 0, "Restored migration head or bootstrap records missing"
        from tradebot import model_registry  # noqa: F401
        from tradebot.db import Base
        missing = set(Base.metadata.tables)-set(inspect(connection).get_table_names())
        assert not missing, "Restored application tables are missing"
        print(f"Backup restore verified: current migration {expected_head}, all application tables, {users} user(s), {instruments} instrument(s).")
finally:
    scratch.dispose()
    if created:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{database}" WITH (FORCE)'))
    admin.dispose()
