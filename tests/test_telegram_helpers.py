from unittest import TestCase

from src.utils import telegram


class TelegramChunkMessageTest(TestCase):
    def test_chunk_message_keeps_messages_under_limit(self) -> None:
        chunks = telegram.chunk_message('Header', ['one', 'two', 'three', 'four'], max_length=24)

        assert chunks == ['Header\n\none\ntwo\nthree', 'Header\n\nfour']
        assert all(len(chunk) <= 24 for chunk in chunks)

    def test_chunk_message_supports_plain_result_chunks(self) -> None:
        chunks = telegram.chunk_message('', ['first', 'second', 'third'], max_length=13)

        assert chunks == ['first\nsecond', 'third']
        assert all(len(chunk) <= 13 for chunk in chunks)
