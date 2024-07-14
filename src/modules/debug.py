from typing import Any

import orjson
from humanize import naturalsize
from telethon.events import NewMessage

from src.modules.base import ModuleBase

json_options = (
    orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS | orjson.OPT_NAIVE_UTC | orjson.OPT_OMIT_MICROSECONDS
)


def process_dict(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {
            k: naturalsize(v) if k == 'size' else process_dict(v)
            for k, v in obj.items()
            if not isinstance(v, bytes)
        }
    if isinstance(obj, list):
        return [process_dict(item) for item in obj if not isinstance(item, bytes)]
    if isinstance(obj, bytes):
        return '<bytes>'
    return obj


class Debug(ModuleBase):
    @staticmethod
    async def to_json(event: NewMessage.Event) -> None:
        reply_message = await event.get_reply_message()
        json_str = orjson.dumps(process_dict(reply_message.to_dict()), option=json_options).decode()
        await event.reply(f'<pre>{json_str}</pre>')

    @property
    def name(self) -> str:
        return 'Debug'

    @property
    def description(self) -> str:
        return 'Print message to JSON.'

    def commands(self) -> ModuleBase.CommandsT:
        return {'json': {'handler': self.to_json, 'description': self.description}}

    def is_applicable(self, event: NewMessage.Event) -> bool:
        return bool(event.message.text.startswith('/json') and event.message.is_reply)
