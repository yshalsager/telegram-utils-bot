import shlex
import shutil
import time
from contextlib import suppress
from html import escape as html_escape
from pathlib import Path
from typing import ClassVar

import regex as re
from telethon.errors import MessageNotModifiedError
from telethon.events import CallbackQuery, NewMessage
from telethon.tl.custom import Message

from src import TMP_DIR
from src.modules.base import ModuleBase
from src.utils.command import Command
from src.utils.downloads import download_to_temp_file, get_download_name, upload_file_and_cleanup
from src.utils.filters import has_file, is_admin_in_private
from src.utils.i18n import t
from src.utils.run import run_command
from src.utils.telegram import (
    edit_or_send_as_file,
    get_reply_message,
    inline_choice_grid,
    send_progress_message,
)

ALLOWED_ARCHIVE_FORMATS = ('zip', '7z', 'tar', 'tar.gz', 'tar.xz', 'tar.br', 'br')
ARCHIVE_FORMAT_ALIASES = (
    {target_format: target_format for target_format in ALLOWED_ARCHIVE_FORMATS}
    | {f'.{target_format}': target_format for target_format in ALLOWED_ARCHIVE_FORMATS}
    | {'tgz': 'tar.gz', '.tgz': 'tar.gz', 'txz': 'tar.xz', '.txz': 'tar.xz'}
)
ARCHIVE_SUFFIXES = [
    ['.tar', '.br'],
    ['.tar', '.gz'],
    ['.tar', '.xz'],
    ['.tar', '.bz2'],
    ['.tgz'],
    ['.txz'],
    ['.tbz2'],
    ['.tbz'],
    ['.zip'],
    ['.7z'],
    ['.rar'],
    ['.cbz'],
    ['.cbr'],
    ['.tar'],
    ['.gz'],
    ['.bz2'],
    ['.xz'],
    ['.br'],
]
SINGLE_FILE_COMPRESSION_SUFFIXES = {'.gz', '.bz2', '.xz'}
SINGLE_FILE_COMPRESSION_MIME_TYPES = {
    'application/gzip',
    'application/x-gzip',
    'application/x-bzip2',
    'application/x-xz',
}
TAR_ARCHIVE_SUFFIX_FLAGS = {
    ('.tar', '.gz'): 'z',
    ('.tgz',): 'z',
    ('.tar', '.xz'): 'J',
    ('.txz',): 'J',
    ('.tar', '.bz2'): 'j',
    ('.tbz2',): 'j',
    ('.tbz',): 'j',
    ('.tar',): '',
}


def archive_suffixes(file_name: str) -> list[str]:
    return [suffix.lower() for suffix in Path(file_name).suffixes]


def is_brotli_tar(file_name: str) -> bool:
    return archive_suffixes(file_name)[-2:] == ['.tar', '.br']


def is_brotli_file(file_name: str) -> bool:
    return archive_suffixes(file_name)[-1:] == ['.br']


def is_archive_file(file_name: str) -> bool:
    suffixes = archive_suffixes(file_name)
    return any(suffixes[-len(suffix_group) :] == suffix_group for suffix_group in ARCHIVE_SUFFIXES)


def single_file_compression_output_name(file_name: str, mime_type: str = '') -> str:
    if Path(file_name).suffix.lower() in SINGLE_FILE_COMPRESSION_SUFFIXES:
        return Path(file_name).with_suffix('').name
    return Path(file_name).name if mime_type in SINGLE_FILE_COMPRESSION_MIME_TYPES else ''


def rename_single_file_compression_output(
    files: list[Path], file_name: str, output_dir: Path, mime_type: str = ''
) -> list[Path]:
    output_name = single_file_compression_output_name(file_name, mime_type)
    if not output_name or len(files) != 1:
        return files
    output_path = output_dir / output_name
    if files[0] != output_path:
        output_path.unlink(missing_ok=True)
        files[0].rename(output_path)
    return [output_path]


def tar_archive_flag(file_name: str) -> str | None:
    suffixes = archive_suffixes(file_name)
    for suffix_group, flag in TAR_ARCHIVE_SUFFIX_FLAGS.items():
        if suffixes[-len(suffix_group) :] == list(suffix_group):
            return flag
    return None


def format_archive_output(status: str, output: str) -> str:
    return f'{status}\n<pre>{html_escape(output or t("empty_output"))}</pre>'


def normalize_archive_format(target_format: str) -> str:
    return ARCHIVE_FORMAT_ALIASES[target_format.strip().lower()]


def archive_output_name(
    input_name: str, target_format: str, *, collision_marker: str = 'compressed'
) -> str:
    target_format = normalize_archive_format(target_format)
    input_name = Path(input_name).name
    stem = input_name if target_format == 'br' else strip_archive_suffix(input_name)
    suffix = f'.{target_format}'
    output_name = f'{stem}{suffix}'
    return f'{stem}_{collision_marker}{suffix}' if output_name == input_name else output_name


