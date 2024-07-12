"""
Telegram Bot
"""

import json
from asyncio import run
from pathlib import Path

from telethon import TelegramClient

from src import API_HASH, API_ID, BOT_TOKEN
from src.modules import ALL_MODULES
from src.utils.modules_loader import load_modules

bot = TelegramClient('utils-bot', API_ID, API_HASH).start(bot_token=BOT_TOKEN)
bot.parse_mode = 'html'


def main() -> None:
    """Run bot."""
    run(run_bot())


async def run_bot() -> None:
    """Run the bot."""
    load_modules(ALL_MODULES, __package__)
    # Check if the bot is restarting
    restart_path = Path('restart.json')
    if restart_path.exists():
        restart_message = json.loads(restart_path.read_text())
        await bot.edit_message(
            restart_message['chat'],
            restart_message['message'],
            'Restarted Successfully!',
        )
        restart_path.unlink()
    async with bot:
        await bot.run_until_disconnected()


if __name__ == '__main__':
    main()
