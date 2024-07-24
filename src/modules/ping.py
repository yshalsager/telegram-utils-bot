from datetime import UTC, datetime
from typing import ClassVar

import regex as re
from telethon.events import NewMessage

from src.modules.base import ModuleBase
from src.utils.command import Command


async def pong(event: NewMessage.Event) -> None:
    await event.reply(
        f'Pong {(datetime.now(UTC) - event.message.date.replace(tzinfo=UTC)).total_seconds():.3f}s'
    )


class Ping(ModuleBase):
    name = 'Ping'
    description = 'Ping the bot.'
    commands: ClassVar[ModuleBase.CommandsT] = {
        'ping': Command(
            name='ping',
            handler=pong,
            description='Restart the bot.',
            pattern=re.compile(r'^/ping$'),
        )
    }
