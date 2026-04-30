from unittest import TestCase

from src.utils import telegram


class TelegramChunkMessageTest(TestCase):
    def test_chunk_message_keeps_messages_under_limit(self) -> None:
        original_limit = telegram.MAX_MESSAGE_LENGTH
        telegram.MAX_MESSAGE_LENGTH = 24
        try:
            chunks = telegram.chunk_message('Header', ['one', 'two', 'three', 'four'])
        finally:
            telegram.MAX_MESSAGE_LENGTH = original_limit

        assert chunks == ['Header\n\none\ntwo\nthree', 'Header\n\nfour']
        assert all(len(chunk) <= 24 for chunk in chunks)

    def test_chunk_message_supports_plain_result_chunks(self) -> None:
        original_limit = telegram.MAX_MESSAGE_LENGTH
        telegram.MAX_MESSAGE_LENGTH = 13
        try:
            chunks = telegram.chunk_message('', ['first', 'second', 'third'])
        finally:
            telegram.MAX_MESSAGE_LENGTH = original_limit

        assert chunks == ['first\nsecond', 'third']
        assert all(len(chunk) <= 13 for chunk in chunks)
