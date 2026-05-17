from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from src.modules.plugins.download_upload import (
    DownloadUpload,
    collect_downloaded_files,
    extract_gdrive_command_input,
    extract_gdrive_input,
)


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
