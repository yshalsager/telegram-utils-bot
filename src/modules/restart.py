"""Bot restart module."""

import json
from os import execl
from pathlib import Path
from sys import executable

from telethon import events

from src import BOT_ADMINS
from src.bot import bot


@bot.on(events.NewMessage(from_users=BOT_ADMINS, pattern=r'/restart'))
async def restart(event: events.NewMessage.Event) -> None:
    """Restart the bot."""
    restart_message = await event.reply('Restarting, please wait...')
    Path('restart.json').write_text(
        json.dumps({'chat': restart_message.chat_id, 'message': restart_message.id})
    )
    execl(executable, executable, '-m', 'src')  # noqa: S606
