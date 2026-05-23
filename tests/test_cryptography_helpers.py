from unittest import TestCase
from unittest.mock import patch

from cryptography.fernet import Fernet
from src.utils.cryptography import (
    decrypt_state_secret,
    encrypt_state_secret,
    has_state_encryption_key,
)


class CryptographyHelpersTest(TestCase):
    def test_state_secret_round_trip(self) -> None:
        with patch.dict('os.environ', {'STATE_ENCRYPTION_KEY': Fernet.generate_key().decode()}):
            assert has_state_encryption_key()
            encrypted = encrypt_state_secret('secret-token')

            assert encrypted != 'secret-token'
            assert decrypt_state_secret(encrypted) == 'secret-token'

    def test_state_secret_requires_key(self) -> None:
        with patch.dict('os.environ', {'STATE_ENCRYPTION_KEY': ''}):
            assert not has_state_encryption_key()
            try:
                encrypt_state_secret('secret-token')
            except RuntimeError:
                pass
            else:
                msg = 'STATE_ENCRYPTION_KEY should be required'
                raise AssertionError(msg)
