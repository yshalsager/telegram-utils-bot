from asyncio import Task, create_task, sleep
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from telethon import Button
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
    *,
    parse_mode: str | None = None,
    file_text: str | None = None,
) -> bool:
    try:
        await message.edit(text, parse_mode=parse_mode)
        return True
    except MessageTooLongError:
        progress_message = await send_progress_message(event, t('sending_file'))
        suffix = Path(file_name).suffix or '.txt'
        with NamedTemporaryFile(mode='w+', suffix=suffix) as temp_file:
            temp_file.write(file_text if file_text is not None else text)
            temp_file.flush()
            temp_file_path = Path(temp_file.name)
            temp_file_path = temp_file_path.rename(temp_file_path.with_name(file_name))
            await upload_file(event, temp_file_path, progress_message, caption=caption)
            await progress_message.delete()
        return False


async def _delete_message_after(message: Message, seconds: int = 10) -> None:
    await sleep(seconds)
    await message.delete()


async def _delete_event_message_after(event: CallbackQuery.Event, seconds: int = 10) -> None:
    await _delete_message_after(await event.get_message(), seconds)


def delete_message_after(message: Message, seconds: int = 10) -> Task[None]:
    return create_task(_delete_message_after(message, seconds))


def delete_event_message_after(event: CallbackQuery.Event, seconds: int = 10) -> Task[None]:
    return create_task(_delete_event_message_after(event, seconds))


async def _delete_callback_after(
    event: NewMessage.Event | CallbackQuery.Event, seconds: int = 60 * 5
) -> None:
    if isinstance(event, CallbackQuery.Event):
        await _delete_event_message_after(event, seconds)


def delete_callback_after(
    event: NewMessage.Event | CallbackQuery.Event, seconds: int = 60 * 5
) -> Task[None]:
    return create_task(_delete_callback_after(event, seconds))


async def inline_choice(
    event: CallbackQuery.Event,
    *,
    prefix: str,
    prompt_text: str,
    buttons: list[list[Button]],
    cast: Any = str,
) -> Any | None:
    data = event.data.decode('utf-8')
    if data.startswith(prefix):
        return cast(data.split('|')[-1])
    await event.edit(prompt_text, buttons=buttons)
    return None


async def inline_choice_grid(
    event: CallbackQuery.Event,
    *,
    prefix: str,
    prompt_text: str,
    pairs: list[tuple[str, Any]],
    cast: Any = str,
    cols: int = 3,
) -> Any | None:
    return await inline_choice(
        event,
        prefix=prefix,
        prompt_text=prompt_text,
        buttons=inline_buttons_grid(pairs, cols=cols),
        cast=cast,
    )


def buttons_grid(items: list[Any], cols: int = 3) -> list[list[Any]]:
    return [items[i : i + cols] for i in range(0, len(items), cols)]


def inline_buttons_grid(
    pairs: list[tuple[str, Any]],
    *,
    cols: int = 3,
) -> list[list[Button]]:
    return buttons_grid([Button.inline(text, data) for text, data in pairs], cols)


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
