import shlex
import zipfile
from pathlib import Path
from typing import ClassVar

import regex as re
from telethon.events import CallbackQuery, NewMessage
from telethon.tl.custom import Message

from src import DOWNLOADS_DIR
from src.modules.base import ModuleBase
from src.utils.command import Command
from src.utils.downloads import download_to_temp_file, get_download_name, upload_file_and_cleanup
from src.utils.filters import has_file, is_admin_in_private
from src.utils.i18n import t
from src.utils.run import run_command
from src.utils.telegram import edit_or_send_as_file, get_reply_message, send_progress_message


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

    async with download_to_temp_file(event, message, progress_message) as temp_file_path:
        await progress_message.edit(t('starting_process'))
        output, code = await run_command(
            f'7z l -ba {shlex.quote(str(temp_file_path))}', timeout=60 * 30
        )
        status = (
            t('process_completed') if code == 0 else t('process_failed_with_return_code', code=code)
        )
        await edit_or_send_as_file(
            event,
            progress_message,
            f'{status}\n<pre>{output or t("empty_output")}</pre>',
            file_name=f'{Path(get_download_name(message).name).stem}_list.txt',
        )


async def unzip_archive_command(event: NewMessage.Event | CallbackQuery.Event) -> None:
    message = await get_target_message(event)
    progress_message = await send_progress_message(event, t('starting_file_download'))
    archive_stem = Path(get_download_name(message).name).stem
    output_dir = DOWNLOADS_DIR / f'{archive_stem}_unzipped'
    output_dir.mkdir(parents=True, exist_ok=True)

    async with download_to_temp_file(event, message, progress_message) as temp_file_path:
        await progress_message.edit(t('starting_process'))
        output, code = await run_command(
            f'7z x -y -o{shlex.quote(str(output_dir))} {shlex.quote(str(temp_file_path))}',
            timeout=60 * 60,
        )
        if code != 0:
            await edit_or_send_as_file(
                event,
                progress_message,
                f'{t("process_failed_with_return_code", code=code)}\n<pre>{output or t("empty_output")}</pre>',
                file_name=f'{archive_stem}_unzip_error.txt',
            )
            return

    extracted_files = sum(1 for p in output_dir.rglob('*') if p.is_file())
    await progress_message.edit(
        f'{t("process_completed")} ({extracted_files} files)\n{t("saved_to")}: <code>{output_dir}</code>'
    )


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
            condition=lambda event, message: is_admin_in_private(event, message)
            and has_file(event, message),
            is_applicable_for_reply=True,
        ),
        'archive list': Command(
            handler=list_archive_command,
            description=t('_list_archive_description'),
            pattern=re.compile(r'^/list\s+archive$'),
            condition=lambda event, message: is_admin_in_private(event, message)
            and has_file(event, message),
            is_applicable_for_reply=True,
        ),
    }
