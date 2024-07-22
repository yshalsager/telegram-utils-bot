from typing import ClassVar

import orjson
import regex as re
from telethon.events import NewMessage

from src.modules.base import ModuleBase
from src.utils.command import Command
from src.utils.json import json_options, process_dict


async def to_json(event: NewMessage.Event) -> None:
    reply_message = await event.get_reply_message()
    json_str = orjson.dumps(process_dict(reply_message.to_dict()), option=json_options).decode()
    await event.reply(f'<pre>{json_str}</pre>')


class Debug(ModuleBase):
    name = 'Debug'
    description = 'Print message to JSON.'
    commands: ClassVar[ModuleBase.CommandsT] = {
        'json': Command(
            name='json',
            handler=to_json,
            description='Show replied to message info in JSON format.',
            pattern=re.compile(r'^/json$'),
            condition=lambda event, _: event.message.is_reply,
        )
    }
