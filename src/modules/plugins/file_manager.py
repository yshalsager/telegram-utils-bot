import shlex
import shutil
import time
import zipfile
from html import escape as html_escape
from pathlib import Path
from typing import ClassVar

import regex as re
from telethon.events import CallbackQuery, NewMessage
from telethon.tl.custom import Message

from src import TMP_DIR
from src.modules.base import ModuleBase
from src.utils.command import Command
from src.utils.downloads import download_to_temp_file, get_download_name, upload_file_and_cleanup
from src.utils.filters import has_file, is_admin_in_private
from src.utils.i18n import t
from src.utils.run import run_command
from src.utils.telegram import edit_or_send_as_file, get_reply_message, send_progress_message


def archive_suffixes(file_name: str) -> list[str]:
    return [suffix.lower() for suffix in Path(file_name).suffixes]


def is_brotli_tar(file_name: str) -> bool:
    return archive_suffixes(file_name)[-2:] == ['.tar', '.br']


def is_brotli_file(file_name: str) -> bool:
    return archive_suffixes(file_name)[-1:] == ['.br']


def format_archive_output(status: str, output: str) -> str:
    return f'{status}\n<pre>{html_escape(output or t("empty_output"))}</pre>'


def archive_list_command(archive_path: Path, file_name: str) -> str:
    quoted_path = shlex.quote(str(archive_path))
    if is_brotli_tar(file_name):
        return f'tar --warning=no-unknown-keyword --use-compress-program=brotli -tf {quoted_path}'
    if is_brotli_file(file_name):
        return f'brotli -t -v {quoted_path}'
    return f'7z l -ba {quoted_path}'


def archive_extract_command(archive_path: Path, file_name: str, output_dir: Path) -> str:
    quoted_path = shlex.quote(str(archive_path))
    quoted_output_dir = shlex.quote(str(output_dir))
    if is_brotli_tar(file_name):
        return f'tar --warning=no-unknown-keyword --use-compress-program=brotli -xf {quoted_path} -C {quoted_output_dir}'
    if is_brotli_file(file_name):
        output_path = output_dir / Path(file_name).with_suffix('').name
        return f'brotli -d -f -o {shlex.quote(str(output_path))} {quoted_path}'
    return f'7z x -y -o{quoted_output_dir} {quoted_path}'


async def get_target_message(event: NewMessage.Event | CallbackQuery.Event) -> Message:
    if isinstance(event, CallbackQuery.Event) or event.message.is_reply:
        return await get_reply_message(event, previous=True)
    return event.message


async def zip_file_command(event: NewMessage.Event | CallbackQuery.Event) -> None:
    message = await get_target_message(event)
    progress_message = await send_progress_message(event, t('starting_file_download'))
    input_name = get_download_name(message).name
    zip_name = f'{Path(input_name).stem}.zip'

    async with download_to_temp_file(event, message, progress_message) as temp_file_path:
        zip_path = temp_file_path.with_name(zip_name)
        with zipfile.ZipFile(zip_path, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
            zf.write(temp_file_path, arcname=input_name)
        await progress_message.edit(t('download_complete_starting_upload'))
        await upload_file_and_cleanup(event, zip_path, progress_message, force_document=True)
    await progress_message.edit(f'{t("file_uploaded")}: <code>{zip_name}</code>')


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


async def unzip_archive_command(event: NewMessage.Event | CallbackQuery.Event) -> None:
    message = await get_target_message(event)
    progress_message = await send_progress_message(event, t('starting_file_download'))
    input_name = get_download_name(message).name
    archive_stem = Path(input_name).stem
    output_dir = TMP_DIR / f'{archive_stem}_unzipped_{int(time.time() * 1000)}'
    output_dir.mkdir(parents=True, exist_ok=True)

    async with download_to_temp_file(
        event, message, progress_message, suffix=Path(input_name).suffix
    ) as temp_file_path:
        await progress_message.edit(t('starting_process'))
        output, code = await run_command(
            archive_extract_command(temp_file_path, input_name, output_dir),
            timeout=60 * 60,
        )
        if code != 0:
            status = t('process_failed_with_return_code', code=code)
            await edit_or_send_as_file(
                event,
                progress_message,
                format_archive_output(status, output),
                file_name=f'{archive_stem}_unzip_error.txt',
                file_text=f'{status}\n{output or t("empty_output")}',
                parse_mode='html',
            )
            shutil.rmtree(output_dir, ignore_errors=True)
            return

    files = sorted(p for p in output_dir.rglob('*') if p.is_file())
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
        'zip': Command(
            handler=zip_file_command,
            description=t('_zip_description'),
            pattern=re.compile(r'^/zip$'),
            condition=has_file,
            is_applicable_for_reply=True,
        ),
        'unzip': Command(
            handler=unzip_archive_command,
            description=t('_unzip_description'),
            pattern=re.compile(r'^/unzip$'),
            condition=lambda event, message: (
                is_admin_in_private(event, message) and has_file(event, message)
            ),
            is_applicable_for_reply=True,
        ),
        'archive list': Command(
            handler=list_archive_command,
            description=t('_archive_list_description'),
            pattern=re.compile(r'^/list\s+archive$'),
            condition=lambda event, message: (
                is_admin_in_private(event, message) and has_file(event, message)
            ),
            is_applicable_for_reply=True,
        ),
    }
