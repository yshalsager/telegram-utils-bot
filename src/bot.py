"""
Telegram Bot
"""

import logging
import traceback
from asyncio import CancelledError, Task, create_task, get_event_loop
from collections.abc import Callable, Coroutine
from contextlib import suppress
from itertools import zip_longest
from pathlib import Path
from typing import Any

import orjson
from telethon import Button, TelegramClient
from telethon.events import CallbackQuery, InlineQuery, NewMessage, StopPropagation

from src import API_HASH, API_ID, BOT_ADMINS, BOT_TOKEN, STATE_DIR
from src.modules.base import InlineModuleBase, ModuleBase, matches_command
from src.utils.i18n import t
from src.utils.modules_registry import ModuleRegistry
from src.utils.permission_manager import PermissionManager
from src.utils.telegram import delete_message_after, get_reply_message


class BotState:
    def __init__(self) -> None:
        self.bot: TelegramClient | None = None
        self.permission_manager: PermissionManager | None = None
        self.modules_registry: ModuleRegistry | None = None
        self.commands_with_modifiers: set[str] = set()


state = BotState()
bot_info = {}
logger = logging.getLogger(__name__)


async def create_bot() -> TelegramClient:
    client = TelegramClient(str(STATE_DIR / 'utils-bot'), API_ID, API_HASH)
    await client.start(bot_token=BOT_TOKEN)
    client.parse_mode = 'html'
    return client


def get_bot() -> TelegramClient:
    assert state.bot is not None
    return state.bot


def get_modules_registry() -> ModuleRegistry:
    assert state.modules_registry is not None
    return state.modules_registry


def get_permission_manager() -> PermissionManager:
    assert state.permission_manager is not None
    return state.permission_manager


def main() -> None:
    """Run bot."""
    loop = get_event_loop()
    loop.run_until_complete(run_bot())


async def handle_restart() -> None:
    restart_path = Path('restart.json')
    if not restart_path.exists():
        return

    restart_message = orjson.loads(restart_path.read_text())
    await get_bot().edit_message(
        restart_message['chat'],
        restart_message['message'],
        t('restarted_successfully'),
    )
    restart_path.unlink()


async def handle_module_execution(
    event: NewMessage.Event | CallbackQuery.Event,
    module: ModuleBase,
    handler_args: tuple[Any, ...],
    response_func: Callable[[str], Coroutine[Any, Any, None]],
) -> None:
    reply_message = None
    if (isinstance(event, NewMessage.Event) and event.message.is_reply) or isinstance(
        event, CallbackQuery.Event
    ):
        reply_message = await get_reply_message(event)
    message = reply_message or event.message
    task_id = f'{message.chat_id}_{message.id}'
    task: Task[bool] = create_task(module.handle(*handler_args))

    if not hasattr(event.client, 'active_tasks'):
        event.client.active_tasks = {}
    event.client.active_tasks[task_id] = task

    try:
        await task
    except CancelledError:
        await response_func(t('operation_cancelled'))
    except StopPropagation:
        pass
    except Exception as e:  # noqa: BLE001
        logger.error(
            f'Error in module {module.name}: {"\n".join(traceback.format_exception(None, e, e.__traceback__))}'
        )
        await response_func(t('an_error_occurred', error=f'{e!s}'))
    finally:
        if task_id in event.client.active_tasks:
            task = event.client.active_tasks[task_id]
            if not task.done():
                with suppress(CancelledError):
                    task.cancel()
            del event.client.active_tasks[task_id]


async def handle_commands(event: NewMessage.Event) -> None:
    match = event.pattern_match
    command = match.group(1)
    modifier = match.group(2)
    if modifier and command in state.commands_with_modifiers:
        command = f'{command} {modifier}'

    module_registry = get_modules_registry()
    perms = get_permission_manager()
    module = module_registry.get_module_by_command(
        command
    ) or module_registry.get_module_by_command(match.group(1))
    if not module or not perms.has_permission(module.name, event.chat_id):
        raise StopPropagation

    reply_message = (
        await get_reply_message(event, previous=True) if event.message.is_reply else None
    )
    cmd = module.commands.get(command) or module.commands.get(command.split(' ', 1)[0])
    if not cmd or not matches_command(event, reply_message, cmd):
        raise StopPropagation

    await handle_module_execution(event, module, (event, command), event.reply)
    raise StopPropagation


