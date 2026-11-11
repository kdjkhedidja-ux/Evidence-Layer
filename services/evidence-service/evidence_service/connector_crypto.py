"""Application-level encryption for connector tokens.

The service stores only AES-GCM ciphertext. The key is supplied by the service
environment and never written to the database or logs.
"""

from __future__ import annotations

import base64
import os
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

KEY_ENV = "CONNECTOR_TOKEN_ENCRYPTION_KEY"
PREFIX = "v1."


class TokenVaultUnavailable(Exception):
    """The token encryption key is missing or invalid."""


class TokenVault:
    def __init__(self, key: bytes) -> None:
        if len(key) != 32:
            raise TokenVaultUnavailable
        self._aead = AESGCM(key)

    @classmethod
    def from_environment(cls) -> "TokenVault":
        raw = os.environ.get(KEY_ENV, "")
        if not raw:
            raise TokenVaultUnavailable
        try:
            key = base64.b64decode(raw, validate=True)
        except Exception as exc:
            raise TokenVaultUnavailable from exc
        return cls(key)

    @classmethod
    def from_base64(cls, value: str) -> "TokenVault":
        try:
            key = base64.b64decode(value, validate=True)
        except Exception as exc:
            raise TokenVaultUnavailable from exc
        return cls(key)

    def encrypt(self, value: str | None) -> str | None:
        if value is None:
            return None
        nonce = secrets.token_bytes(12)
        ciphertext = self._aead.encrypt(nonce, value.encode("utf-8"), None)
        return PREFIX + base64.b64encode(nonce + ciphertext).decode("ascii")

    def decrypt(self, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.startswith(PREFIX):
            raise TokenVaultUnavailable
        try:
            payload = base64.b64decode(value[len(PREFIX) :], validate=True)
        except Exception as exc:
            raise TokenVaultUnavailable from exc
        if len(payload) <= 12:
            raise TokenVaultUnavailable
        try:
            plaintext = self._aead.decrypt(payload[:12], payload[12:], None)
        except Exception as exc:
            raise TokenVaultUnavailable from exc
        return plaintext.decode("utf-8")
