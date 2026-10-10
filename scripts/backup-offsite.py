"""Stream-encrypted backup artifacts and explicitly configured SFTP delivery.

No off-host transfer occurs during encryption, verification, ordinary startup
or local backup. Upload requires an explicit destination, a private identity
file and independently provisioned host keys. Secret values never reach argv
or output; the encryption key never accompanies the encrypted dump.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import tempfile
import uuid

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
MAGIC = b"TBPBACKUP\x01"
CHUNK = 1024*1024
MAX_HEADER = 16384


def read_key(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
        raise ValueError("Encryption key must be a private regular file with mode 0600")
    if path.stat().st_size != 32:
        raise ValueError("Encryption key file must contain exactly 32 raw random bytes")
    return path.read_bytes()


def init_key(path: Path):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write(os.urandom(32))
        output.flush()
        os.fsync(output.fileno())


def _publish(temporary: Path, destination: Path):
    # Atomic create that refuses to replace any existing backup/restore file.
    os.link(temporary, destination)
    temporary.unlink()
    os.chmod(destination, 0o600)


def encrypt_backup(source: Path, destination: Path, key_path: Path) -> dict:
    key = read_key(key_path)
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if destination.exists():
        raise ValueError("Encrypted destination already exists; choose a new filename")
    temporary = destination.with_name(destination.name+".partial-"+uuid.uuid4().hex)
    try:
        with source.open("rb") as original:
            if original.read(5) != b"PGDMP":
                raise ValueError("Backup encryption requires a PostgreSQL custom-format dump")
            original.seek(0)
            size = os.fstat(original.fileno()).st_size
            digest = hashlib.sha256()
            while data := original.read(CHUNK):
                digest.update(data)
            original.seek(0)
            nonce = os.urandom(12)
            metadata = {"format": 1, "cipher": "AES-256-GCM", "created_at": datetime.now(timezone.utc).isoformat(),
                        "plaintext_bytes": size, "plaintext_sha256": digest.hexdigest(), "nonce": nonce.hex(),
                        "content": "PostgreSQL custom-format dump"}
            header = json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode()
            prefix = MAGIC+struct.pack(">I", len(header))+header
            encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
            encryptor.authenticate_additional_data(prefix)
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                output.write(prefix)
                while data := original.read(CHUNK):
                    output.write(encryptor.update(data))
                output.write(encryptor.finalize())
                output.write(encryptor.tag)
                output.flush()
                os.fsync(output.fileno())
        _publish(temporary, destination)
        return metadata
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def decrypt_to_stream(source: Path, output, key_path: Path) -> dict:
    key = read_key(key_path)
    with source.open("rb") as encrypted:
        if encrypted.read(len(MAGIC)) != MAGIC:
            raise ValueError("Encrypted backup has an unsupported format")
        raw_length = encrypted.read(4)
        if len(raw_length) != 4:
            raise ValueError("Encrypted backup header is incomplete")
        header_size = struct.unpack(">I", raw_length)[0]
        if not 1 <= header_size <= MAX_HEADER:
            raise ValueError("Encrypted backup header exceeds the supported limit")
        header = encrypted.read(header_size)
        metadata = json.loads(header)
        if metadata.get("format") != 1 or metadata.get("cipher") != "AES-256-GCM":
            raise ValueError("Encrypted backup cipher or version is unsupported")
        nonce = bytes.fromhex(metadata["nonce"])
        if len(nonce) != 12:
            raise ValueError("Encrypted backup nonce is invalid")
        offset = len(MAGIC)+4+header_size
        file_size = os.fstat(encrypted.fileno()).st_size
        remaining = file_size-offset-16
        if remaining < 0:
            raise ValueError("Encrypted backup authentication tag is missing")
        encrypted.seek(file_size-16)
        tag = encrypted.read(16)
        encrypted.seek(offset)
        decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
        decryptor.authenticate_additional_data(MAGIC+raw_length+header)
        digest, plain_size = hashlib.sha256(), 0
        while remaining:
            data = encrypted.read(min(CHUNK, remaining))
            if not data:
                raise ValueError("Encrypted backup was truncated")
            remaining -= len(data)
            plain = decryptor.update(data)
            output.write(plain)
            digest.update(plain)
            plain_size += len(plain)
        final = decryptor.finalize()
        output.write(final)
        digest.update(final)
        plain_size += len(final)
        if plain_size != metadata["plaintext_bytes"] or digest.hexdigest() != metadata["plaintext_sha256"]:
            raise ValueError("Authenticated backup does not match its content manifest")
        return metadata


def decrypt_backup(source: Path, destination: Path, key_path: Path) -> dict:
    if destination.exists():
        raise ValueError("Restore destination already exists; existing data will not be replaced")
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name+".partial-"+uuid.uuid4().hex)
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            metadata = decrypt_to_stream(source, output, key_path)
            output.flush()
            os.fsync(output.fileno())
        _publish(temporary, destination)
        return metadata
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _sftp_quote(value: str) -> str:
    if "\n" in value or "\r" in value:
        raise ValueError("SFTP paths cannot contain newlines")
    return '"'+value.replace("\\", "\\\\").replace('"', '\\"')+'"'


class _DiscardPlaintext:
    def write(self, value):
        return len(value)


def upload_backup(source: Path, *, host: str, user: str, directory: str, identity: Path, known_hosts: Path,
                  key_path: Path, port=22):
    if not host or not user or not directory or not identity or not known_hosts:
        raise ValueError("Offsite destination requires host, user, directory, identity file and verified host-keys file")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}", host) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,63}", user):
        raise ValueError("Invalid SFTP host or user")
    if not re.fullmatch(r"/[A-Za-z0-9/_.-]{1,500}", directory) or ".." in directory.split("/") or not 1 <= port <= 65535:
        raise ValueError("Invalid SFTP backup directory or port")
    if not identity.is_file() or identity.is_symlink() or identity.stat().st_mode & 0o077 or not known_hosts.is_file():
        raise ValueError("SFTP requires a private identity file and independently verified host keys")
    if not source.is_file():
        raise ValueError("Only encrypted TradeBot backup artifacts can be uploaded")
    # Authenticate the artifact before sending it. Decrypted chunks are
    # discarded locally and the encryption key is never part of the transfer.
    decrypt_to_stream(source, _DiscardPlaintext(), key_path)
    remote = directory.rstrip("/")+"/"+source.name
    partial = remote+".partial-"+uuid.uuid4().hex
    batch = "put "+_sftp_quote(str(source.resolve()))+" "+_sftp_quote(partial)+"\nrename "+_sftp_quote(partial)+" "+_sftp_quote(remote)+"\n"
    result = subprocess.run(["sftp", "-q", "-b", "-", "-oBatchMode=yes", "-oStrictHostKeyChecking=yes",
            "-oUserKnownHostsFile="+str(known_hosts.resolve()), "-oIdentitiesOnly=yes", "-i", str(identity.resolve()),
            "-oConnectTimeout=15", "-oServerAliveInterval=30", "-oServerAliveCountMax=2",
            "-P", str(port), user+"@"+host], input=batch, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        raise RuntimeError("Encrypted SFTP delivery was not acknowledged; inspect destination with the operator")


def write_offsite_status(success: bool, size: int):
    directory = ROOT/".runtime"/"backups"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = directory/"offsite-status.json"
    previous = {}
    if path.exists():
        try:
            previous = json.loads(path.read_text())
        except (OSError, ValueError):
            pass
    now = datetime.now(timezone.utc).isoformat()
    payload = {"last_attempt_at": now, "last_attempt_success": success,
               "last_success_at": now if success else previous.get("last_success_at"),
               "last_success_bytes": size if success else previous.get("last_success_bytes", 0),
               "scope": "SFTP acknowledged encrypted artifact delivery; restoration is separate evidence"}
    partial = path.with_name("offsite-status.json.partial")
    descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as output:
        json.dump(payload, output, indent=2)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    partial.replace(path)
    try:
        from tradebot.telemetry import record_offsite_backup
        record_offsite_backup(success, size)
    except Exception:
        print("Database offsite telemetry unavailable; the private local status record was retained.")


def main():
    config = {**dotenv_values(ROOT/".env"), **os.environ}
    if config.get("DATABASE_URL"):
        os.environ["DATABASE_URL"] = config["DATABASE_URL"]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("init-key", "encrypt", "verify", "decrypt", "upload"))
    parser.add_argument("--key-file", type=Path, default=config.get("BACKUP_ENCRYPTION_KEY_FILE"))
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--host", default=config.get("BACKUP_SFTP_HOST"))
    parser.add_argument("--user", default=config.get("BACKUP_SFTP_USER"))
    parser.add_argument("--directory", default=config.get("BACKUP_SFTP_DIRECTORY"))
    parser.add_argument("--identity-file", type=Path, default=config.get("BACKUP_SFTP_IDENTITY_FILE"))
    parser.add_argument("--known-hosts-file", type=Path, default=config.get("BACKUP_SFTP_KNOWN_HOSTS_FILE"))
    parser.add_argument("--port", type=int, default=int(config.get("BACKUP_SFTP_PORT", "22")))
    args = parser.parse_args()
    try:
        if not args.key_file:
            raise ValueError("Configure BACKUP_ENCRYPTION_KEY_FILE or --key-file; key material is never accepted in arguments")
        if args.action == "init-key":
            init_key(args.key_file)
            print("Created private encryption key file; retain a separately protected recovery copy.")
        elif args.action == "encrypt":
            source = args.input or ROOT/".runtime"/"backups"/"snapshot.dump"
            destination = args.output or ROOT/".runtime"/"backups"/("snapshot-"+datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")+"-"+uuid.uuid4().hex[:12]+".tbpenc")
            encrypt_backup(source, destination, args.key_file)
            print("Created authenticated encrypted backup artifact at "+str(destination))
        elif args.action == "verify":
            if not args.input:
                raise ValueError("Specify an encrypted input artifact")
            with tempfile.TemporaryFile() as output:
                decrypt_to_stream(args.input, output, args.key_file)
            print("Verified backup authentication and plaintext content hash; no restore was performed.")
        elif args.action == "decrypt":
            if not args.input or not args.output:
                raise ValueError("Specify encrypted input and a new private restore destination")
            decrypt_backup(args.input, args.output, args.key_file)
            print("Verified and decrypted backup into the requested new restore file.")
        else:
            if not args.input:
                raise ValueError("Specify an encrypted artifact for offsite delivery")
            try:
                upload_backup(args.input, host=args.host, user=args.user, directory=args.directory,
                              identity=args.identity_file, known_hosts=args.known_hosts_file,
                              key_path=args.key_file, port=args.port)
            except Exception:
                write_offsite_status(False, 0)
                raise
            write_offsite_status(True, args.input.stat().st_size)
            print("Remote SFTP server acknowledged the encrypted artifact; verify restoration separately.")
    except Exception as error:
        # Only a safe predefined ValueError message or class name is output.
        # Transport exception messages can expose destination/identity values.
        if isinstance(error, ValueError):
            print(str(error))
        else:
            print("Backup operation failed ("+type(error).__name__+"); existing backups were preserved.")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
