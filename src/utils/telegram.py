from asyncio import sleep
from pathlib import Path
from tempfile import NamedTemporaryFile

from telethon.errors import MessageTooLongError
from telethon.events import CallbackQuery, NewMessage
from telethon.tl.custom import Message

from src.utils.downloads import upload_file


async def get_reply_message(
    event: NewMessage.Event | CallbackQuery.Event, previous: bool = False
) -> Message:
    if isinstance(event, CallbackQuery.Event):
        message = await event.get_message()
        return message if not previous else await message.get_reply_message()
    return await event.message.get_reply_message()


async def edit_or_send_as_file(
    event: NewMessage.Event,
    message: Message,
    text: str,
    file_name: str = 'output.txt',
    caption: str = '',
) -> None:
    try:
        await message.edit(text)
    except MessageTooLongError:
        progress_message = await event.reply('Sending file...')
        with NamedTemporaryFile(mode='w+', suffix='.txt') as temp_file:
            temp_file.write(text)
            temp_file.flush()
            temp_file_path = Path(temp_file.name)
            temp_file_path = temp_file_path.rename(temp_file_path.with_name(file_name))
            await upload_file(event, temp_file_path, progress_message, caption=caption)
            await progress_message.delete()


async def delete_message_after(message: Message, seconds: int = 10) -> None:
    await sleep(seconds)
    await message.delete()
