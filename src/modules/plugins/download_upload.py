from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import ClassVar

import regex as re
from telethon.events import CallbackQuery, NewMessage
from telethon.tl.custom import Message

from src import DOWNLOADS_DIR, PARENT_DIR
from src.modules.base import ModuleBase
from src.modules.plugins.run import stream_shell_output
from src.utils.command import Command
from src.utils.downloads import download_file, get_download_name, get_filename_from_url, upload_file
from src.utils.filters import (
    has_file,
    has_no_file,
    has_valid_url,
    is_admin_in_private,
    is_file,
)
from src.utils.i18n import t
from src.utils.patterns import HTTP_URL_PATTERN
from src.utils.telegram import get_reply_message, send_progress_message


async def download_from_url(
    event: NewMessage.Event | CallbackQuery.Event,
    url: str,
    download_dir: Path,
    progress_message: Message | None = None,
) -> Path:
    filename = get_filename_from_url(url)
    download_to = download_dir / filename
    cmd = f"aria2c -x 16 -d {download_dir} -o {filename} '{url}' --allow-overwrite=true"
    await stream_shell_output(event, cmd, progress_message=progress_message)
    return download_to


async def download_file_command(event: NewMessage.Event | CallbackQuery.Event) -> None:
    progress_message = await send_progress_message(event, t('starting_file_download'))
    reply_message = await get_reply_message(event, previous=True)
    if url_match := re.search(HTTP_URL_PATTERN, reply_message.raw_text):
        url = url_match.group(0)
        download_to = await download_from_url(
            event, url, DOWNLOADS_DIR, progress_message=progress_message
        )
        if not download_to.exists():
            await event.reply(t('download_failed'))
            return
    else:
        reply_message = await get_reply_message(event, previous=True)
        download_to = DOWNLOADS_DIR / get_download_name(reply_message)
        with download_to.open('wb') as temp_file:
            temp_file_path = await download_file(event, temp_file, reply_message, progress_message)
            temp_file_path.rename(download_to)
    await progress_message.edit(f'{t("file_downloaded")}: <code>{download_to}</code>')


async def upload_file_command(event: NewMessage.Event) -> None:
    progress_message = await event.reply(t('starting_file_upload'))
    for file_path in PARENT_DIR.glob(event.message.text.split(maxsplit=1)[1].strip()):
        if file_path.exists():
            await upload_file(event, file_path, progress_message)
            await progress_message.edit(f'{t("file_uploaded")}: <code>{file_path.name}</code>')
            return
    await progress_message.edit(t('no_files_found'))


async def upload_from_url_command(event: NewMessage.Event) -> None:
    reply_message = await get_reply_message(event, previous=True)
    message = reply_message or event.message
    custom_name = ''
    url_match = re.search(HTTP_URL_PATTERN, message.raw_text)
    if url_match:
        url = url_match.group(0)
    else:
        await event.reply(t('no_valid_url_found'))
        return
    if custom := (message.raw_text or '').split('|', 1):
        custom_name = custom[1].strip() if len(custom) > 1 else ''
    progress_message = await send_progress_message(event, t('starting_file_download'))

    with NamedTemporaryFile(dir=DOWNLOADS_DIR, delete=False) as temp_file:
        download_to = await download_from_url(
            event, url, Path(temp_file.name).parent, progress_message=progress_message
        )
        if not download_to.exists():
            await progress_message.edit(t('download_failed'))
            return

        if custom_name:
            new_download_to = download_to.with_name(custom_name)
            download_to.rename(new_download_to)
            download_to = new_download_to

        await progress_message.edit(t('download_complete_starting_upload'))
        await upload_file(event, download_to, progress_message)
        await progress_message.edit(f'{t("file_uploaded")}: <code>{download_to.name}</code>')
        Path(temp_file.name).unlink(missing_ok=True)


async def upload_as_file_or_media(event: NewMessage.Event | CallbackQuery.Event) -> None:
    if isinstance(event, CallbackQuery.Event):
        force_document = event.data.decode().split('_')[-1] == 'file'
    else:
        force_document = event.message.text.split(maxsplit=1)[-1].strip() == 'file'

    reply_message = await get_reply_message(event, previous=True)
    progress_message = await send_progress_message(event, t('starting_file_download'))
    _type = 'file' if force_document else 'media'
    output_file_name = f'{reply_message.file.name or _type}{reply_message.file.ext}'
    with NamedTemporaryFile() as temp_file:
        temp_file_path = await download_file(event, temp_file, reply_message, progress_message)
        await progress_message.edit(t('download_complete_starting_upload'))
        temp_file_path = temp_file_path.rename(temp_file_path.with_name(output_file_name))
        await upload_file(event, temp_file_path, progress_message, force_document=force_document)

    await progress_message.edit(f'{t("file_uploaded_as")} {_type}: <code>{output_file_name}</code>')


class DownloadUpload(ModuleBase):
    name = 'Download'
    description = t('_download_module_description')
    commands: ClassVar[ModuleBase.CommandsT] = {
        'download': Command(
            handler=download_file_command,
            description=t('_download_description'),
            pattern=re.compile(r'^/download(?:\s+(.+))?$'),
            condition=lambda event, message: is_admin_in_private(event, message)
            and (has_file(event, message) or has_valid_url(event, message)),
            is_applicable_for_reply=True,
        ),
        'upload': Command(
            handler=upload_file_command,
            description=t('_upload_description'),
            pattern=re.compile(r'^/upload\s+(.+)$'),
            condition=lambda event, message: is_admin_in_private(event, message)
            and has_no_file(event, message),
        ),
        'upload file': Command(
            handler=upload_as_file_or_media,
            description=t('_upload_file_description'),
            pattern=re.compile(r'^/upload\s+file$'),
            condition=lambda event, message: has_file(event, message)
            and not is_file(event, message),
            is_applicable_for_reply=True,
        ),
        'upload media': Command(
            handler=upload_as_file_or_media,
            description=t('_upload_media_description'),
            pattern=re.compile(r'^/upload\s+media$'),
            condition=lambda event, message: has_file(event, message) and is_file(event, message),
            is_applicable_for_reply=True,
        ),
        'upload url': Command(
            handler=upload_from_url_command,
            description=t('_upload_url_description'),
            pattern=re.compile(r'^/upload\s+url\s+(.+)$'),
            condition=has_valid_url,
            is_applicable_for_reply=True,
        ),
    }
