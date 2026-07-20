from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, patch

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


class TelegramOutputTest(IsolatedAsyncioTestCase):
    async def test_long_text_is_sent_as_file_without_editing_message(self) -> None:
        message = AsyncMock()
        progress_message = AsyncMock()
        with (
            patch.object(
                telegram, 'send_progress_message', AsyncMock(return_value=progress_message)
            ),
            patch.object(telegram, 'upload_file', AsyncMock()) as upload_file,
        ):
            edited = await telegram.edit_or_send_as_file(
                AsyncMock(), message, 'x' * (telegram.MAX_MESSAGE_LENGTH + 1)
            )

        assert not edited
        message.edit.assert_not_awaited()
        upload_file.assert_awaited_once()
