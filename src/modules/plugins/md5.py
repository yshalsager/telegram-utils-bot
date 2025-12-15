import hashlib
from typing import ClassVar

import regex as re
from telethon.events import CallbackQuery, NewMessage

from src.modules.base import ModuleBase
from src.utils.command import Command
from src.utils.downloads import download_to_temp_file
from src.utils.filters import has_file
from src.utils.i18n import t
from src.utils.telegram import get_reply_message, send_progress_message


async def calculate_md5(event: NewMessage.Event | CallbackQuery.Event) -> None:
    reply_message = await get_reply_message(event, previous=True)
    progress_message = await send_progress_message(event, t('md5_hashing_started'))

    async with download_to_temp_file(event, reply_message, progress_message) as temp_file_path:
        md5_hash = hashlib.md5(usedforsecurity=False)
        with temp_file_path.open('rb') as f:
            for chunk in iter(lambda: f.read(4096), b''):
                md5_hash.update(chunk)
        await progress_message.edit(f'<code>{md5_hash.hexdigest()}</code>')


class MD5Hash(ModuleBase):
    name = 'MD5 Hash'
    description = t('_md5_module_description')
    commands: ClassVar[ModuleBase.CommandsT] = {
        'md5': Command(
            handler=calculate_md5,
            description=t('_md5_description'),
            pattern=re.compile(r'^/md5$'),
            condition=has_file,
            is_applicable_for_reply=True,
        )
    }