async def handle_messages(event: NewMessage.Event) -> None:
    if applicable_commands := await get_modules_registry().get_applicable_commands(event):
        keyboard = [
            [
                Button.inline(
                    t(f'_{command.replace(" ", "_")}'), data=f'm|{command.replace(" ", "_")}'
                )
                for command in row
                if command is not None
            ]
            for row in list(zip_longest(*[iter(sorted(applicable_commands))] * 3, fillvalue=None))
        ]
        await event.reply(t('choose_an_option'), buttons=keyboard)
    elif event.is_private:
        await event.reply(t('no_applicable_modules'))
    raise StopPropagation


async def handle_callback(event: CallbackQuery.Event) -> None:
    command = event.data.decode('utf-8')
    if command.startswith('m|'):
        command = command[2:]
    command = command.replace('_', ' ')
    perms = get_permission_manager()
    module = get_modules_registry().get_module_by_command(command.split('|')[0])
    if not module or not perms.has_permission(module.name, event.chat_id):
        return

    async def response_func(message: str) -> None:
        await event.answer(message, alert=True)

    await handle_module_execution(event, module, (event, command), response_func)
    event.client.loop.create_task(delete_message_after(await event.get_message(), seconds=60 * 5))


async def handle_inline_query(event: InlineQuery.Event) -> None:
    for module in get_modules_registry().modules:
        if isinstance(module, InlineModuleBase) and await module.is_applicable(event):
            await module.handle(event)
            break
    raise StopPropagation


async def start_command(event: NewMessage.Event) -> None:
    await event.reply(t('welcome'))
    raise StopPropagation


async def cancel_command(event: NewMessage.Event) -> None:
    original_message = await get_reply_message(event)
    task_id = f'{original_message.chat_id}_{original_message.id}'
    if not getattr(event.client, 'active_tasks', {}).get(task_id):
        await event.reply(t('no_active_operation'))
        return
    event.client.active_tasks[task_id].cancel()
    await event.reply(t('operation_cancellation_requested'))
    raise StopPropagation


async def run_bot() -> None:
    """Run the bot."""
    state.permission_manager = PermissionManager(set(BOT_ADMINS), STATE_DIR / 'permissions.json')
    state.modules_registry = ModuleRegistry(__package__, state.permission_manager)
    state.commands_with_modifiers = {
        command.split(' ', 1)[0]
        for module in state.modules_registry.modules
        for command in module.commands
        if ' ' in command
    }

    state.bot = await create_bot()
    bot = get_bot()
    bot.modules_registry = get_modules_registry()
    bot.permission_manager = get_permission_manager()

    # Get bot info
    me = await bot.get_me()
    bot_info.update({'name': me.first_name, 'username': me.username, 'id': me.id})
    logger.info(f'Bot started: {me.first_name} (@{me.username})')

    # Register event handlers
    bot.add_event_handler(start_command, NewMessage(pattern='/start'))
    bot.add_event_handler(
        cancel_command, NewMessage(pattern=r'^/cancel$', func=lambda x: x.message.is_reply)
    )

    # Register module-specific handlers
    for module in get_modules_registry().modules:
        if hasattr(module, 'register_handlers'):
            module.register_handlers(bot)

    # Register general handlers
    bot.add_event_handler(
        handle_commands,
        NewMessage(
            pattern=rf'^/(\w+)(?:@{bot_info["username"]})?(?:\s+(\w+))?(?:\s+(.+))?$',
            func=lambda x: not any(x.message.text.startswith(c) for c in ('/start', '/cancel')),
        ),
    )
    bot.add_event_handler(
        handle_messages,
        NewMessage(func=lambda x: not x.message.text.startswith('/') and not x.message.via_bot),
    )
    bot.add_event_handler(handle_callback, CallbackQuery(pattern=r'^m|'))
    bot.add_event_handler(handle_inline_query, InlineQuery(func=lambda x: len(x.text) > 2))

    # Check if the bot is restarting
    await handle_restart()

    # Run blocking
    async with bot:
        await bot.run_until_disconnected()
