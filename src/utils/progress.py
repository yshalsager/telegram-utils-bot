import asyncio
import time
from contextlib import suppress

from humanize import naturalsize, precisedelta
from telethon.errors import RPCError
from telethon.tl.custom import Message

last_updates: dict[str, tuple[float, float, float]] = {}


async def progress_callback(
    current: float, total: float, event: Message, action: str, *, unit: str = ''
) -> None:
    key = f'{event.chat_id}:{event.id}'
    now = time.time()
    last_update, last_current, start_time = last_updates.get(key, (0, 0, now))

    if now - last_update > 2 or current == total:
        percentage = current * 100 / total
        speed = (current - last_current) / (now - last_update) if now - last_update > 0 else 0
        elapsed_time = now - start_time
        remaining_time = (total - current) / speed if speed > 0 else 0
        if remaining_time > 60 * 60 * 24 * 365:
            remaining_time = 0
        last_updates[key] = (now, current, start_time)

        text = f'<b>{action}…</b>\n\n'
        if unit := unit.strip():
            text += f'{int(current)} / {int(total)} {unit} '
            if speed > 0:
                text += f'🌐 {speed:.2f}/{unit}/s '
        else:
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

        with suppress(RPCError):
            await event.edit(text)

    await asyncio.sleep(0)  # to yield control and prevent blocking
