from pathlib import Path
from unittest import TestCase

from src.modules.plugins.file_manager import (
    archive_extract_command,
    archive_list_command,
    archive_suffixes,
    format_archive_output,
    is_brotli_file,
    is_brotli_tar,
)


class FileManagerHelpersTest(TestCase):
    def test_archive_suffixes_are_pathlib_based_and_case_insensitive(self) -> None:
        assert archive_suffixes('SAADI-EPUBS.TAR.BR') == ['.tar', '.br']
        assert is_brotli_tar('SAADI-EPUBS.TAR.BR')
        assert is_brotli_file('book.EPUB.BR')

    def test_archive_commands_use_tar_for_brotli_tar(self) -> None:
        archive = Path('saadi epubs.tar.br')
        output_dir = Path('out dir')

        assert archive_list_command(archive, 'saadi-epubs.tar.br') == (
            "tar --warning=no-unknown-keyword --use-compress-program=brotli -tf 'saadi epubs.tar.br'"
        )
        assert archive_extract_command(archive, 'saadi-epubs.tar.br', output_dir) == (
            "tar --warning=no-unknown-keyword --use-compress-program=brotli -xf 'saadi epubs.tar.br' -C 'out dir'"
        )

    def test_archive_commands_keep_7z_for_other_archives(self) -> None:
        archive = Path('book.zip')
        output_dir = Path('out')

        assert archive_list_command(archive, 'book.zip') == '7z l -ba book.zip'
        assert archive_extract_command(archive, 'book.zip', output_dir) == (
            '7z x -y -oout book.zip'
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
