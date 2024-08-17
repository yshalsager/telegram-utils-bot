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
from src.utils.i18n import t
from src.utils.run import run_command


async def restart(event: NewMessage.Event) -> None:
    """Restart the bot."""
    restart_message = await event.reply(t('restarting_please_wait'))
    Path('restart.json').write_text(
        orjson.dumps({'chat': restart_message.chat_id, 'message': restart_message.id}).decode()
    )
    execl(executable, executable, '-m', 'src')  # noqa: S606


async def update(event: NewMessage.Event) -> None:
    """Update the bot."""
    message = await event.reply(t('updating_please_wait'))
    output, code = await run_command('git pull --rebase', cwd=PARENT_DIR)
    if code and code != 0:
        await message.edit(f'{t("failed_to_update")}:\n<pre>{output}</pre>')
        return None
    if output.strip() == 'Already up to date.':
        await message.edit(t('already_up_to_date'))
        return None
    await message.edit(f'{t("git_update_successful_updating_requirements")}\n<pre>{output}</pre>')

    output, code = await run_command('pip install --upgrade -r requirements.txt', cwd=PARENT_DIR)
    if code and code != 0:
        await message.edit(f'{t("failed_to_update_requirements")}:\n<pre>{output}</pre>')
        return None
    await message.edit(t('updated_successfully'))
    return await restart(event)


class Admin(ModuleBase):
    name = 'Admin'
    description = t('_admin_module_description')
    commands: ClassVar[ModuleBase.CommandsT] = {
        'restart': Command(
            handler=restart,
            description=t('_restart_description'),
            pattern=re.compile(r'^/restart$'),
            condition=is_admin_in_private,
        ),
        'update': Command(
            handler=update,
            description=t('_update_description'),
            pattern=re.compile(r'^/update$'),
            condition=is_admin_in_private,
        ),
    }
