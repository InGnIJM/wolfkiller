from __future__ import annotations

import base64
import hashlib
import platform
import uuid

from cryptography.fernet import Fernet, InvalidToken


def _derive_key() -> bytes:
    """Stable per-machine key from node id + hostname (no separate key file)."""
    material = f"{uuid.getnode()}:{platform.node()}".encode("utf-8")
    return base64.urlsafe_b64encode(hashlib.sha256(material).digest())


class KeyDecryptionError(ValueError):
    """Raised when a stored key cannot be decrypted (e.g. different machine)."""


class ModelKeyCrypto:
    def __init__(self, key: bytes | None = None):
        self._fernet = Fernet(key if key is not None else _derive_key())

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, token: str) -> str:
        try:
            return self._fernet.decrypt(token.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError, UnicodeDecodeError) as error:
            raise KeyDecryptionError("stored api key cannot be decrypted") from error


def mask_api_key(plaintext: str) -> str:
    """Return a masked display form that never reveals the full key."""
    if len(plaintext) <= 8:
        return "***"
    return f"{plaintext[:3]}***{plaintext[-4:]}"
