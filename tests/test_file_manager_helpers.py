from pathlib import Path
from typing import Any, cast
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, patch

from src.modules.plugins.file_manager import (
    ALLOWED_ARCHIVE_FORMATS,
    FileManager,
    archive_compress_command,
    archive_extract_command,
    archive_list_command,
    archive_output_name,
    archive_suffixes,
    format_archive_output,
    is_archive_file,
    is_brotli_file,
    is_brotli_tar,
    normalize_archive_format,
    run_archive_step,
    strip_archive_suffix,
)
from telethon.errors import MessageNotModifiedError


class FileManagerHelpersTest(TestCase):
    def test_file_manager_buttons_use_archive_names(self) -> None:
        assert 'zip' not in FileManager.commands
        assert 'unzip' not in FileManager.commands
        assert {
            name
            for name, command in FileManager.commands.items()
            if command.is_applicable_for_reply
        } == {'archive', 'unarchive'}

    def test_archive_suffixes_are_pathlib_based_and_case_insensitive(self) -> None:
        assert archive_suffixes('SAADI-EPUBS.TAR.BR') == ['.tar', '.br']
        assert is_brotli_tar('SAADI-EPUBS.TAR.BR')
        assert is_brotli_file('book.EPUB.BR')
        assert is_archive_file('book.zip')
        assert is_archive_file('book.rar')
        assert is_archive_file('book.cbz')
        assert is_archive_file('book.cbr')
        assert is_archive_file('book.tgz')
        assert is_archive_file('book.txz')
        assert is_archive_file('book.tar.bz2')
        assert is_archive_file('book.tbz2')
        assert is_archive_file('book.epub.gz')
        assert is_archive_file('book.epub.bz2')
        assert is_archive_file('book.epub.xz')
        assert not is_archive_file('book.epub')

    def test_archive_commands_use_tar_for_brotli_tar(self) -> None:
        archive = Path('saadi epubs.tar.br')
        output_dir = Path('out dir')

        assert archive_list_command(archive, 'saadi-epubs.tar.br') == (
            "tar --warning=no-unknown-keyword --use-compress-program=brotli -tf 'saadi epubs.tar.br'"
        )
        assert archive_extract_command(archive, 'saadi-epubs.tar.br', output_dir) == (
            "tar --warning=no-unknown-keyword --use-compress-program=brotli -xf 'saadi epubs.tar.br' -C 'out dir'"
        )

    def test_archive_commands_use_tar_for_tar_archives(self) -> None:
        output_dir = Path('out')

        assert archive_list_command(Path('book.tar'), 'book.tar') == 'tar -tf book.tar'
        assert archive_extract_command(Path('book.tar'), 'book.tar', output_dir) == (
            'tar -xf book.tar -C out'
        )
        assert archive_list_command(Path('book.tgz'), 'book.tgz') == 'tar -tzf book.tgz'
        assert archive_extract_command(Path('book.tgz'), 'book.tgz', output_dir) == (
            'tar -xzf book.tgz -C out'
        )
        assert archive_list_command(Path('book.tar.gz'), 'book.tar.gz') == 'tar -tzf book.tar.gz'
        assert archive_extract_command(Path('book.tar.gz'), 'book.tar.gz', output_dir) == (
            'tar -xzf book.tar.gz -C out'
        )
        assert archive_list_command(Path('book.txz'), 'book.txz') == 'tar -tJf book.txz'
        assert archive_extract_command(Path('book.txz'), 'book.txz', output_dir) == (
            'tar -xJf book.txz -C out'
        )
        assert archive_list_command(Path('book.tar.bz2'), 'book.tar.bz2') == 'tar -tjf book.tar.bz2'
        assert archive_extract_command(Path('book.tbz2'), 'book.tbz2', output_dir) == (
            'tar -xjf book.tbz2 -C out'
        )

    def test_archive_commands_keep_7z_for_other_archives(self) -> None:
        archive = Path('book.zip')
        output_dir = Path('out')

        assert archive_list_command(archive, 'book.zip') == '7z l -ba book.zip'
        assert archive_extract_command(archive, 'book.zip', output_dir) == (
            '7z x -y -oout book.zip'
        )
        assert archive_list_command(Path('book.rar'), 'book.rar') == '7z l -ba book.rar'
        assert archive_extract_command(Path('book.rar'), 'book.rar', output_dir) == (
            '7z x -y -oout book.rar'
        )
        assert archive_extract_command(Path('book.cbz'), 'book.cbz', output_dir) == (
            '7z x -y -oout book.cbz'
        )
        assert archive_extract_command(Path('book.epub.gz'), 'book.epub.gz', output_dir) == (
            '7z x -y -oout book.epub.gz'
        )

    def test_archive_commands_use_brotli_for_plain_brotli_files(self) -> None:
        archive = Path('book.epub.br')
        output_dir = Path('out')

        assert archive_list_command(archive, 'book.epub.br') == 'brotli -t -v book.epub.br'
        assert archive_extract_command(archive, 'book.epub.br', output_dir) == (
            'brotli -d -f -o out/book.epub book.epub.br'
        )

    def test_format_archive_output_escapes_html(self) -> None:
        assert format_archive_output('failed', '<tag>') == 'failed\n<pre>&lt;tag&gt;</pre>'

    def test_archive_formats_are_normalized(self) -> None:
        assert normalize_archive_format('.tgz') == 'tar.gz'
        assert normalize_archive_format('TXZ') == 'tar.xz'
        assert 'br' in ALLOWED_ARCHIVE_FORMATS

    def test_archive_output_names_strip_archive_suffixes(self) -> None:
        assert strip_archive_suffix('saadi-epubs.tar.br') == 'saadi-epubs'
        assert strip_archive_suffix('book.rar') == 'book'
        assert strip_archive_suffix('book.tar.bz2') == 'book'
        assert strip_archive_suffix('book.tgz') == 'book'
        assert strip_archive_suffix('book.epub.gz') == 'book.epub'
        assert strip_archive_suffix('book.epub') == 'book'
        assert archive_output_name('saadi-epubs.tar.br', 'zip') == 'saadi-epubs.zip'
        assert archive_output_name('book.epub', 'br') == 'book.epub.br'
        assert archive_output_name('book.zip', 'zip') == 'book_compressed.zip'
        assert archive_output_name('book.zip', 'zip', collision_marker='converted') == (
            'book_converted.zip'
        )

    def test_archive_compress_commands_use_requested_format(self) -> None:
        assert archive_compress_command('book.epub', Path('book.zip'), 'zip') == (
            '7z a -tzip book.zip book.epub'
        )
        assert archive_compress_command('book.epub', Path('book.7z'), '7z') == (
            '7z a -t7z book.7z book.epub'
        )
        assert archive_compress_command('book.epub', Path('book.tar'), 'tar') == (
            'tar -cf book.tar book.epub'
        )
        assert archive_compress_command('book.epub', Path('book.tar.gz'), 'tar.gz') == (
            'tar -czf book.tar.gz book.epub'
        )
        assert archive_compress_command('book.epub', Path('book.tar.xz'), 'tar.xz') == (
            'tar -cJf book.tar.xz book.epub'
        )
        assert archive_compress_command('book.epub', Path('book.tar.br'), 'tar.br') == (
            'tar --warning=no-unknown-keyword --use-compress-program=brotli -cf book.tar.br book.epub'
        )
        assert archive_compress_command('book.epub', Path('book.epub.br'), 'br') == (
            'brotli -f -o book.epub.br book.epub'
        )


class FileManagerAsyncHelpersTest(IsolatedAsyncioTestCase):
    async def test_run_archive_step_ignores_duplicate_progress_text(self) -> None:
        progress_message = AsyncMock()
        progress_message.edit.side_effect = MessageNotModifiedError(request=None)

        with patch('src.modules.plugins.file_manager.run_command', AsyncMock(return_value=('', 0))):
            assert await run_archive_step(
                cast(Any, object()),
                progress_message,
                'true',
                cwd=Path(),
                error_file_name='archive_error.txt',
            )
