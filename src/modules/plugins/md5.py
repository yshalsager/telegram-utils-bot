import hashlib
from tempfile import NamedTemporaryFile
from typing import ClassVar

import regex as re
from telethon.events import CallbackQuery, NewMessage

from src.modules.base import ModuleBase
from src.utils.command import Command
from src.utils.downloads import download_file
from src.utils.filters import has_file
from src.utils.i18n import t
from src.utils.telegram import get_reply_message


async def calculate_md5(event: NewMessage.Event | CallbackQuery.Event) -> None:
    reply_message = await get_reply_message(event, previous=True)
    progress_message = await event.reply(t('md5_hashing_started'))

    with NamedTemporaryFile() as temp_file:
        temp_file_path = await download_file(event, temp_file, reply_message, progress_message)
        md5_hash = hashlib.md5(usedforsecurity=False)
        with temp_file_path.open('rb') as f:
            for chunk in iter(lambda: f.read(4096), b''):
                md5_hash.update(chunk)
        hash_result = md5_hash.hexdigest()
        await progress_message.edit(f'<code>{hash_result}</code>')


class MD5Hash(ModuleBase):
    name = 'MD5 Hash'
    description = t('_md5_module_description')
    commands: ClassVar[ModuleBase.CommandsT] = {
        'md5': Command(
            name='md5',
            handler=calculate_md5,
            description=t('_md5_description'),
            pattern=re.compile(r'^/md5$'),
            condition=has_file,
            is_applicable_for_reply=True,
        )
    }
