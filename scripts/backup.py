"""Write an atomic PostgreSQL custom-format backup; never delete active data."""
from __future__ import annotations

import os
import subprocess
import json
import fcntl
from datetime import datetime, timezone
from pathlib import Path

from dotenv import dotenv_values

root = Path(__file__).resolve().parents[1]


def write_status(directory: Path, success: bool, size_bytes: int = 0):
    """Retain actual successes while making the most recent failure visible."""
    destination = directory / "status.json"
    previous = {}
    if destination.exists():
        try:
            previous = json.loads(destination.read_text())
        except (OSError, ValueError):
            pass
    now = datetime.now(timezone.utc).isoformat()
    payload = {"last_attempt_at": now, "last_attempt_success": success,
               "last_success_at": now if success else previous.get("last_success_at"),
               "last_success_bytes": size_bytes if success else previous.get("last_success_bytes", 0),
               "scope": "local PostgreSQL dump; offsite delivery and restoration are separate evidence"}
    temporary = directory / "status.json.partial"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as output:
        json.dump(payload, output, indent=2)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(destination)
    os.chmod(destination, 0o600)


def database_status(config: dict, success: bool, size_bytes: int = 0):
    # The CLI does not otherwise load .env. Set only the database URL needed
    # for telemetry, without printing values or passing secrets on a command.
    if config.get("DATABASE_URL"):
        os.environ["DATABASE_URL"] = config["DATABASE_URL"]
    try:
        from tradebot.telemetry import record_backup
        record_backup(success, size_bytes)
    except Exception:
        print("Database backup telemetry unavailable; the private local status record remains authoritative.")


def main():
    config = {**dotenv_values(root / ".env"), **os.environ}
    directory = root / ".runtime" / "backups"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination = directory / "snapshot.dump"
    temporary = directory / "snapshot.dump.partial"
    # Serialize the timer, manual backup and restore drill so two writers
    # cannot share a partial dump or replace each other's status records.
    lock = os.open(directory / "backup.lock", os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(lock, fcntl.LOCK_EX)
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        process_env = dict(os.environ)
        process_env["DOCKER_CONFIG"] = config.get("DOCKER_CONFIG", str(root / ".cache" / "docker"))
        with os.fdopen(descriptor, "wb") as output:
            result = subprocess.run([
                "docker", "compose", "exec", "-T", "postgres", "pg_dump", "-Fc", "--no-owner", "--no-acl",
                "--username", config.get("POSTGRES_USER", "tradebot"),
                "--dbname", config.get("POSTGRES_DB", "tradebot"),
            ], cwd=root, env=process_env, stdout=output, stderr=subprocess.PIPE)
            if result.returncode:
                raise RuntimeError("PostgreSQL dump failed")
            output.flush()
            os.fsync(output.fileno())
        with temporary.open("rb") as input_file:
            signature = input_file.read(5)
        if temporary.stat().st_size <= 5 or signature != b"PGDMP":
            raise RuntimeError("PostgreSQL dump is empty or has an unexpected format")
        temporary.replace(destination)
        os.chmod(destination, 0o600)
        size = destination.stat().st_size
        write_status(directory, True, size)
        database_status(config, True, size)
        print("Saved .runtime/backups/snapshot.dump and recorded actual local backup success.")
    except Exception:
        temporary.unlink(missing_ok=True)
        write_status(directory, False)
        database_status(config, False)
        print("PostgreSQL backup failed; the previous successful snapshot was preserved.")
        raise SystemExit(1) from None
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        os.close(lock)


if __name__ == "__main__":
    main()
