from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from src.modules.plugins.download_upload import (
    DownloadUpload,
    extract_gdrive_command_input,
)
from src.utils.archive_org import (
    ArchiveFile,
    extract_archive_input,
    select_archive_files,
)
from src.utils.google_drive import collect_downloaded_files, extract_gdrive_input


class GDriveInputTest(TestCase):
    def test_extract_gdrive_input_accepts_upstream_drive_url_shapes(self) -> None:
        cases = [
            'https://drive.google.com/open?id=abc_123-DEF456',
            'https://drive.google.com/file/d/abc_123-DEF456/view?usp=sharing',
            'https://drive.google.com/drive/folders/abc_123-DEF456?usp=sharing',
            'https://drive.google.com/drive/u/0/folders/abc_123-DEF456',
            'https://docs.google.com/document/d/abc_123-DEF456/edit',
        ]

        for value in cases:
            with self.subTest(value=value):
                assert extract_gdrive_input(f'please download {value}') == value

    def test_extract_gdrive_input_accepts_raw_ids_and_rejects_other_urls(self) -> None:
        assert extract_gdrive_input('abc_123-DEF456') == 'abc_123-DEF456'
        assert extract_gdrive_input('https://example.com/file') is None


class GDriveDownloadHelpersTest(TestCase):
    def test_collect_downloaded_files_returns_files_recursively(self) -> None:
        with TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            (temp_dir / 'folder').mkdir()
            file_a = temp_dir / 'a.txt'
            file_b = temp_dir / 'folder' / 'b.txt'
            file_a.write_text('a')
            file_b.write_text('b')

            assert collect_downloaded_files(temp_dir) == [file_a, file_b]

    def test_gdrive_command_pattern_accepts_direct_and_reply_forms(self) -> None:
        pattern = DownloadUpload.commands['gdrive'].pattern

        assert pattern.match('/gdrive')
        assert pattern.match('/gdrive https://drive.google.com/open?id=abc_123-DEF456')

    def test_extract_gdrive_command_input_allows_empty_callback_reply_flow(self) -> None:
        assert extract_gdrive_command_input('/gdrive') == ''
        assert (
            extract_gdrive_command_input('/gdrive https://drive.google.com/open?id=abc_123-DEF456')
            == 'https://drive.google.com/open?id=abc_123-DEF456'
        )


class ArchiveInputTest(TestCase):
    def test_extract_archive_input_accepts_item_and_download_urls(self) -> None:
        item_input = extract_archive_input('https://archive.org/details/example_item')
        download_input = extract_archive_input(
            'https://archive.org/download/example_item/folder/book.pdf'
        )

        assert item_input is not None
        assert item_input.identifier == 'example_item'
        assert item_input.selected_path == ''
        assert download_input is not None
        assert download_input.identifier == 'example_item'
        assert download_input.selected_path == 'folder/book.pdf'

    def test_extract_archive_input_decodes_selected_path(self) -> None:
        archive_input = extract_archive_input(
            'https://archive.org/details/example_item/sample%20book/'
        )

        assert archive_input is not None
        assert archive_input.identifier == 'example_item'
        assert archive_input.selected_path == 'sample book'

    def test_extract_archive_input_accepts_raw_identifier_and_rejects_other_urls(self) -> None:
        archive_input = extract_archive_input('example_item-123')

        assert archive_input is not None
        assert archive_input.identifier == 'example_item-123'
        assert extract_archive_input('https://example.com/item') is None


class ArchiveDownloadHelpersTest(TestCase):
    def test_archive_command_is_not_registered(self) -> None:
        assert 'archive' not in DownloadUpload.commands

    def test_select_archive_files_uses_original_non_metadata_files_by_default(self) -> None:
        files = [
            ArchiveFile('item_meta.xml', 'original'),
            ArchiveFile('__ia_thumb.jpg', 'original'),
            ArchiveFile('book.pdf', 'original'),
            ArchiveFile('book_djvu.txt', 'derivative'),
        ]

        assert select_archive_files(files) == [ArchiveFile('book.pdf', 'original')]

    def test_select_archive_files_exact_match_includes_direct_derivative_selection(self) -> None:
        files = [
            ArchiveFile('book.pdf', 'original'),
            ArchiveFile('book_djvu.txt', 'derivative'),
        ]

        assert select_archive_files(files, 'book_djvu.txt') == [
            ArchiveFile('book_djvu.txt', 'derivative')
        ]

    def test_select_archive_files_prefix_prefers_original_matches(self) -> None:
        files = [
            ArchiveFile('sample-book.pdf', 'original'),
            ArchiveFile('sample-book_djvu.txt', 'derivative'),
            ArchiveFile('other.pdf', 'original'),
        ]

        assert select_archive_files(files, 'sample-book') == [
            ArchiveFile('sample-book.pdf', 'original')
        ]
