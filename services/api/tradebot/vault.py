"""Encrypted local credentials with interactive configuration and recoverable rotation.

This vault is for a single trusted deployment operator. Key files belong on an
encrypted volume or secret-manager mount, outside version control and backups of
the database. No HTTP handler accepts or returns exchange credentials.
"""
import argparse
import contextlib
import fcntl
import getpass
import json
import os
import re
import secrets
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


class VaultError(RuntimeError):
    pass


SCHEMAS = {
    "delta-demo": ("demo", ("api_key", "api_secret")),
    "delta-production": ("production", ("api_key", "api_secret")),
    "telegram": ("notification", ("bot_token", "chat_id")),
}


def _key_path() -> Path:
    return Path(os.environ.get("TRADEBOT_VAULT_KEY_FILE", ".runtime/vault.key")).absolute()


def _vault_path() -> Path:
    return Path(os.environ.get("TRADEBOT_VAULT_FILE", ".runtime/credentials.vault")).absolute()


def _private_read(path: Path) -> str:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as error:
        raise VaultError("Vault file is unavailable; configure it using the local vault CLI") from error
    with os.fdopen(descriptor) as stream:
        mode = os.fstat(stream.fileno())
        if not stat.S_ISREG(mode.st_mode) or stat.S_IMODE(mode.st_mode) & 0o077:
            raise VaultError("Vault files must be regular files with owner-only permissions (0600)")
        if mode.st_size > 4 * 1024 * 1024:
            raise VaultError("Vault file exceeds its maximum size")
        return stream.read()


