from asyncio import sleep
from io import BytesIO

from telethon.errors import MessageTooLongError
from telethon.events import CallbackQuery, NewMessage
from telethon.tl.custom import Message


async def get_reply_message(
    event: NewMessage.Event | CallbackQuery.Event, previous: bool = False
) -> Message:
    if isinstance(event, CallbackQuery.Event):
        message = await event.get_message()
        return message if not previous else await message.get_reply_message()
    return await event.message.get_reply_message()


async def edit_or_send_as_file(event: NewMessage.Event, message: Message, text: str) -> None:
    try:
        await message.edit(text)
    except MessageTooLongError:
        file = BytesIO(text.encode())
        file.name = 'output.txt'
        await event.client.send_file(message.chat_id, file, reply_to=message.id)


async def delete_message_after(message: Message, seconds: int = 10) -> None:
    await sleep(seconds)
    await message.delete()
