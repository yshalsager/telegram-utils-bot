from datetime import UTC, datetime
from pathlib import Path
from tempfile import _TemporaryFileWrapper

from telethon.events import NewMessage
from telethon.tl.custom import Message
from telethon.tl.types import DocumentAttributeFilename

from src.utils.fast_telethon import download_file, upload_file
from src.utils.progress import progress_callback


def get_download_name(message: Message, new_filename: str = '') -> Path:
    mime_type = message.document.mime_type.split('/')[1]
    if mime_type == 'octet-stream':
        mime_type = ''

    original_filename = next(
        (
            attr.file_name
            for attr in message.document.attributes
            if isinstance(attr, DocumentAttributeFilename)
        ),
        Path(message.file.name).name if message.file.name else 'unknown',
    )

    if original_filename == 'unknown':
        original_filename = f"{datetime.now(UTC).strftime('%Y-%m-%d_%H-%M-%S')}.{mime_type}"
    original_ext = Path(original_filename).suffix or f'.{mime_type}'

    if not new_filename:
        return Path(original_filename)

    new_filename_with_ext = Path(new_filename)
    if original_ext and new_filename_with_ext.suffix != original_ext:
        new_filename_with_ext = new_filename_with_ext.with_suffix(original_ext)
    return new_filename_with_ext


async def download_audio(
    event: NewMessage.Event,
    temp_file: _TemporaryFileWrapper,
    reply_message: Message,
    progress_message: Message,
) -> None:
    await download_file(
        event.client,
        reply_message.document,
        temp_file,
        progress_callback=lambda current, total: progress_callback(
            current, total, progress_message, 'Downloading'
        ),
    )


async def upload_audio(
    event: NewMessage.Event, output_file: Path, progress_message: Message, is_voice: bool
) -> None:
    with output_file.open('rb') as file_to_upload:
        uploaded_file = await upload_file(
            event.client,
            file_to_upload,
            output_file.name,
            progress_callback=lambda current, total: progress_callback(
                current, total, progress_message, 'Uploading'
            ),
        )
    await event.client.send_file(
        event.chat_id,
        file=uploaded_file,
        voice_note=is_voice,
        reply_to=event.message.id,
    )
