import json
import os

import pytest


@pytest.fixture
def private_vault(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADEBOT_VAULT_KEY_FILE", str(tmp_path / "vault.key"))
    monkeypatch.setenv("TRADEBOT_VAULT_FILE", str(tmp_path / "credentials.vault"))
    from tradebot.vault import initialize_vault
    initialize_vault()
    return tmp_path


def test_encrypted_storage_rotation_and_metadata_redaction(private_vault):
    from tradebot.vault import configure_credentials, decrypt_secret, encrypt_secret, rotate_vault_key, vault_credentials, vault_status
    values = {"api_key": "fixture-key-never-real", "api_secret": "fixture-secret-never-real"}
    configure_credentials("delta-demo", values)
    ciphertext_before = (private_vault / "credentials.vault").read_text()
    assert all(value not in ciphertext_before for value in values.values())
    before_secret = encrypt_secret("fixture-totp-secret", "mfa:1")
    metadata = vault_status()
    assert all(value not in json.dumps(metadata) for value in values.values())
    assert metadata["credentials"][0]["generation"] == 1
    rotate_vault_key()
    assert (private_vault / "credentials.vault").read_text() != ciphertext_before
    assert vault_credentials("delta-demo") == values
    assert decrypt_secret(before_secret, "mfa:1") == "fixture-totp-secret"
    configure_credentials("delta-demo", {**values, "api_secret": "rotated-fixture-secret"})
    assert vault_status()["credentials"][0]["generation"] == 2
    assert os.stat(private_vault / "vault.key").st_mode & 0o777 == 0o600
    assert os.stat(private_vault / "credentials.vault").st_mode & 0o777 == 0o600


def test_initialize_preserves_existing_key(private_vault):
    from tradebot.vault import initialize_vault
    before = (private_vault / "vault.key").read_bytes()
    initialize_vault()
    assert (private_vault / "vault.key").read_bytes() == before


def test_authenticated_ciphertext_and_purpose_binding(private_vault):
    from tradebot.vault import VaultError, decrypt_secret, encrypt_secret
    value = encrypt_secret("fixture-secret", "mfa:1")
    with pytest.raises(VaultError, match="different purpose"):
        decrypt_secret(value, "mfa:2")
    with pytest.raises(VaultError, match="authenticated"):
        decrypt_secret(value[:-8] + "tampered", "mfa:1")


def test_reject_world_readable_key_and_symlinks(private_vault, monkeypatch):
    from tradebot.vault import VaultError, vault_status
    (private_vault / "vault.key").chmod(0o644)
    with pytest.raises(VaultError, match="owner-only"):
        vault_status()
    (private_vault / "vault.key").chmod(0o600)
    alias = private_vault / "alias.key"
    alias.symlink_to(private_vault / "vault.key")
    monkeypatch.setenv("TRADEBOT_VAULT_KEY_FILE", str(alias))
    with pytest.raises(VaultError, match="unavailable"):
        vault_status()


def test_credential_entry_cannot_be_rebound_to_another_environment(private_vault):
    from tradebot.vault import VaultError, configure_credentials, vault_credentials
    configure_credentials("delta-production", {"api_key": "fixture-prod-key", "api_secret": "fixture-prod-secret"})
    path = private_vault / "credentials.vault"
    data = json.loads(path.read_text())
    data["entries"]["delta-demo"] = data["entries"]["delta-production"]
    path.write_text(json.dumps(data))
    with pytest.raises(VaultError, match="invalid fields"):
        vault_credentials("delta-demo")
