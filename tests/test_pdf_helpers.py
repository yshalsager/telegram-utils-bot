from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, patch

import pymupdf
from src.modules.plugins.pdf import (
    PDF,
    collect_pdf_attachments,
    collect_pdf_font_files,
    collect_pdf_fonts,
    format_pdf_info,
    parse_page_numbers,
)
from src.utils.downloads import PDF_THUMBNAIL_MAX_SIZE, prepare_pdf_thumbnail, upload_file


class PdfPageParsingTest(TestCase):
    def test_parse_page_numbers_uses_one_based_input(self) -> None:
        assert parse_page_numbers('1') == [0]
        assert parse_page_numbers('1, 3 5') == [0, 2, 4]
        assert parse_page_numbers('2-4') == [1, 2, 3]

    def test_parse_page_numbers_ignores_zero_and_invalid_ranges(self) -> None:
        assert parse_page_numbers('0') == []
        assert parse_page_numbers('0-2 4-2') == []


class PdfThumbnailTest(IsolatedAsyncioTestCase):
    def make_pdf(self, path: Path) -> None:
        with pymupdf.open() as doc:
            page = doc.new_page(width=595, height=842)
            page.insert_text((72, 72), 'PDF thumbnail test')
            doc.save(path)

    def test_prepare_pdf_thumbnail_renders_first_page_jpeg(self) -> None:
        with TemporaryDirectory() as temp_dir_name:
            pdf_file = Path(temp_dir_name) / 'book.pdf'
            thumb_file = Path(temp_dir_name) / 'book.jpg'
            self.make_pdf(pdf_file)

            assert prepare_pdf_thumbnail(pdf_file, thumb_file)
            assert 0 < thumb_file.stat().st_size <= PDF_THUMBNAIL_MAX_SIZE

    async def test_upload_file_adds_pdf_thumbnail_for_generic_uploads(self) -> None:
        with TemporaryDirectory() as temp_dir_name:
            pdf_file = Path(temp_dir_name) / 'book.pdf'
            self.make_pdf(pdf_file)
            event: Any = SimpleNamespace(chat_id=123, client=SimpleNamespace(send_file=AsyncMock()))
            progress_message: Any = SimpleNamespace()

            async def send_file(*args: Any, **kwargs: Any) -> None:
                assert Path(kwargs['thumb']).exists()

            event.client.send_file.side_effect = send_file

            with patch('src.utils.downloads.fast_upload_file', AsyncMock(return_value=object())):
                await upload_file(event, pdf_file, progress_message)

            thumb = Path(event.client.send_file.await_args.kwargs['thumb'])
            assert thumb.name.startswith('book_thumb_')
            assert thumb.suffix == '.jpg'
            assert not thumb.exists()


class PdfInfoHelpersTest(TestCase):
    def make_pdf(self, path: Path) -> None:
        with pymupdf.open() as doc:
            doc.set_metadata({'title': 'Sample PDF', 'author': 'Tester'})
            page = doc.new_page(width=300, height=400)
            page.insert_text((72, 72), 'PDF info test')
            doc.embfile_add('note.txt', b'attachment body', filename='note.txt')
            doc.save(path)

    def test_format_pdf_info_includes_document_font_and_attachment_summary(self) -> None:
        with TemporaryDirectory() as temp_dir_name:
            pdf_file = Path(temp_dir_name) / 'sample.pdf'
            self.make_pdf(pdf_file)

            with pymupdf.open(pdf_file) as doc:
                report = format_pdf_info(doc, pdf_file.name, pdf_file.stat().st_size)

            assert 'sample.pdf' in report
            assert 'Sample PDF' in report
            assert 'Tester' in report
            assert 'Helvetica' in report

    def test_collect_pdf_attachments_returns_embedded_files(self) -> None:
        with TemporaryDirectory() as temp_dir_name:
            pdf_file = Path(temp_dir_name) / 'sample.pdf'
            self.make_pdf(pdf_file)

            with pymupdf.open(pdf_file) as doc:
                attachments = collect_pdf_attachments(doc)

            assert attachments == [('note.txt', b'attachment body')]

    def test_collect_pdf_fonts_skips_base_fonts_without_embedded_data(self) -> None:
        with TemporaryDirectory() as temp_dir_name:
            pdf_file = Path(temp_dir_name) / 'sample.pdf'
            self.make_pdf(pdf_file)

            with pymupdf.open(pdf_file) as doc:
                fonts = collect_pdf_fonts(doc)
                font_files = collect_pdf_font_files(doc)

            assert fonts[0]['basefont'] == 'Helvetica'
            assert font_files == []


class PdfCommandPatternTest(TestCase):
    def test_pdf_info_attachments_and_fonts_commands_match(self) -> None:
        assert PDF.commands['pdf info'].pattern.match('/pdf info')
        assert PDF.commands['pdf attachments'].pattern.match('/pdf attachments')
        assert PDF.commands['pdf fonts'].pattern.match('/pdf fonts')
