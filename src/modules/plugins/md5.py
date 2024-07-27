import hashlib
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import ClassVar

import regex as re
from telethon.events import CallbackQuery, NewMessage

from src.modules.base import ModuleBase
from src.utils.command import Command
from src.utils.fast_telethon import download_file
from src.utils.filters import has_file
from src.utils.progress import progress_callback
from src.utils.telegram import get_reply_message


async def calculate_md5(event: NewMessage.Event | CallbackQuery.Event) -> None:
    reply_message = await get_reply_message(event, previous=True)
    progress_message = await event.reply('Starting MD5 hash calculation...')

    with NamedTemporaryFile(delete=False) as temp_file:
        await download_file(
            event.client,
            reply_message.document,
            temp_file,
            progress_callback=lambda current, total: progress_callback(
                current, total, progress_message, 'Downloading'
            ),
        )
        temp_file_path = Path(temp_file.name)

    md5_hash = hashlib.md5(usedforsecurity=False)
    with temp_file_path.open('rb') as f:
        for chunk in iter(lambda: f.read(4096), b''):
            md5_hash.update(chunk)

    hash_result = md5_hash.hexdigest()
    temp_file_path.unlink(missing_ok=True)
    await progress_message.edit(f'<code>{hash_result}</code>')


class MD5Hash(ModuleBase):
    name = 'MD5 Hash'
    description = 'Calculate MD5 hash of a Telegram file'
    commands: ClassVar[ModuleBase.CommandsT] = {
        'md5': Command(
            name='md5',
            handler=calculate_md5,
            description='Calculate MD5 hash of a Telegram file',
            pattern=re.compile(r'^/md5$'),
            condition=has_file,
            is_applicable_for_reply=True,
        )
    }