def _atomic_private_write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise VaultError("Vault file must not be a symlink")
    descriptor, temporary = tempfile.mkstemp(prefix=".vault-", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextlib.contextmanager
def _locked():
    path = _key_path().with_suffix(".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        if stat.S_IMODE(os.fstat(descriptor).st_mode) & 0o077:
            raise VaultError("Vault lock must have owner-only permissions")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def _read_keyring() -> dict:
    try:
        result = json.loads(_private_read(_key_path()))
        if not isinstance(result, dict) or result.get("active") not in result.get("keys", {}):
            raise ValueError
        for key in result["keys"].values():
            Fernet(key.encode())
        return result
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise VaultError("Vault key file is invalid") from error


def _encrypt(value: str, keyring: dict) -> str:
    active = keyring["active"]
    ciphertext = Fernet(keyring["keys"][active].encode()).encrypt(value.encode()).decode()
    return active + ":" + ciphertext


def _decrypt(value: str, keyring: dict) -> str:
    try:
        key_id, ciphertext = value.split(":", 1)
        return Fernet(keyring["keys"][key_id].encode()).decrypt(ciphertext.encode()).decode()
    except (KeyError, ValueError, UnicodeError, InvalidToken) as error:
        raise VaultError("Encrypted secret could not be authenticated") from error


def encrypt_secret(value: str, purpose: str = "") -> str:
    return _encrypt(json.dumps({"purpose": purpose, "value": value}), _read_keyring())


def decrypt_secret(value: str, purpose: str = "") -> str:
    try:
        data = json.loads(_decrypt(value, _read_keyring()))
        if data["purpose"] != purpose or not isinstance(data["value"], str):
            raise ValueError
        return data["value"]
    except (KeyError, ValueError, TypeError) as error:
        raise VaultError("Encrypted secret has a different purpose") from error


def initialize_vault() -> None:
    with _locked():
        if _key_path().exists():
            _read_keyring()  # Preserve and validate the existing key, never replace it.
            return
        if _vault_path().exists():
            raise VaultError("A credential vault already exists without its key; restore its original key")
        identifier = secrets.token_hex(8)
        _atomic_private_write(_key_path(), json.dumps({"active": identifier, "keys": {identifier: Fernet.generate_key().decode()}}))


def _read_entries() -> dict:
    if not _vault_path().exists():
        return {}
    try:
        data = json.loads(_private_read(_vault_path()))
        if data.get("format") != 1 or not isinstance(data.get("entries"), dict):
            raise ValueError
        return data["entries"]
    except (ValueError, TypeError, AttributeError) as error:
        raise VaultError("Credential vault is invalid") from error


def _write_entries(entries: dict) -> None:
    _atomic_private_write(_vault_path(), json.dumps({"format": 1, "entries": entries}, sort_keys=True))


def configure_credentials(name: str, values: dict[str, str]) -> None:
    if name not in SCHEMAS or set(values) != set(SCHEMAS[name][1]):
        raise VaultError("Unsupported credential name or fields")
    if any(not isinstance(value, str) or not value.strip() or len(value) > 4096 or any(ord(char) < 32 or ord(char) == 127 for char in value) for value in values.values()):
        raise VaultError("Credential values must be nonempty, contain no control characters and be at most 4096 characters")
    if name == "telegram" and not re.fullmatch(r"-?\d+", values["chat_id"]):
        raise VaultError("Telegram chat ID must be a numeric ID")
    with _locked():
        keyring = _read_keyring()
        entries = _read_entries()
        generation = entries.get(name, {}).get("generation", 0) + 1
        entries[name] = {
            "environment": SCHEMAS[name][0], "generation": generation,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "ciphertext": _encrypt(json.dumps({"credential_name": name, "values": values}), keyring),
        }
        _write_entries(entries)


def vault_credentials(name: str) -> dict[str, str] | None:
    if name not in SCHEMAS:
        raise VaultError("Unsupported credential name")
    entries = _read_entries()
    if name not in entries:
        return None
    try:
        data = json.loads(_decrypt(entries[name]["ciphertext"], _read_keyring()))
        values = data.get("values", {})
        if data.get("credential_name") != name or set(values) != set(SCHEMAS[name][1]) or any(not isinstance(value, str) for value in values.values()):
            raise ValueError
        return values
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        raise VaultError("Credential record has invalid fields") from error


def vault_status() -> dict:
    if not _key_path().exists():
        return {"configured": False, "credentials": []}
    _read_keyring()
    return {"configured": True, "credentials": [
        {"name": name, **{key: entry[key] for key in ("environment", "generation", "updated_at")}}
        for name, entry in sorted(_read_entries().items())
    ]}


def rotate_vault_key() -> None:
    """Keep old decryption keys so encrypted MFA records and interrupted writes survive."""
    with _locked():
        keyring = _read_keyring()
        entries = _read_entries()
        plain = {name: _decrypt(entry["ciphertext"], keyring) for name, entry in entries.items()}
        identifier = secrets.token_hex(8)
        keyring["keys"][identifier] = Fernet.generate_key().decode()
        keyring["active"] = identifier
        # Publish a key ring able to read both old and new data before changing ciphertext.
        _atomic_private_write(_key_path(), json.dumps(keyring))
        for name, entry in entries.items():
            entry["ciphertext"] = _encrypt(plain[name], keyring)
        _write_entries(entries)


def main() -> None:
    parser = argparse.ArgumentParser(description="Configure local encrypted credentials without exposing values in chat or shell history")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init")
    configure = subparsers.add_parser("set")
    configure.add_argument("name", choices=SCHEMAS)
    subparsers.add_parser("rotate")
    subparsers.add_parser("status")
    args = parser.parse_args()
    try:
        if args.command == "init":
            initialize_vault()
            print("Encrypted local vault initialized; existing keys preserved.")
        elif args.command == "set":
            initialize_vault()
            if not os.isatty(0):
                raise VaultError("Credential configuration requires an interactive terminal")
            values = {field: getpass.getpass(f"{field}: ") for field in SCHEMAS[args.name][1]}
            configure_credentials(args.name, values)
            print("Encrypted credential record saved. Credential values were not printed.")
        elif args.command == "rotate":
            rotate_vault_key()
            print("Vault encryption key rotated. Previous decryption keys retained for recovery and MFA records.")
        else:
            print(json.dumps(vault_status(), indent=2))
    except VaultError as error:
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()
