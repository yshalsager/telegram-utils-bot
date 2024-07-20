import orjson
from telethon.events import NewMessage

from src.modules.base import ModuleBase
from src.utils.json import json_options, process_dict


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
