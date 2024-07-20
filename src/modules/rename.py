from pathlib import Path
from tempfile import NamedTemporaryFile

from telethon.events import NewMessage

from src.modules.base import ModuleBase
from src.utils.downloads import get_download_name
from src.utils.fast_telethon import download_file, upload_file
from src.utils.progress import progress_callback


async def rename_file(event: NewMessage.Event) -> None:
    try:
        new_filename = event.message.text.split(maxsplit=1)[1].strip()
        if not new_filename:
            raise ValueError('New filename is empty')
    except (IndexError, ValueError):
        await event.reply('Please provide a new filename: /rename <new_filename>')
        return

    reply_message = await event.message.get_reply_message()
    if not reply_message.file:
        await event.reply("The replied message doesn't contain a file.")
        return

    new_filename_with_ext = get_download_name(reply_message, new_filename)

    progress_message = await event.reply('Starting file rename process...')

    with NamedTemporaryFile(delete=False) as temp_file:
        await download_file(
            event.client,
            reply_message.document,
            temp_file,
            progress_callback=lambda current, total: progress_callback(
                current, total, progress_message, 'Downloading'
            ),
        )
        temp_file_path = Path(temp_file.name)

        new_file_path = temp_file_path.with_name(str(new_filename_with_ext))
        temp_file_path.rename(new_file_path)

    with new_file_path.open('rb') as file_to_upload:
        uploaded_file = await upload_file(
            event.client,
            file_to_upload,
            new_file_path.name,
            progress_callback=lambda current, total: progress_callback(
                current, total, progress_message, 'Uploading'
            ),
        )

    await event.client.send_file(
        event.chat_id,
        file=uploaded_file,
        reply_to=event.message.id,
    )
    new_file_path.unlink(missing_ok=True)

    await progress_message.edit(f'File successfully renamed to: {new_filename_with_ext}')


class Rename(ModuleBase):
    @property
    def name(self) -> str:
        return 'Rename'

    @property
    def description(self) -> str:
        return 'Rename files'

    def commands(self) -> ModuleBase.CommandsT:
        return {
            'rename': {
                'handler': rename_file,
                'description': 'Rename a file: /rename <new_filename>',
            }
        }

    def is_applicable(self, event: NewMessage.Event) -> bool:
        return bool(event.message.text.startswith('/rename') and event.message.is_reply)
