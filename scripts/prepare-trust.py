"""Copy only a public trust bundle for verified container dependency installation."""
import os
from pathlib import Path

destination = Path(__file__).resolve().parents[1] / ".runtime" / "ca-bundle.pem"
destination.parent.mkdir(mode=0o700, exist_ok=True)
source = os.environ.get("SSL_CERT_FILE") or "/etc/ssl/certs/ca-certificates.crt"
destination.write_bytes(Path(source).read_bytes())
destination.chmod(0o644)  # Public certificates must be readable by unprivileged app containers.
print("Prepared public CA trust bundle; TLS verification stays enabled.")
