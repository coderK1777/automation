"""Create owner-only read-only container mounts; never expose credential values."""
import os
import shutil
from pathlib import Path

from dotenv import dotenv_values

from tradebot.vault import initialize_vault

root = Path(__file__).resolve().parents[1]
os.chdir(root)
for key, value in dotenv_values(root / ".env").items():
    if key.startswith("TRADEBOT_VAULT_") and value:
        os.environ.setdefault(key, value)
initialize_vault()
directory = root / ".runtime/container-secrets"
directory.mkdir(mode=0o700, parents=True, exist_ok=True)
for name, variable in (("vault.key", "TRADEBOT_VAULT_KEY_FILE"), ("credentials.vault", "TRADEBOT_VAULT_FILE")):
    source = Path(os.environ.get(variable, str(root / ".runtime" / name)))
    destination = directory / name
    if source.is_symlink() or destination.is_symlink():
        raise SystemExit("Vault mounts must be regular owner-only files")
    if source.exists():
        if source.stat().st_mode & 0o077:
            raise SystemExit("Vault source permissions must remain owner-only")
        temporary = directory / (name+".partial")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with source.open("rb") as incoming, os.fdopen(descriptor, "wb") as outgoing:
            shutil.copyfileobj(incoming, outgoing)
        os.chmod(temporary, 0o600)
        if os.getuid() == 0:
            os.chown(temporary, 10001, 10001)
        temporary.replace(destination)
os.chmod(directory, 0o700)
if os.getuid() == 0:
    os.chown(directory, 10001, 10001)
print("Prepared owner-only container vault mounts; Compose uses the mapped unprivileged owner. No credential values printed.")
