from unittest import TestCase

from src.modules.plugins.pdf import parse_page_numbers


class PdfPageParsingTest(TestCase):
    def test_parse_page_numbers_uses_one_based_input(self) -> None:
        assert parse_page_numbers('1') == [0]
        assert parse_page_numbers('1, 3 5') == [0, 2, 4]
        assert parse_page_numbers('2-4') == [1, 2, 3]

    def test_parse_page_numbers_ignores_zero_and_invalid_ranges(self) -> None:
        assert parse_page_numbers('0') == []
        assert parse_page_numbers('0-2 4-2') == []
