import orjson
from telethon.events import NewMessage

from src.modules.base import ModuleBase

json_options = (
    orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS | orjson.OPT_NAIVE_UTC | orjson.OPT_OMIT_MICROSECONDS
)


class Debug(ModuleBase):
    @staticmethod
    async def to_json(event: NewMessage.Event) -> None:
        await event.reply(
            f'<pre>{orjson.dumps(event.message.to_dict(), option=json_options).decode()}</pre>',
        )

    @property
    def name(self) -> str:
        return 'Debug'

    @property
    def description(self) -> str:
        return 'Print message to JSON.'

    def commands(self) -> ModuleBase.CommandsT:
        return {'json': {'handler': self.to_json, 'description': self.description}}

    def is_applicable(self, event: NewMessage.Event) -> bool:
        return bool(event.message.text.startswith('/json'))
