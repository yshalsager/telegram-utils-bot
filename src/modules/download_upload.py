from pathlib import Path

from telethon.events import CallbackQuery, NewMessage

from src import DOWNLOADS_DIR
from src.modules.base import ModuleBase
from src.utils.downloads import get_download_name
from src.utils.fast_telethon import download_file, upload_file
from src.utils.progress import progress_callback
from src.utils.telegram import get_reply_message


async def download_file_command(event: NewMessage.Event | CallbackQuery.Event) -> None:
    reply_message = await get_reply_message(event, previous=True)
    if not reply_message.file:
        await event.reply('Please reply to a message with a file to download.')
        return

    download_to = DOWNLOADS_DIR / get_download_name(reply_message)
    progress_message = await event.reply('Starting file download...')

    with download_to.open('wb') as temp_file:
        await download_file(
            event.client,
            reply_message.document,
            temp_file,
            progress_callback=lambda current, total: progress_callback(
                current, total, progress_message, 'Downloading'
            ),
        )

    await progress_message.edit(f'File successfully downloaded: <pre>{download_to}</pre>')


async def upload_file_command(event: NewMessage.Event) -> None:
    try:
        filepath = event.message.text.split(maxsplit=1)[1].strip()
    except IndexError:
        await event.reply('Please provide a filepath: /upload <filepath>')
        return

    file_path = Path(filepath)
    if not file_path.exists():
        await event.reply(f'File not found: <pre>{filepath}</pre>')
        return

    progress_message = await event.reply('Starting file upload...')

    with file_path.open('rb') as file_to_upload:
        uploaded_file = await upload_file(
            event.client,
            file_to_upload,
            file_path.name,
            progress_callback=lambda current, total: progress_callback(
                current, total, progress_message, 'Uploading'
            ),
        )

    await event.client.send_file(
        event.chat_id,
        file=uploaded_file,
        reply_to=event.message.id,
    )

    await progress_message.edit(f'File successfully uploaded: <pre>{file_path.name}</pre>')


class DownloadUpload(ModuleBase):
    @property
    def name(self) -> str:
        return 'Download'

    @property
    def description(self) -> str:
        return 'Download files from Telegram to local filesystem'

    def commands(self) -> ModuleBase.CommandsT:
        return {
            'download': {
                'handler': download_file_command,
                'description': 'Download a file: Reply to a message with a file and use <code>/download</code>',
                'is_applicable_for_reply': True,
            },
            'upload': {
                'handler': upload_file_command,
                'description': 'Upload a file: <code>/upload [filepath]</code>',
            },
        }

    def is_applicable(self, event: NewMessage.Event) -> bool:
        return bool(event.message.text.startswith('/download') and event.message.is_reply)

    @staticmethod
    def is_applicable_for_reply(event: NewMessage.Event) -> bool:
        return bool(event.message.document)