async def select_archive_format(event: NewMessage.Event | CallbackQuery.Event) -> str | None:
    prefix = 'm|archive|'
    if isinstance(event, CallbackQuery.Event):
        return await inline_choice_grid(
            event,
            prefix=prefix,
            prompt_text=f'{t("choose_target_format")}:',
            pairs=[
                (target_format, f'{prefix}{target_format}')
                for target_format in ALLOWED_ARCHIVE_FORMATS
            ],
            cols=3,
            cast=str,
        )
    target_format = event.message.raw_text.rsplit(maxsplit=1)[-1]
    try:
        normalized_format = normalize_archive_format(target_format)
    except KeyError:
        await event.reply(
            f'{t("unsupported_archive_format")}\n{t("allowed_formats")}: <code>{", ".join(ALLOWED_ARCHIVE_FORMATS)}</code>',
            parse_mode='html',
        )
        return None
    return normalized_format


def strip_archive_suffix(file_name: str) -> str:
    name = Path(file_name).name
    suffixes = archive_suffixes(name)
    for suffix_group in ARCHIVE_SUFFIXES:
        if suffixes[-len(suffix_group) :] == suffix_group:
            suffix_length = sum(len(suffix) for suffix in Path(name).suffixes[-len(suffix_group) :])
            return name[:-suffix_length]
    return Path(name).stem


def archive_compress_command(
    source_item: str,
    output_path: Path,
    target_format: str,
) -> str:
    target_format = normalize_archive_format(target_format)
    command = {
        'zip': '7z a -tzip {output} {source}',
        '7z': '7z a -t7z {output} {source}',
        'tar': 'tar -cf {output} {source}',
        'tar.gz': 'tar -czf {output} {source}',
        'tar.xz': 'tar -cJf {output} {source}',
        'tar.br': 'tar --warning=no-unknown-keyword --use-compress-program=brotli -cf {output} {source}',
        'br': 'brotli -f -o {output} {source}',
    }[target_format]
    return command.format(output=shlex.quote(str(output_path)), source=shlex.quote(source_item))


def archive_list_command(archive_path: Path, file_name: str) -> str:
    quoted_path = shlex.quote(str(archive_path))
    if is_brotli_tar(file_name):
        return f'tar --warning=no-unknown-keyword --use-compress-program=brotli -tf {quoted_path}'
    if (flag := tar_archive_flag(file_name)) is not None:
        return f'tar -t{flag}f {quoted_path}'
    if is_brotli_file(file_name):
        return f'brotli -t -v {quoted_path}'
    return f'7z l -ba {quoted_path}'


def archive_extract_command(
    archive_path: Path, file_name: str, output_dir: Path, mime_type: str = ''
) -> str:
    quoted_path = shlex.quote(str(archive_path))
    quoted_output_dir = shlex.quote(str(output_dir))
    if is_brotli_tar(file_name):
        return f'tar --warning=no-unknown-keyword --use-compress-program=brotli -xf {quoted_path} -C {quoted_output_dir}'
    if (flag := tar_archive_flag(file_name)) is not None:
        return f'tar -x{flag}f {quoted_path} -C {quoted_output_dir}'
    if is_brotli_file(file_name):
        output_path = output_dir / Path(file_name).with_suffix('').name
        return f'brotli -d -f -o {shlex.quote(str(output_path))} {quoted_path}'
    return f'7z x -y -o{quoted_output_dir} {quoted_path}'


async def run_archive_step(
    event: NewMessage.Event | CallbackQuery.Event,
    progress_message: Message,
    command: str,
    *,
    cwd: Path,
    error_file_name: str,
) -> bool:
    with suppress(MessageNotModifiedError):
        await progress_message.edit(t('starting_process'))
    output, code = await run_command(command, timeout=60 * 60, cwd=cwd)
    if code == 0:
        return True
    status = t('process_failed_with_return_code', code=code)
    await edit_or_send_as_file(
        event,
        progress_message,
        format_archive_output(status, output),
        file_name=error_file_name,
        file_text=f'{status}\n{output or t("empty_output")}',
        parse_mode='html',
    )
    return False


async def get_target_message(event: NewMessage.Event | CallbackQuery.Event) -> Message:
    if isinstance(event, CallbackQuery.Event) or event.message.is_reply:
        return await get_reply_message(event, previous=True)
    return event.message


