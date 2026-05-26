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
    remaining_pdf_pages,
    save_repaired_pdf,
    save_reversed_pdf,
    save_sanitized_pdf,
    save_selected_pdf_pages,
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


class PdfEditHelpersTest(TestCase):
    def make_pdf(self, path: Path) -> None:
        with pymupdf.open() as doc:
            doc.set_metadata({'title': 'Secret title', 'author': 'Tester'})
            for page_number in range(1, 4):
                page = doc.new_page(width=300, height=400)
                page.insert_text((72, 72), f'page {page_number}')
            doc.embfile_add('note.txt', b'attachment body', filename='note.txt')
            doc.save(path)

    def page_texts(self, path: Path) -> list[str]:
        with pymupdf.open(path) as doc:
            return [page.get_text().strip() for page in doc]

    def test_remaining_pdf_pages_uses_zero_based_delete_list(self) -> None:
        assert remaining_pdf_pages(4, [0, 2, 8]) == [1, 3]

    def test_save_selected_pdf_pages_deletes_requested_pages(self) -> None:
        with TemporaryDirectory() as temp_dir_name:
            input_file = Path(temp_dir_name) / 'sample.pdf'
            output_file = Path(temp_dir_name) / 'deleted.pdf'
            self.make_pdf(input_file)

            save_selected_pdf_pages(input_file, output_file, remaining_pdf_pages(3, [1]))

            assert self.page_texts(output_file) == ['page 1', 'page 3']

    def test_save_reversed_pdf_reverses_page_order(self) -> None:
        with TemporaryDirectory() as temp_dir_name:
            input_file = Path(temp_dir_name) / 'sample.pdf'
            output_file = Path(temp_dir_name) / 'reversed.pdf'
            self.make_pdf(input_file)

            save_reversed_pdf(input_file, output_file)

            assert self.page_texts(output_file) == ['page 3', 'page 2', 'page 1']

    def test_save_sanitized_pdf_removes_metadata_and_attachments(self) -> None:
        with TemporaryDirectory() as temp_dir_name:
            input_file = Path(temp_dir_name) / 'sample.pdf'
            output_file = Path(temp_dir_name) / 'sanitized.pdf'
            self.make_pdf(input_file)

            save_sanitized_pdf(input_file, output_file)

            with pymupdf.open(output_file) as doc:
                assert doc.metadata.get('title') in (None, '')
                assert doc.metadata.get('author') in (None, '')
                assert doc.embfile_count() == 0

    def test_save_repaired_pdf_writes_openable_pdf(self) -> None:
        with TemporaryDirectory() as temp_dir_name:
            input_file = Path(temp_dir_name) / 'sample.pdf'
            output_file = Path(temp_dir_name) / 'repaired.pdf'
            self.make_pdf(input_file)

            save_repaired_pdf(input_file, output_file)

            with pymupdf.open(output_file) as doc:
                assert doc.page_count == 3


class PdfCommandPatternTest(TestCase):
    def test_pdf_info_attachments_and_fonts_commands_match(self) -> None:
        assert PDF.commands['pdf info'].pattern.match('/pdf info')
        assert PDF.commands['pdf attachments'].pattern.match('/pdf attachments')
        assert PDF.commands['pdf fonts'].pattern.match('/pdf fonts')

    def test_pdf_edit_commands_match(self) -> None:
        assert PDF.commands['pdf delete'].pattern.match('/pdf delete 1,3-5')
        assert PDF.commands['pdf reverse'].pattern.match('/pdf reverse')
        assert PDF.commands['pdf sanitize'].pattern.match('/pdf sanitize')
        assert PDF.commands['pdf repair'].pattern.match('/pdf repair')
        assert PDF.commands['pdf linearize'].pattern.match('/pdf linearize')
