"""Bot restart module."""

from os import execl
from pathlib import Path
from sys import executable

import orjson
from telethon.events import NewMessage

from src import BOT_ADMINS
from src.modules.base import ModuleBase


# @bot.on(NewMessage(from_users=BOT_ADMINS, pattern=r'/restart'))
async def restart(event: NewMessage.Event) -> None:
    """Restart the bot."""
    restart_message = await event.reply('Restarting, please wait...')
    Path('restart.json').write_text(
        orjson.dumps({'chat': restart_message.chat_id, 'message': restart_message.id}).decode()
    )
    execl(executable, executable, '-m', 'src')  # noqa: S606


class Restart(ModuleBase):
    @property
    def name(self) -> str:
        return 'Restart'

    @property
    def description(self) -> str:
        return 'Restart the bot.'

    def commands(self) -> ModuleBase.CommandsT:
        return {'restart': {'handler': restart, 'description': self.description}}

    def is_applicable(self, event: NewMessage.Event) -> bool:
        return event.message.text.startswith('/restart') and event.sender_id in BOT_ADMINS
