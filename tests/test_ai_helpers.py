from unittest import TestCase

from src.modules.plugins.ai import format_mistral_ocr_pages


class MistralOcrHelpersTest(TestCase):
    def test_format_mistral_ocr_pages_preserves_source_page_numbers(self) -> None:
        markdown, content = format_mistral_ocr_pages(
            [{'index': 2, 'markdown': 'Third page'}, {'index': 4, 'markdown': 'Fifth page'}]
        )

        assert content == {'3': 'Third page', '5': 'Fifth page'}
        assert markdown == '=== Page 3 ===\nThird page\n\n=== Page 5 ===\nFifth page'
