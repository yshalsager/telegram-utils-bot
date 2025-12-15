"""Bot Admin module."""

import logging
from asyncio import sleep
from os import execl
from pathlib import Path
from sys import executable
from typing import ClassVar

import orjson
import regex as re
from telethon.errors import FloodWaitError
from telethon.events import NewMessage

from src import PARENT_DIR
from src.modules.base import ModuleBase
from src.utils.command import Command
from src.utils.filters import is_admin_in_private, is_reply_in_private
from src.utils.i18n import t
from src.utils.run import run_command
from src.utils.telegram import edit_or_send_as_file


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

    output, code = await run_command('uv sync --frozen --no-cache', cwd=PARENT_DIR)
    if code and code != 0:
        await edit_or_send_as_file(
            event, message, f'{t("failed_to_update_requirements")}:\n<pre>{output}</pre>'
        )
        return None
    await message.edit(t('updated_successfully'))
    return await restart(event)


async def broadcast(event: NewMessage.Event) -> None:
    """Broadcast a message to all bot users."""
    permission_manager = event.client.permission_manager
    users = list({i for _ in permission_manager.module_permissions.values() for i in _})
    if not users:
        return

    success_count = 0
    fail_count = 0
    reply_message = await event.get_reply_message()
    progress_message = await event.reply(t('broadcasting_message'))
    users_count = len(users)

    for user_id in users:
        try:
            await event.client.send_message(user_id, reply_message)
        except FloodWaitError as e:
            await sleep(e.seconds + 1)
            await event.client.send_message(user_id, reply_message)
        except Exception as e:  # noqa: BLE001
            logging.error(f'Error broadcasting message to {user_id}: {e}')
            fail_count += 1

        success_count += 1
        if (success_count + fail_count) % 5 == 0:
            await progress_message.edit(
                t('broadcasting_progress', progress=success_count + fail_count, total=users_count)
            )
        await sleep(0.25)

    await progress_message.edit(
        t(
            'broadcasting_completed',
            success=success_count,
            failed=fail_count,
            total=users_count,
        )
    )


class Admin(ModuleBase):
    name = 'Admin'
    description = t('_admin_module_description')
    commands: ClassVar[ModuleBase.CommandsT] = {
        'broadcast': Command(
            handler=broadcast,
            description=t('_broadcast_description'),
            pattern=re.compile(r'^/broadcast$'),
            condition=lambda e, m: is_admin_in_private(e, m) and is_reply_in_private(e, m),
        ),
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
