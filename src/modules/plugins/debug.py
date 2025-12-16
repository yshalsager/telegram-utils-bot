from html import escape as html_escape
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import ClassVar

import orjson
import regex as re
from telethon.events import NewMessage

from src.modules.base import ModuleBase
from src.utils.command import Command
from src.utils.downloads import upload_file
from src.utils.filters import is_reply_in_private
from src.utils.i18n import t
from src.utils.json_processing import json_options, process_dict


async def to_json(event: NewMessage.Event) -> None:
    reply_message = await event.get_reply_message()
    json_str = orjson.dumps(process_dict(reply_message.to_dict()), option=json_options).decode()
    json_html = html_escape(json_str)
    if len(json_html) > 3500:
        progress_message = await event.reply(t('sending_file'))
        with NamedTemporaryFile(mode='w+', suffix='.json') as temp_file:
            temp_file.write(json_str)
            temp_file.flush()
            path = Path(temp_file.name)
            path = path.rename(path.with_name('message.json'))
            await upload_file(event, path, progress_message, force_document=True)
        await progress_message.delete()
        return

    await event.reply(f'<pre>{json_html}</pre>')


class Debug(ModuleBase):
    name = 'Debug'
    description = t('_debug_module_description')
    commands: ClassVar[ModuleBase.CommandsT] = {
        'json': Command(
            handler=to_json,
            description=t('_json_description'),
            pattern=re.compile(r'^/json$'),
            condition=is_reply_in_private,
        )
    }
