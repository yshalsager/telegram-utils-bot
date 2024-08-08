"""Bot Admin module."""

from os import execl
from pathlib import Path
from sys import executable
from typing import ClassVar

import orjson
import regex as re
from telethon.events import NewMessage

from src import PARENT_DIR
from src.modules.base import ModuleBase
from src.utils.command import Command
from src.utils.filters import is_admin_in_private
from src.utils.run import run_command


async def restart(event: NewMessage.Event) -> None:
    """Restart the bot."""
    restart_message = await event.reply('Restarting, please wait...')
    Path('restart.json').write_text(
        orjson.dumps({'chat': restart_message.chat_id, 'message': restart_message.id}).decode()
    )
    execl(executable, executable, '-m', 'src')  # noqa: S606


async def update(event: NewMessage.Event) -> None:
    """Update the bot."""
    message = await event.reply('Updating, please wait...')
    output, code = await run_command('git pull --rebase', cwd=PARENT_DIR)
    if code and code != 0:
        await message.edit(f'Failed to update:\n<pre>{output}</pre>')
        return None
    await message.edit(f'Git update successful. Updating requirements...\n<pre>{output}</pre>')

    output, code = await run_command('pip install --upgrade -r requirements.txt', cwd=PARENT_DIR)
    if code and code != 0:
        await message.edit(f'Failed to update requirements:\n<pre>{output}</pre>')
        return None
    await message.edit('Updated successfully!')
    return await restart(event)


class Admin(ModuleBase):
    name = 'Admin'
    description = 'Admin related commands.'
    commands: ClassVar[ModuleBase.CommandsT] = {
        'restart': Command(
            handler=restart,
            description='Restart the bot.',
            pattern=re.compile(r'^/restart$'),
            condition=is_admin_in_private,
        ),
        'update': Command(
            handler=update,
            description='Update the bot.',
            pattern=re.compile(r'^/update$'),
            condition=is_admin_in_private,
        ),
    }
