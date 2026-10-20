"""Encryption of connection credentials at rest (Fernet / AES-128-CBC + HMAC).

Production deployments must provide ``INTELLIQ_SECRET_KEY`` (a urlsafe
base64 32-byte Fernet key, ideally injected from a KMS or secret manager).
In development mode a key file is generated in the data directory with
owner-only permissions.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


class SecretBox:
    def __init__(self, key: str | bytes):
        self._fernet = Fernet(key if isinstance(key, bytes) else key.encode())

    @classmethod
    def from_settings(cls, secret_key: str | None, data_dir: Path, dev_mode: bool) -> SecretBox:
        if secret_key:
            return cls(secret_key)
        if not dev_mode:
            raise RuntimeError("INTELLIQ_SECRET_KEY must be set outside development mode")
        key_file = data_dir / "dev-secret.key"
        if not key_file.exists():
            data_dir.mkdir(parents=True, exist_ok=True)
            fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(Fernet.generate_key())
        return cls(key_file.read_bytes().strip())

    def seal(self, secrets: dict[str, str]) -> str:
        return self._fernet.encrypt(json.dumps(secrets).encode()).decode()

    def open(self, token: str | None) -> dict[str, str]:
        if not token:
            return {}
        try:
            return json.loads(self._fernet.decrypt(token.encode()))
        except InvalidToken:
            raise RuntimeError("Stored credentials could not be decrypted with the configured key") from None
