from asyncio import StreamReader
from unittest import IsolatedAsyncioTestCase, TestCase

from src.utils.run import format_pre_block, read_stream


class RunHelpersTest(TestCase):
    def test_format_pre_block_counts_wrapper_in_limit(self) -> None:
        message = format_pre_block('abcdef', max_length=12)

        assert message == '<pre>a</pre>'
        assert len(message) <= 12

    def test_format_pre_block_can_keep_tail(self) -> None:
        message = format_pre_block('abcdef', max_length=12, tail=True)

        assert message == '<pre>f</pre>'
        assert len(message) <= 12

    def test_format_pre_block_escapes_html(self) -> None:
        assert format_pre_block('<tag>&') == '<pre>&lt;tag&gt;&amp;</pre>'


class ReadStreamTest(IsolatedAsyncioTestCase):
    async def test_read_stream_handles_long_output_without_separator(self) -> None:
        reader = StreamReader(limit=8)
        reader.feed_data(b'a' * 32)
        reader.feed_eof()

        chunks = [chunk async for chunk in read_stream(reader)]

        assert ''.join(chunks) == 'a' * 32
