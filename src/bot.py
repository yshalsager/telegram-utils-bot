"""
Telegram Bot
"""

import logging
from asyncio import CancelledError, Task, create_task, run, sleep
from collections.abc import Callable, Coroutine
from contextlib import suppress
from pathlib import Path
from typing import Any

from orjson import orjson
from telethon import Button, TelegramClient
from telethon.events import CallbackQuery, NewMessage, StopPropagation

from src import API_HASH, API_ID, BOT_ADMINS, BOT_TOKEN, PARENT_DIR
from src.modules.base import ModuleBase
from src.utils.modules_registry import ModuleRegistry
from src.utils.permission_manager import PermissionManager
from src.utils.telegram import get_reply_message

bot = TelegramClient('utils-bot', API_ID, API_HASH).start(bot_token=BOT_TOKEN)
bot.parse_mode = 'html'
bot_info = {}
permission_manager = PermissionManager(set(BOT_ADMINS), PARENT_DIR / 'permissions.json')
modules_registry = ModuleRegistry(__package__, permission_manager)
logger = logging.getLogger(__name__)


def main() -> None:
    """Run bot."""
    run(run_bot())


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
    message = await get_reply_message(event)
    task_id = f'{message.chat_id}_{message.id}'
    task: Task[bool] = create_task(module.handle(*handler_args))

    if not hasattr(event.client, 'active_tasks'):
        event.client.active_tasks = {}
    event.client.active_tasks[task_id] = task

    try:
        await task
    except CancelledError:
        await response_func('Operation cancelled.')
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
    command = event.pattern_match.group(1)
    module = modules_registry.get_module_by_command(command)
    if not module or not permission_manager.has_permission(module.name, event.sender_id):
        raise StopPropagation

    await handle_module_execution(event, module, (event, command), event.reply)
    raise StopPropagation


async def handle_messages(event: NewMessage.Event) -> None:
    applicable_modules = modules_registry.get_applicable_modules(event)
    if applicable_modules:
        keyboard = [
            [Button.inline(module.name, data=f'm_{module.name.lower()}')]
            for module in applicable_modules
        ]
        await event.reply('Choose an option:', buttons=keyboard)
    else:
        await event.reply('No applicable modules found for this input.')
    raise StopPropagation


async def handle_callback(event: CallbackQuery.Event) -> None:
    module_name = event.data.decode('utf-8').lstrip('m_')
    module = modules_registry.get_module_by_command(module_name)
    if not module or not permission_manager.has_permission(module.name, event.sender_id):
        return

    async def response_func(message: str) -> None:
        await event.answer(message, alert=True)

    await handle_module_execution(event, module, (event, module_name), response_func)
    await sleep(5)
    await event.delete()


async def start_command(event: NewMessage.Event) -> None:
    await event.reply('Welcome! Use /help to see available commands.')
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
    bot.add_event_handler(
        handle_commands,
        NewMessage(
            pattern=rf'^/(\w+)(?:@{bot_info['username']})?\s?(.+)?',
            func=lambda x: x.is_private
            and not any(x.message.text.startswith(c) for c in ('/start', '/help', '/cancel')),
        ),
    )
    bot.add_event_handler(start_command, NewMessage(pattern='/start', func=lambda x: x.is_private))
    bot.add_event_handler(
        cancel_command, NewMessage(pattern=r'^/cancel$', func=lambda x: x.message.is_reply)
    )
    bot.add_event_handler(
        handle_messages,
        NewMessage(func=lambda x: x.is_private and not x.message.text.startswith('/')),
    )
    bot.add_event_handler(handle_callback, CallbackQuery(pattern=r'^m_'))

    # Check if the bot is restarting
    await handle_restart()

    # Run blocking
    async with bot:
        await bot.run_until_disconnected()


if __name__ == '__main__':
    main()
