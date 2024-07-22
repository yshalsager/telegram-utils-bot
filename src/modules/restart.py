"""Bot restart module."""

from os import execl
from pathlib import Path
from sys import executable
from typing import ClassVar

import orjson
import regex as re
from telethon.events import NewMessage

from src import BOT_ADMINS
from src.modules.base import ModuleBase
from src.utils.command import Command


async def restart(event: NewMessage.Event) -> None:
    """Restart the bot."""
    restart_message = await event.reply('Restarting, please wait...')
    Path('restart.json').write_text(
        orjson.dumps({'chat': restart_message.chat_id, 'message': restart_message.id}).decode()
    )
    execl(executable, executable, '-m', 'src')  # noqa: S606


class Restart(ModuleBase):
    name = 'Restart'
    description = 'Restart the bot.'
    commands: ClassVar[ModuleBase.CommandsT] = {
        'restart': Command(
            handler=restart,
            description='Restart the bot.',
            pattern=re.compile(r'^/restart$'),
            condition=lambda event, _: event.sender_id in BOT_ADMINS,
        )
    }
