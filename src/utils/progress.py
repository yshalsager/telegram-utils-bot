import asyncio
import time

from humanize import naturalsize
from telethon.tl.custom import Message


async def progress_callback(current: float, total: float, event: Message, action: str) -> None:
    # Using a global dictionary to store last update time and current progress for each unique operation
    if not hasattr(progress_callback, 'last_updates'):
        progress_callback.last_updates = {}  # type: ignore[attr-defined]

    key = f'{event.chat_id}:{event.id}'
    now = time.time()
    last_update, last_current = progress_callback.last_updates.get(key, (0, 0))  # type: ignore[attr-defined]

    if now - last_update > 2 or current == total:
        percentage = current * 100 / total
        speed = (current - last_current) / (now - last_update) if now - last_update > 0 else 0
        progress_callback.last_updates[key] = (now, current)  # type: ignore[attr-defined]

        text = f'<b>{action}...</b>\n\n'
        text += f'{naturalsize(current)} of {naturalsize(total)}\n'
        if speed > 0:
            text += f'@ {naturalsize(speed)}/s\n'
        text += '\n'

        # Generate a progress bar
        bar_length = 20
        filled_length = int(bar_length * current // total)
        bar = '█' * filled_length + '░' * (bar_length - filled_length)
        text += f'[{bar}] {percentage:.1f}%'

        await event.edit(text)

    await asyncio.sleep(0)  # to yield control and prevent blocking
