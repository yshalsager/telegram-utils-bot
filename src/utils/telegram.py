from telethon.events import CallbackQuery, NewMessage


async def get_reply_message(
    event: NewMessage.Event | CallbackQuery.Event, previous: bool = False
) -> NewMessage.Event:
    if isinstance(event, CallbackQuery.Event):
        message = await event.get_message()
        return message if not previous else await message.get_reply_message()
    return await event.message.get_reply_message()
