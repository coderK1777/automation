"""Encryption/restore/strict SFTP contracts; no offsite server is contacted."""
import importlib.util
import io
import os
from pathlib import Path

from cryptography.exceptions import InvalidTag
import pytest


def script(name):
    path = Path(__file__).resolve().parents[2]/"scripts"/name
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def encrypted(tmp_path):
    module = script("backup-offsite.py")
    key, source, destination = tmp_path/"key", tmp_path/"snapshot.dump", tmp_path/"snapshot.tbpenc"
    module.init_key(key)
    payload = b"PGDMP"+os.urandom(2*1024*1024+17)
    source.write_bytes(payload)
    metadata = module.encrypt_backup(source, destination, key)
    return module, key, source, destination, payload, metadata


def test_streamed_encryption_restores_exact_binary_dump_and_private_permissions(encrypted, tmp_path):
    module, key, _, encrypted_file, payload, metadata = encrypted
    restored = tmp_path/"restored.dump"
    result = module.decrypt_backup(encrypted_file, restored, key)
    assert result == metadata
    assert restored.read_bytes() == payload
    assert encrypted_file.read_bytes()[len(module.MAGIC):] != payload
    assert restored.stat().st_mode & 0o077 == 0
    assert encrypted_file.stat().st_mode & 0o077 == 0
    assert key.stat().st_mode & 0o077 == 0


@pytest.mark.parametrize("fault", ["ciphertext", "tag", "wrong_key"])
def test_tampering_or_wrong_key_never_publishes_a_restore(encrypted, tmp_path, fault):
    module, key, _, encrypted_file, _, _ = encrypted
    if fault == "wrong_key":
        key.write_bytes(os.urandom(32))
    else:
        payload = bytearray(encrypted_file.read_bytes())
        index = len(payload)-1 if fault == "tag" else len(payload)//2
        payload[index] ^= 1
        encrypted_file.write_bytes(payload)
    restored = tmp_path/"failed-restore.dump"
    with pytest.raises(InvalidTag):
        module.decrypt_backup(encrypted_file, restored, key)
    assert not restored.exists()
    assert not list(tmp_path.glob("failed-restore.dump.partial-*"))


def test_existing_key_backup_and_restore_files_are_preserved(encrypted, tmp_path):
    module, key, source, encrypted_file, payload, _ = encrypted
    original_key = key.read_bytes()
    with pytest.raises(FileExistsError):
        module.init_key(key)
    assert key.read_bytes() == original_key
    with pytest.raises(ValueError, match="already exists"):
        module.encrypt_backup(source, encrypted_file, key)
    restored = tmp_path/"existing.dump"
    restored.write_bytes(b"retain existing state")
    with pytest.raises(ValueError, match="already exists"):
        module.decrypt_backup(encrypted_file, restored, key)
    assert restored.read_bytes() == b"retain existing state"
    assert module.decrypt_to_stream(encrypted_file, io.BytesIO(), key)["plaintext_bytes"] == len(payload)


def test_upload_requires_explicit_destination_and_strict_host_verification(encrypted, tmp_path, monkeypatch):
    module, key, _, encrypted_file, _, _ = encrypted
    calls = []
    identity, known_hosts = tmp_path/"identity", tmp_path/"known_hosts"
    identity.write_bytes(b"offline identity fixture; never sent to SSH")
    identity.chmod(0o600)
    known_hosts.write_text("backup.example.com ssh-ed25519 offline-hostkey-fixture\n")
    class Result:
        returncode = 0
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return Result()
    monkeypatch.setattr(module.subprocess, "run", run)
    with pytest.raises(ValueError, match="requires"):
        module.upload_backup(encrypted_file, host=None, user="backup", directory="/backups",
                             identity=identity, known_hosts=known_hosts, key_path=key)
    assert calls == []
    module.upload_backup(encrypted_file, host="backup.example.com", user="backup", directory="/backups",
                         identity=identity, known_hosts=known_hosts, key_path=key)
    command, kwargs = calls[0]
    assert "-oStrictHostKeyChecking=yes" in command
    assert "-oIdentitiesOnly=yes" in command
    assert "-oBatchMode=yes" in command
    assert kwargs["input"].startswith("put ") and "\nrename " in kwargs["input"]
    assert str(key) not in kwargs["input"] and key.read_bytes().hex() not in str(command)


def test_corrupt_artifact_is_never_sent_offsite(encrypted, tmp_path, monkeypatch):
    module, key, _, encrypted_file, _, _ = encrypted
    identity, known_hosts = tmp_path/"identity", tmp_path/"known_hosts"
    identity.write_bytes(b"offline fixture")
    identity.chmod(0o600)
    known_hosts.write_text("offline fixture\n")
    payload = bytearray(encrypted_file.read_bytes())
    payload[-1] ^= 1
    encrypted_file.write_bytes(payload)
    calls = []
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: calls.append(args))
    with pytest.raises(InvalidTag):
        module.upload_backup(encrypted_file, host="backup.example.com", user="backup", directory="/backups",
                             identity=identity, known_hosts=known_hosts, key_path=key)
    assert calls == []


def test_local_backup_failure_status_retains_previous_success(tmp_path):
    module = script("backup.py")
    module.write_status(tmp_path, True, 100)
    module.write_status(tmp_path, False)
    import json
    state = json.loads((tmp_path/"status.json").read_text())
    assert state["last_attempt_success"] is False
    assert state["last_success_at"] and state["last_success_bytes"] == 100
    assert (tmp_path/"status.json").stat().st_mode & 0o077 == 0


def test_scheduled_offsite_delivery_fails_closed_without_a_destination(tmp_path, monkeypatch, capsys):
    module = script("backup-scheduled-offsite.py")
    monkeypatch.setattr(module, "root", tmp_path)
    for name in ("BACKUP_ENCRYPTION_KEY_FILE", "BACKUP_SFTP_HOST", "BACKUP_SFTP_USER", "BACKUP_SFTP_DIRECTORY",
                 "BACKUP_SFTP_IDENTITY_FILE", "BACKUP_SFTP_KNOWN_HOSTS_FILE"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(SystemExit) as error:
        module.main()
    assert error.value.code == 1
    assert "no data was transferred" in capsys.readouterr().out
    assert not (tmp_path/".runtime").exists()
