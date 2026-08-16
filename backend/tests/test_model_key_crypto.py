import pytest
from cryptography.fernet import Fernet

from app.stores.model_key_crypto import (
    KeyDecryptionError, ModelKeyCrypto, _derive_key, mask_api_key,
)


def test_encrypt_decrypt_roundtrip():
    crypto = ModelKeyCrypto()
    token = crypto.encrypt("sk-secret-1234")
    assert token != "sk-secret-1234"
    assert crypto.decrypt(token) == "sk-secret-1234"


def test_encryption_is_deterministic_per_key():
    crypto = ModelKeyCrypto()
    token = crypto.encrypt("sk-secret")
    other = ModelKeyCrypto(key=_derive_key())
    assert other.decrypt(token) == "sk-secret"


def test_decrypt_wrong_key_raises_key_decryption_error():
    crypto = ModelKeyCrypto()
    token = crypto.encrypt("sk-secret")
    with pytest.raises(KeyDecryptionError):
        ModelKeyCrypto(key=Fernet.generate_key()).decrypt(token)


def test_decrypt_garbage_raises_key_decryption_error():
    with pytest.raises(KeyDecryptionError):
        ModelKeyCrypto().decrypt("not-a-valid-token")


def test_decrypt_non_utf8_payload_raises_key_decryption_error():
    key = _derive_key()
    token = Fernet(key).encrypt(b"\xff\xfe").decode("ascii")
    with pytest.raises(KeyDecryptionError):
        ModelKeyCrypto(key=key).decrypt(token)


def test_decrypt_non_ascii_token_raises_key_decryption_error():
    with pytest.raises(KeyDecryptionError):
        ModelKeyCrypto().decrypt("非ascii-token")


def test_mask_short_key_is_fully_hidden():
    assert mask_api_key("abc1234") == "***"
    assert mask_api_key("") == "***"


def test_mask_long_key_keeps_prefix_and_suffix():
    assert mask_api_key("sk-abcdef1234") == "sk-***1234"
