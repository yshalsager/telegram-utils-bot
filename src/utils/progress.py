import asyncio
import time

from humanize import naturalsize, precisedelta
from telethon.tl.custom import Message


async def progress_callback(current: float, total: float, event: Message, action: str) -> None:
    # Using a global dictionary to store last update time, current progress, and start time for each unique operation
    if not hasattr(progress_callback, 'last_updates'):
        progress_callback.last_updates = {}  # type: ignore[attr-defined]

    key = f'{event.chat_id}:{event.id}'
    now = time.time()
    last_update, last_current, start_time = progress_callback.last_updates.get(key, (0, 0, now))  # type: ignore[attr-defined]

    if now - last_update > 2 or current == total:
        percentage = current * 100 / total
        speed = (current - last_current) / (now - last_update) if now - last_update > 0 else 0
        elapsed_time = now - start_time
        remaining_time = (total - current) / speed if speed > 0 else 0
        progress_callback.last_updates[key] = (now, current, start_time)  # type: ignore[attr-defined]

        text = f'<b>{action}…</b>\n\n'
        text += f'{naturalsize(current)} / {naturalsize(total)} '
        if speed > 0:
            text += f'🌐 {naturalsize(speed)}/s '
        text += f'⏱️ {precisedelta(elapsed_time)}'
        if remaining_time > 0:
            text += f' ⏰ {precisedelta(remaining_time)}'
        text += '\n\n'

        # Generate a progress bar
        bar_length = 20
        filled_length = int(bar_length * current // total)
        bar = '█' * filled_length + '░' * (bar_length - filled_length)
        text += f'[{bar}] {percentage:.1f}%'

        await event.edit(text)

    await asyncio.sleep(0)  # to yield control and prevent blocking
