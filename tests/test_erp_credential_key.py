from pathlib import Path

from cryptography.fernet import Fernet

from domain_packs.mold.erp_adapter import encryption_key, encrypt, decrypt


def test_encryption_key_prefers_checkout_env_over_empty_process_env(monkeypatch, tmp_path):
    key = Fernet.generate_key().decode()
    env_file = tmp_path / ".env"
    env_file.write_text(f'MOLD_CREDENTIAL_ENCRYPTION_KEY="{key}"\n', encoding="utf-8")
    monkeypatch.setattr("domain_packs.mold.erp_adapter.REPO_ENV_FILE", env_file)
    monkeypatch.setenv("MOLD_CREDENTIAL_ENCRYPTION_KEY", "")
    assert encryption_key() == key


def test_encrypt_decrypt_roundtrip_uses_same_key(monkeypatch, tmp_path):
    key = Fernet.generate_key().decode()
    env_file = tmp_path / ".env"
    env_file.write_text(f"MOLD_CREDENTIAL_ENCRYPTION_KEY={key}\n", encoding="utf-8")
    monkeypatch.setattr("domain_packs.mold.erp_adapter.REPO_ENV_FILE", env_file)
    monkeypatch.delenv("MOLD_CREDENTIAL_ENCRYPTION_KEY", raising=False)
    token = "erp-session-token"
    assert decrypt(encrypt(token)) == token
