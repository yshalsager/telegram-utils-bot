from typing import Any, ClassVar

import regex as re
from telethon.events import CallbackQuery, NewMessage, StopPropagation
from telethon.tl.custom import Message

from src.modules.base import ModuleBase
from src.utils.command import Command
from src.utils.downloads import download_to_temp_file, get_download_name, upload_file_and_cleanup
from src.utils.filters import has_file
from src.utils.i18n import t
from src.utils.telegram import get_reply_message, send_progress_message


async def _rename_process(
    event: NewMessage.Event,
    reply_message: Message | None,
    match: Any,
) -> None:
    assert reply_message is not None
    new_filename = match.group(1).strip()

    new_filename_with_ext = get_download_name(reply_message, new_filename)
    if new_filename_with_ext.name == reply_message.file.name:
        await event.reply(t('the_new_filename_is_the_same'))
        return

    progress_message = await send_progress_message(event, t('starting_file_rename'))

    async with download_to_temp_file(event, reply_message, progress_message) as temp_file_path:
        new_file_path = temp_file_path.rename(temp_file_path.with_name(str(new_filename_with_ext)))
        await upload_file_and_cleanup(event, new_file_path, progress_message)

    await progress_message.edit(f'{t("file_renamed")}: {new_filename_with_ext}')
    raise StopPropagation


async def rename(event: NewMessage.Event | CallbackQuery.Event) -> None:
    if isinstance(event, CallbackQuery.Event):
        await event.client.reply_prompts.ask(
            event,
            t('please_provide_a_new_filename'),
            pattern=re.compile(r'^(.+)$'),
            handler=_rename_process,
            invalid_reply_text=t('please_provide_a_new_filename'),
        )
        return

    reply_message = await get_reply_message(event, previous=True)
    new_filename = event.message.text.split(maxsplit=1)[1].strip()
    match = re.match(r'^(.+)$', new_filename)
    assert match is not None
    await _rename_process(event, reply_message, match)


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
