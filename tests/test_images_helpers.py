from unittest import TestCase

from src.modules.plugins.images import image_ocr_language


class ImageOcrLanguageTest(TestCase):
    def test_defaults_to_arabic_for_button_callbacks(self) -> None:
        assert image_ocr_language() == 'ara'

    def test_parses_command_language(self) -> None:
        assert image_ocr_language('/image ocr ara+eng') == 'ara+eng'
