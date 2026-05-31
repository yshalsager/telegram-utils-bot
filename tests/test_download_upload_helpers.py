import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any
from unittest import TestCase
from unittest.mock import AsyncMock, patch

from src.modules.plugins.download_upload import (
    DownloadUpload,
    build_download_upload_params,
    collect_upload_paths,
    extract_gdrive_command_input,
    has_gdrive_download_input,
    upload_file_command,
)
from src.utils.archive_org import (
    ArchiveFile,
    extract_archive_input,
    select_archive_files,
)
from src.utils.filters import BOT_ADMINS
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
    def setUp(self) -> None:
        self.bot_admins = list(BOT_ADMINS)
        BOT_ADMINS[:] = [123]

    def tearDown(self) -> None:
        BOT_ADMINS[:] = self.bot_admins

    def make_event(self, text: str) -> Any:
        return SimpleNamespace(
            is_private=True,
            sender_id=123,
            message=SimpleNamespace(raw_text=text, file=None, is_reply=False),
        )

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

    def test_gdrive_button_condition_only_accepts_drive_urls(self) -> None:
        assert has_gdrive_download_input(
            self.make_event('https://drive.google.com/open?id=abc_123-DEF456'), None
        )
        assert not has_gdrive_download_input(self.make_event('https://example.com/file.pdf'), None)

    def test_gdrive_button_condition_still_allows_explicit_command_validation(self) -> None:
        assert has_gdrive_download_input(self.make_event('/gdrive'), None)


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


class DownloadUploadParamsTest(TestCase):
    def test_collect_upload_paths_returns_sorted_file_matches(self) -> None:
        with TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            (temp_dir / 'downloads' / 'saadi' / 'epubs').mkdir(parents=True)
            (temp_dir / 'downloads' / 'saadi' / 'epubs' / '002.epub').write_text('2')
            (temp_dir / 'downloads' / 'saadi' / 'epubs' / '001.epub').write_text('1')
            (temp_dir / 'downloads' / 'saadi' / 'epubs' / 'nested').mkdir()

            paths = collect_upload_paths('downloads/saadi/epubs/*.epub', temp_dir)

            assert [path.name for path in paths] == ['001.epub', '002.epub']

    def test_upload_file_command_uploads_all_matches(self) -> None:
        async def run_test() -> None:
            event: Any = SimpleNamespace(
                message=SimpleNamespace(text='/upload downloads/saadi/epubs/*.epub'),
                reply=AsyncMock(),
            )
            progress_message = SimpleNamespace(edit=AsyncMock())
            event.reply.return_value = progress_message
            paths = [Path('001.epub'), Path('002.epub')]

            with (
                patch(
                    'src.modules.plugins.download_upload.collect_upload_paths', return_value=paths
                ),
                patch(
                    'src.modules.plugins.download_upload.upload_file', AsyncMock()
                ) as upload_file,
            ):
                await upload_file_command(event)

            assert [call.args[1] for call in upload_file.await_args_list] == paths
            assert progress_message.edit.await_args_list[0].args[0].endswith('1/2')
            assert progress_message.edit.await_args.args[0].endswith('<code>2</code>')

        asyncio.run(run_test())

    def test_download_upload_params_reuse_media_upload_params_for_media_files(self) -> None:
        async def run_test() -> None:
            with patch(
                'src.modules.plugins.download_upload.build_media_upload_params',
                AsyncMock(return_value={'thumb': 'cover.jpg'}),
            ) as build_media_params:
                params = await build_download_upload_params(Path('song.mp3'))

            assert params == {'thumb': 'cover.jpg'}
            build_media_params.assert_awaited_once_with(Path('song.mp3'))

        asyncio.run(run_test())

    def test_download_upload_params_skip_non_media_files(self) -> None:
        async def run_test() -> None:
            with patch(
                'src.modules.plugins.download_upload.build_media_upload_params',
                AsyncMock(return_value={'thumb': 'cover.jpg'}),
            ) as build_media_params:
                params = await build_download_upload_params(Path('book.pdf'))

            assert params == {}
            build_media_params.assert_not_awaited()

        asyncio.run(run_test())
