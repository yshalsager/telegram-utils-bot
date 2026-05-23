from os import getenv

from cryptography.fernet import Fernet


def has_state_encryption_key() -> bool:
    return bool(getenv('STATE_ENCRYPTION_KEY'))


def get_state_encryptor() -> Fernet:
    key = getenv('STATE_ENCRYPTION_KEY')
    if not key:
        raise RuntimeError('STATE_ENCRYPTION_KEY is required')
    return Fernet(key.encode())


def encrypt_state_secret(secret: str) -> str:
    if not secret:
        return ''
    return get_state_encryptor().encrypt(secret.encode()).decode()


def decrypt_state_secret(encrypted_secret: str) -> str:
    if not encrypted_secret:
        return ''
    return get_state_encryptor().decrypt(encrypted_secret.encode()).decode()
