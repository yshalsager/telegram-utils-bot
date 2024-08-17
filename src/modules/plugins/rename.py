from collections import defaultdict
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import ClassVar

import regex as re
from telethon import TelegramClient
from telethon.events import CallbackQuery, NewMessage, StopPropagation

from src.modules.base import ModuleBase
from src.utils.command import Command
from src.utils.downloads import get_download_name, upload_file
from src.utils.fast_telethon import download_file
from src.utils.filters import has_file, is_valid_reply_state
from src.utils.i18n import t
from src.utils.progress import progress_callback
from src.utils.reply import ReplyState, StateT, handle_callback_query_for_reply_state
from src.utils.telegram import get_reply_message

reply_states: StateT = defaultdict(
    lambda: {'state': ReplyState.WAITING, 'media_message_id': None, 'reply_message_id': None}
)


async def rename(event: NewMessage.Event | CallbackQuery.Event) -> None:
    if isinstance(event, CallbackQuery.Event):
        return await handle_callback_query_for_reply_state(
            event, reply_states, t('please_provide_a_new_filename')
        )

    if event.sender_id in reply_states:
        reply_states[event.sender_id]['state'] = ReplyState.PROCESSING
        reply_message = await event.client.get_messages(
            event.chat_id, ids=reply_states[event.sender_id]['media_message_id']
        )
        new_filename = event.message.text
    else:
        reply_message = await get_reply_message(event, previous=True)
        new_filename = event.message.text.split(maxsplit=1)[1].strip()

    new_filename_with_ext = get_download_name(reply_message, new_filename)
    if new_filename_with_ext.name == reply_message.file.name:
        await event.reply(t('the_new_filename_is_the_same'))
        return None

    progress_message = await event.reply(t('starting_file_rename'))

    with NamedTemporaryFile(delete=False) as temp_file:
        await download_file(
            event.client,
            reply_message.document,
            temp_file,
            progress_callback=lambda current, total: progress_callback(
                current, total, progress_message, t('downloading')
            ),
        )
        temp_file_path = Path(temp_file.name)
        new_file_path = temp_file_path.with_name(str(new_filename_with_ext))
        temp_file_path.rename(new_file_path)

    await upload_file(event, new_file_path, progress_message)
    new_file_path.unlink(missing_ok=True)

    await progress_message.edit(f'{t('file_renamed')}: {new_filename_with_ext}')
    if event.sender_id in reply_states:
        del reply_states[event.sender_id]
    raise StopPropagation


class Rename(ModuleBase):
    name = 'Rename'
    description = t('_rename_module_description')
    commands: ClassVar[ModuleBase.CommandsT] = {
        'rename': Command(
            handler=rename,
            description=t('_rename_description'),
            pattern=re.compile(r'^/rename\s+(.+)$'),
            condition=has_file,
            is_applicable_for_reply=True,
        )
    }

    @staticmethod
    def register_handlers(bot: TelegramClient) -> None:
        bot.add_event_handler(
            rename,
            NewMessage(func=lambda e: is_valid_reply_state(e, reply_states)),
        )