async def compress_file_command(event: NewMessage.Event | CallbackQuery.Event) -> None:
    message = await get_target_message(event)
    target_format = await select_archive_format(event)
    if target_format is None:
        return

    progress_message = await send_progress_message(event, t('starting_file_download'))
    input_name = Path(get_download_name(message).name).name
    output_name = archive_output_name(input_name, target_format)
    archive_stem = strip_archive_suffix(input_name)
    is_conversion = is_archive_file(input_name)
    work_dir = TMP_DIR / f'{archive_stem}_archived_{int(time.time() * 1000)}'
    work_dir.mkdir(parents=True, exist_ok=True)

    try:
        async with download_to_temp_file(
            event, message, progress_message, suffix=Path(input_name).suffix, temp_dir=work_dir
        ) as temp_file_path:
            source_item = input_name
            cwd = work_dir
            output_file = work_dir / output_name
            if is_conversion:
                output_dir = work_dir / 'contents'
                output_dir.mkdir()
                if not await run_archive_step(
                    event,
                    progress_message,
                    archive_extract_command(temp_file_path, input_name, output_dir),
                    cwd=work_dir,
                    error_file_name=f'{archive_stem}_archive_error.txt',
                ):
                    return
                source_item = '.'
                cwd = output_dir
                output_file = work_dir / archive_output_name(
                    input_name, target_format, collision_marker='converted'
                )
                if target_format == 'br':
                    files = sorted(path for path in output_dir.rglob('*') if path.is_file())
                    if len(files) != 1:
                        await progress_message.edit(t('plain_brotli_requires_single_file_archive'))
                        return
                    source_item = str(files[0].relative_to(output_dir))
                    output_file = work_dir / f'{files[0].name}.br'
            else:
                temp_file_path.with_name(input_name).hardlink_to(temp_file_path)

            if not await run_archive_step(
                event,
                progress_message,
                archive_compress_command(source_item, output_file, target_format),
                cwd=cwd,
                error_file_name=f'{archive_stem}_archive_error.txt',
            ):
                return
            await upload_file_and_cleanup(event, output_file, progress_message, force_document=True)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
    result_key = (
        'archive_conversion_completed' if is_conversion else 'archive_compression_completed'
    )
    await progress_message.edit(t(result_key, target_format=target_format))


async def list_archive_command(event: NewMessage.Event | CallbackQuery.Event) -> None:
    message = await get_target_message(event)
    progress_message = await send_progress_message(event, t('starting_file_download'))
    input_name = get_download_name(message).name

    async with download_to_temp_file(
        event, message, progress_message, suffix=Path(input_name).suffix
    ) as temp_file_path:
        await progress_message.edit(t('starting_process'))
        output, code = await run_command(
            archive_list_command(temp_file_path, input_name), timeout=60 * 30
        )
        status = (
            t('process_completed') if code == 0 else t('process_failed_with_return_code', code=code)
        )
        await edit_or_send_as_file(
            event,
            progress_message,
            format_archive_output(status, output),
            file_name=f'{Path(input_name).stem}_list.txt',
            file_text=f'{status}\n{output or t("empty_output")}',
            parse_mode='html',
        )


async def unarchive_command(event: NewMessage.Event | CallbackQuery.Event) -> None:
    message = await get_target_message(event)
    progress_message = await send_progress_message(event, t('starting_file_download'))
    input_name = get_download_name(message).name
    archive_stem = strip_archive_suffix(input_name)
    output_dir = TMP_DIR / f'{archive_stem}_unarchived_{int(time.time() * 1000)}'
    output_dir.mkdir(parents=True, exist_ok=True)

    async with download_to_temp_file(
        event, message, progress_message, suffix=Path(input_name).suffix
    ) as temp_file_path:
        await progress_message.edit(t('starting_process'))
        output, code = await run_command(
            archive_extract_command(
                temp_file_path,
                input_name,
                output_dir,
                message.document.mime_type if message.document else '',
            ),
            timeout=60 * 60,
        )
        if code != 0:
            status = t('process_failed_with_return_code', code=code)
            await edit_or_send_as_file(
                event,
                progress_message,
                format_archive_output(status, output),
                file_name=f'{archive_stem}_unarchive_error.txt',
                file_text=f'{status}\n{output or t("empty_output")}',
                parse_mode='html',
            )
            shutil.rmtree(output_dir, ignore_errors=True)
            return

    files = rename_single_file_compression_output(
        sorted(p for p in output_dir.rglob('*') if p.is_file()),
        input_name,
        output_dir,
        message.document.mime_type if message.document else '',
    )
    if not files:
        await progress_message.edit(t('process_completed'))
        shutil.rmtree(output_dir, ignore_errors=True)
        return

    for idx, path in enumerate(files, start=1):
        await progress_message.edit(f'{t("uploading")} {idx}/{len(files)}')
        await upload_file_and_cleanup(event, path, progress_message, force_document=True)

    await progress_message.edit(f'{t("process_completed")} ({len(files)} files)')
    shutil.rmtree(output_dir, ignore_errors=True)


class FileManager(ModuleBase):
    name = 'File Manager'
    description = t('_file_manager_module_description')
    commands: ClassVar[ModuleBase.CommandsT] = {
        'archive': Command(
            handler=compress_file_command,
            description=t('_archive_description'),
            pattern=re.compile(r'^/archive\s+([\w.]+)$'),
            condition=has_file,
            is_applicable_for_reply=True,
        ),
        'unarchive': Command(
            handler=unarchive_command,
            description=t('_unarchive_description'),
            pattern=re.compile(r'^/unarchive$'),
            condition=lambda event, message: (
                is_admin_in_private(event, message) and has_file(event, message)
            ),
            is_applicable_for_reply=True,
        ),
        'archive list': Command(
            handler=list_archive_command,
            description=t('_archive_list_description'),
            pattern=re.compile(r'^/archive\s+list$'),
            condition=lambda event, message: (
                is_admin_in_private(event, message) and has_file(event, message)
            ),
            is_applicable_for_reply=False,
        ),
    }
