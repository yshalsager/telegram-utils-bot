"""
Telegram Bot
"""

import logging
from asyncio import CancelledError, Task, create_task, get_event_loop, sleep
from collections.abc import Callable, Coroutine
from contextlib import suppress
from itertools import zip_longest
from pathlib import Path
from typing import Any

import regex as re
from orjson import orjson
from telethon import Button, TelegramClient
from telethon.events import CallbackQuery, InlineQuery, NewMessage, StopPropagation

from src import API_HASH, API_ID, BOT_ADMINS, BOT_TOKEN, PARENT_DIR
from src.modules.base import InlineModuleBase, ModuleBase
from src.utils.modules_registry import ModuleRegistry
from src.utils.permission_manager import PermissionManager
from src.utils.telegram import get_reply_message

bot = TelegramClient('utils-bot', API_ID, API_HASH).start(bot_token=BOT_TOKEN)
bot.parse_mode = 'html'
bot_info = {}
permission_manager = PermissionManager(set(BOT_ADMINS), PARENT_DIR / 'permissions.json')
modules_registry = ModuleRegistry(__package__, permission_manager)
logger = logging.getLogger(__name__)

commands_with_modifiers = (
    'audio',
    'media',
    'video',
    'tasks',
    'plugins',
    'permissions',
    'upload',
    'transcribe',
)


def main() -> None:
    """Run bot."""
    loop = get_event_loop()
    loop.run_until_complete(run_bot())


async def handle_restart() -> None:
    restart_path = Path('restart.json')
    if not restart_path.exists():
        return

    restart_message = orjson.loads(restart_path.read_text())
    await bot.edit_message(
        restart_message['chat'],
        restart_message['message'],
        'Restarted Successfully!',
    )
    restart_path.unlink()


async def handle_module_execution(
    event: NewMessage.Event | CallbackQuery.Event,
    module: ModuleBase,
    handler_args: tuple[Any, ...],
    response_func: Callable[[str], Coroutine[Any, Any, None]],
) -> None:
    message = await get_reply_message(event) or event.message
    task_id = f'{message.chat_id}_{message.id}'
    task: Task[bool] = create_task(module.handle(*handler_args))

    if not hasattr(event.client, 'active_tasks'):
        event.client.active_tasks = {}
    event.client.active_tasks[task_id] = task

    try:
        await task
    except CancelledError:
        await response_func('Operation cancelled.')
    except StopPropagation:
        pass
    except Exception as e:  # noqa: BLE001
        logger.error(f'Error in module {module.name}: {e!s}')
        await response_func(f'An error occurred: {e!s}')
    finally:
        if task_id in event.client.active_tasks:
            task = event.client.active_tasks[task_id]
            if not task.done():
                with suppress(CancelledError):
                    task.cancel()
            del event.client.active_tasks[task_id]


async def handle_commands(event: NewMessage.Event) -> None:
    command_with_args = re.match(r'^/(\w+)(?:\s+(\w+))?(?:\s+(.+))?$', event.message.text)
    command = command_with_args.group(1)
    modifier = command_with_args.group(2)
    # args = command_with_args.group(3)
    if modifier and command in commands_with_modifiers:
        command = f'{command} {modifier}'
    module = modules_registry.get_module_by_command(
        command
    ) or modules_registry.get_module_by_command(command_with_args.group(1))
    if (
        not module
        or not permission_manager.has_permission(module.name, event.sender_id)
        or not await module.is_applicable(event)
    ):
        raise StopPropagation

    await handle_module_execution(event, module, (event, command), event.reply)
    raise StopPropagation


async def handle_messages(event: NewMessage.Event) -> None:
    if applicable_commands := await modules_registry.get_applicable_commands(event):
        keyboard = [
            [
                Button.inline(command, data=f'm|{command.replace(' ', '_')}')
                for command in row
                if command is not None
            ]
            for row in list(zip_longest(*[iter(sorted(applicable_commands))] * 3, fillvalue=None))
        ]
        await event.reply('Choose an option:', buttons=keyboard)
    else:
        await event.reply('No applicable modules found for this input.')
    raise StopPropagation


async def handle_callback(event: CallbackQuery.Event) -> None:
    command = event.data.decode('utf-8')
    if command.startswith('m|'):
        command = command[2:]
    command = command.replace('_', ' ')
    module = modules_registry.get_module_by_command(command.split('|')[0])
    if not module or not permission_manager.has_permission(module.name, event.sender_id):
        return

    async def response_func(message: str) -> None:
        await event.answer(message, alert=True)

    await handle_module_execution(event, module, (event, command), response_func)
    await sleep(60 * 5)
    await event.delete()


async def handle_inline_query(event: InlineQuery.Event) -> None:
    for module in modules_registry.modules:
        if isinstance(module, InlineModuleBase) and await module.is_applicable(event):
            await module.handle(event)
            break
    raise StopPropagation


async def start_command(event: NewMessage.Event) -> None:
    await event.reply('Welcome! Use /commands to see available commands.')
    raise StopPropagation


async def cancel_command(event: NewMessage.Event) -> None:
    original_message = await get_reply_message(event)
    task_id = f'{original_message.chat_id}_{original_message.id}'
    if not getattr(event.client, 'active_tasks', {}).get(task_id):
        await event.reply('No active operation found for this command.')
        return
    event.client.active_tasks[task_id].cancel()
    await event.reply('Operation cancellation requested.')
    raise StopPropagation


async def run_bot() -> None:
    """Run the bot."""
    # Get bot info
    me = await bot.get_me()
    bot_info.update({'name': me.first_name, 'username': me.username, 'id': me.id})
    logger.info(f'Bot started: {me.first_name} (@{me.username})')

    # Register event handlers
    bot.add_event_handler(start_command, NewMessage(pattern='/start', func=lambda x: x.is_private))
    bot.add_event_handler(
        cancel_command, NewMessage(pattern=r'^/cancel$', func=lambda x: x.message.is_reply)
    )

    # Register module-specific handlers
    for module in modules_registry.modules:
        if hasattr(module, 'register_handlers'):
            module.register_handlers(bot)

    # Register general handlers
    bot.add_event_handler(
        handle_commands,
        NewMessage(
            pattern=rf'^/(\w+)(?:@{bot_info['username']})?\s?(.+)?',
            func=lambda x: x.is_private
            and not any(x.message.text.startswith(c) for c in ('/start', '/help', '/cancel')),
        ),
    )
    bot.add_event_handler(
        handle_messages,
        NewMessage(
            func=lambda x: x.is_private
            and not x.message.text.startswith('/')
            and not x.message.via_bot
        ),
    )
    bot.add_event_handler(handle_callback, CallbackQuery(pattern=r'^m|'))
    bot.add_event_handler(handle_inline_query, InlineQuery(func=lambda x: len(x.text) > 2))

    # Check if the bot is restarting
    await handle_restart()

    # Run blocking
    async with bot:
        await bot.run_until_disconnected()
