from typing import ClassVar

import orjson
import regex as re
from telethon.events import NewMessage

from src.modules.base import ModuleBase
from src.utils.command import Command
from src.utils.filters import is_reply_in_private
from src.utils.i18n import t
from src.utils.json import json_options, process_dict


async def to_json(event: NewMessage.Event) -> None:
    reply_message = await event.get_reply_message()
    json_str = orjson.dumps(process_dict(reply_message.to_dict()), option=json_options).decode()
    await event.reply(f'<pre>{json_str}</pre>')


class Debug(ModuleBase):
    name = 'Debug'
    description = t('_debug_module_description')
    commands: ClassVar[ModuleBase.CommandsT] = {
        'json': Command(
            name='json',
            handler=to_json,
            description=t('_json_description'),
            pattern=re.compile(r'^/json$'),
            condition=is_reply_in_private,
        )
    }
