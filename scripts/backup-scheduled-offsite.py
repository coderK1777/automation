"""Encrypt and deliver a recent local backup only to a configured destination.

The optional systemd unit requires the local backup service to complete first.
Running this script without a complete authorized destination fails closed.
It is never invoked by ordinary application/cloud startup.
"""
from datetime import datetime, timezone
import importlib.util
import os
from pathlib import Path
import uuid

from dotenv import dotenv_values

root = Path(__file__).resolve().parents[1]


def main():
    config = {**dotenv_values(root/".env"), **os.environ}
    required = ("BACKUP_ENCRYPTION_KEY_FILE", "BACKUP_SFTP_HOST", "BACKUP_SFTP_USER", "BACKUP_SFTP_DIRECTORY",
                "BACKUP_SFTP_IDENTITY_FILE", "BACKUP_SFTP_KNOWN_HOSTS_FILE")
    if any(not config.get(name) for name in required):
        print("Offsite backup is not configured; no data was transferred. Provision destination, identity, verified host keys and encryption key.")
        raise SystemExit(1)
    source = root/".runtime"/"backups"/"snapshot.dump"
    if not source.is_file() or datetime.now(timezone.utc).timestamp()-source.stat().st_mtime > 90000:
        print("A successful local backup from the last 25 hours is required before encrypted offsite delivery.")
        raise SystemExit(1)
    if config.get("DATABASE_URL"):
        os.environ["DATABASE_URL"] = config["DATABASE_URL"]
    spec = importlib.util.spec_from_file_location("tradebot_offsite_backup", root/"scripts"/"backup-offsite.py")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    destination = source.with_name("snapshot-"+datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")+"-"+uuid.uuid4().hex[:12]+".tbpenc")
    try:
        helper.encrypt_backup(source, destination, Path(config["BACKUP_ENCRYPTION_KEY_FILE"]))
        helper.upload_backup(destination, host=config["BACKUP_SFTP_HOST"], user=config["BACKUP_SFTP_USER"],
            directory=config["BACKUP_SFTP_DIRECTORY"], identity=Path(config["BACKUP_SFTP_IDENTITY_FILE"]),
            known_hosts=Path(config["BACKUP_SFTP_KNOWN_HOSTS_FILE"]), key_path=Path(config["BACKUP_ENCRYPTION_KEY_FILE"]),
            port=int(config.get("BACKUP_SFTP_PORT", "22")))
    except Exception as error:
        helper.write_offsite_status(False, 0)
        print("Scheduled encrypted offsite delivery failed ("+type(error).__name__+"); backups remain preserved.")
        raise SystemExit(1) from None
    helper.write_offsite_status(True, destination.stat().st_size)
    print("Scheduled encrypted offsite delivery was acknowledged; remote restoration remains separate evidence.")


if __name__ == "__main__":
    main()
