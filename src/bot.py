"""
Telegram Bot
"""

import json
import logging
from asyncio import run
from pathlib import Path

from telethon import TelegramClient
from telethon.events import NewMessage, StopPropagation
from telethon.tl.types import KeyboardButton, KeyboardButtonRow, ReplyKeyboardMarkup

from src import API_HASH, API_ID, BOT_ADMINS, BOT_TOKEN
from src.modules.base import ModuleBase
from src.utils.modules_loader import ModuleRegistry

bot = TelegramClient('utils-bot', API_ID, API_HASH).start(bot_token=BOT_TOKEN)
bot.parse_mode = 'html'
bot_info = {}
modules = ModuleRegistry(__package__)
logger = logging.getLogger(__name__)


def main() -> None:
    """Run bot."""
    run(run_bot())


async def handle_restart() -> None:
    restart_path = Path('restart.json')
    if not restart_path.exists():
        return

    restart_message = json.loads(restart_path.read_text())
    await bot.edit_message(
        restart_message['chat'],
        restart_message['message'],
        'Restarted Successfully!',
    )
    restart_path.unlink()


async def handle_commands(event: NewMessage.Event) -> None:
    command = event.pattern_match.group(1)
    module = modules.get_module_by_command(command)
    if module:
        await module.handle(event, command)
    else:
        await event.reply('Unknown command. Use /help to see available commands.')
    raise StopPropagation


async def start_command(event: NewMessage.Event) -> None:
    await event.reply('Welcome! Use /help to see available commands.')
    raise StopPropagation


async def help_command(event: NewMessage.Event) -> None:
    all_commands: dict[str, ModuleBase.CommandsT] = modules.get_all_commands()
    help_text = '<b>Available commands</b>:\n\n'
    for module, commands in all_commands.items():
        help_text += f'<i>{module.upper()}</i>:\n'
        for cmd, data in commands.items():
            help_text += f'/{cmd}: {data['description']}\n'
        help_text += '\n'
    await event.reply(help_text)
    raise StopPropagation


async def handle_messages(event: NewMessage.Event) -> None:
    applicable_modules = modules.get_applicable_modules(event)
    if applicable_modules:
        keyboard = [
            KeyboardButtonRow([KeyboardButton(module.name)]) for module in applicable_modules
        ]
        markup = ReplyKeyboardMarkup(keyboard)
        await event.reply('Choose an option:', buttons=markup)
    else:
        await event.reply('No applicable modules found for this input.')
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
            and not any(x.message.text.startswith(c) for c in ('/start', '/help')),
        ),
    )
    bot.add_event_handler(start_command, NewMessage(pattern='/start', func=lambda x: x.is_private))
    bot.add_event_handler(
        help_command,
        NewMessage(
            pattern='/help', func=lambda x: x.is_private and x.message.sender_id in BOT_ADMINS
        ),
    )
    bot.add_event_handler(handle_messages, NewMessage(func=lambda x: x.is_private))

    # Check if the bot is restarting
    await handle_restart()

    # Run blocking
    async with bot:
        await bot.run_until_disconnected()


if __name__ == '__main__':
    main()
