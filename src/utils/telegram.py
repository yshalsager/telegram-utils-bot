from asyncio import Task, create_task, sleep
from pathlib import Path
from tempfile import NamedTemporaryFile

from telethon.errors import MessageTooLongError
from telethon.events import CallbackQuery, NewMessage
from telethon.tl.custom import Message

from src.utils.downloads import upload_file
from src.utils.i18n import t


async def get_reply_message(
    event: NewMessage.Event | CallbackQuery.Event, previous: bool = False
) -> Message:
    if isinstance(event, CallbackQuery.Event):
        message = await event.get_message()
        return message if not previous else await message.get_reply_message()
    return await event.message.get_reply_message()


async def edit_or_send_as_file(
    event: NewMessage.Event | CallbackQuery.Event,
    message: Message,
    text: str,
    file_name: str = 'output.txt',
    caption: str = '',
) -> bool:
    try:
        await message.edit(text)
        return True
    except MessageTooLongError:
        progress_message = await send_progress_message(event, t('sending_file'))
        with NamedTemporaryFile(mode='w+', suffix='.txt') as temp_file:
            temp_file.write(text)
            temp_file.flush()
            temp_file_path = Path(temp_file.name)
            temp_file_path = temp_file_path.rename(temp_file_path.with_name(file_name))
            await upload_file(event, temp_file_path, progress_message, caption=caption)
            await progress_message.delete()
        return False


async def _delete_message_after(message: Message, seconds: int = 10) -> None:
    await sleep(seconds)
    await message.delete()


def delete_message_after(message: Message, seconds: int = 10) -> Task[None]:
    return create_task(_delete_message_after(message, seconds))


async def send_progress_message(
    event: NewMessage.Event | CallbackQuery.Event,
    text: str,
    *,
    reply: bool = True,
) -> Message:
    if isinstance(event, CallbackQuery.Event):
        await event.answer()
        reply_to = None
    else:
        reply_to = event.message.id if reply else None
    return await event.client.send_message(event.chat_id, text, reply_to=reply_to)
