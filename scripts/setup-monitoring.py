"""Prepare a private Grafana password file without displaying its value."""
import os
from pathlib import Path
import secrets

root = Path(__file__).resolve().parents[1]
directory = root/".runtime"/"monitoring"
directory.mkdir(mode=0o700, parents=True, exist_ok=True)
destination = directory/"grafana-password"
try:
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
except FileExistsError:
    print("Preserved the existing private Grafana password file.")
else:
    with os.fdopen(descriptor, "w") as output:
        output.write(secrets.token_urlsafe(32))
        output.flush()
        os.fsync(output.fileno())
    # The Compose service adds only this runtime group. The parent directory
    # remains private; the container receives only this single secret file.
    os.chmod(destination, 0o640)
    print("Created .runtime/monitoring/grafana-password for the operator Grafana account.")
print("Set TRADEBOT_GRAFANA_SECRET_GID to the file's group ID if it differs from 1000.")
