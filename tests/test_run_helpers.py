from unittest import TestCase

from src.utils.run import format_pre_block


class RunHelpersTest(TestCase):
    def test_format_pre_block_counts_wrapper_in_limit(self) -> None:
        message = format_pre_block('abcdef', max_length=12)

        assert message == '<pre>a</pre>'
        assert len(message) <= 12

    def test_format_pre_block_can_keep_tail(self) -> None:
        message = format_pre_block('abcdef', max_length=12, tail=True)

        assert message == '<pre>f</pre>'
        assert len(message) <= 12
