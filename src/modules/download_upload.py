from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import ClassVar

import regex as re
from telethon.events import CallbackQuery, NewMessage

from src import DOWNLOADS_DIR
from src.modules.base import ModuleBase
from src.utils.command import Command
from src.utils.downloads import download_file, get_download_name, upload_file
from src.utils.filters import has_file_or_reply_with_file, has_no_file_or_reply_with_file, is_file
from src.utils.telegram import get_reply_message


async def download_file_command(event: NewMessage.Event | CallbackQuery.Event) -> None:
    reply_message = await get_reply_message(event, previous=True)
    download_to = DOWNLOADS_DIR / get_download_name(reply_message)
    progress_message = await event.reply('Starting file download...')

    with download_to.open('wb') as temp_file:
        await download_file(event, temp_file, reply_message, progress_message)
        Path(temp_file.name).rename(download_to)

    await progress_message.edit(f'File successfully downloaded: <code>{download_to}</code>')


async def upload_file_command(event: NewMessage.Event) -> None:
    file_path = Path(event.message.text.split(maxsplit=1)[1].strip())
    if not file_path.exists():
        await event.reply(f'File not found: <code>{file_path.name}</code>')
        return

    progress_message = await event.reply('Starting file upload...')
    await upload_file(event, file_path, progress_message)
    await progress_message.edit(f'File successfully uploaded: <code>{file_path.name}</code>')


async def upload_as_file_or_media(event: NewMessage.Event | CallbackQuery.Event) -> None:
    if isinstance(event, CallbackQuery.Event):
        await event.answer()
        force_document = event.data.decode().split('_')[-1] == 'file'
    else:
        force_document = event.message.text.split(maxsplit=1)[-1].strip() == 'file'

    reply_message = await get_reply_message(event, previous=True)
    progress_message = await event.reply('Starting download...')

    with NamedTemporaryFile() as temp_file:
        await download_file(event, temp_file, reply_message, progress_message)
        await progress_message.edit('Download complete. Starting upload...')
        temp_file_path = Path(temp_file.name).with_name(reply_message.file.name)
        await upload_file(event, temp_file_path, progress_message, force_document=force_document)

    await progress_message.edit(
        f'File successfully uploaded as {"file" if force_document else "media"}: <code>{reply_message.file.name}</code>'
    )


class DownloadUpload(ModuleBase):
    name = 'Download'
    description = 'Download / Upload files from Telegram / local filesystem.'
    commands: ClassVar[ModuleBase.CommandsT] = {
        'download': Command(
            handler=download_file_command,
            description='Download a file: Reply to a message with a file and use <code>/download</code>',
            pattern=re.compile(r'^/download$'),
            condition=has_file_or_reply_with_file,
            is_applicable_for_reply=True,
        ),
        'upload': Command(
            handler=upload_file_command,
            description='[filepath]: Upload a file from local filesystem',
            pattern=re.compile(r'^/upload\s+(.+)$'),
            condition=has_no_file_or_reply_with_file,
        ),
        'upload file': Command(
            handler=upload_as_file_or_media,
            description='Upload media as file: Reply to a message with media and use <code>/as_file</code>',
            pattern=re.compile(r'^/upload\s+file$'),
            condition=lambda event, message: has_file_or_reply_with_file(event, message)
            and not is_file(event, message),
            is_applicable_for_reply=True,
        ),
        'upload media': Command(
            handler=upload_as_file_or_media,
            description='Upload document as media: Reply to a message with a file and use <code>/as_media</code>',
            pattern=re.compile(r'^/upload\s+media$'),
            condition=lambda event, message: has_file_or_reply_with_file(event, message)
            and is_file(event, message),
            is_applicable_for_reply=True,
        ),
    }
