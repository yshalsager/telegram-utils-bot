from datetime import UTC, datetime
from pathlib import Path

from telethon.events import NewMessage
from telethon.tl.types import Document, DocumentAttributeFilename


def get_download_name(
    original_file: Document, reply_message: NewMessage.Event, new_filename: str = ''
) -> Path:
    mime_type = original_file.mime_type.split('/')[1]
    if mime_type == 'octet-stream':
        mime_type = ''

    original_filename = next(
        (
            attr.file_name
            for attr in original_file.attributes
            if isinstance(attr, DocumentAttributeFilename)
        ),
        Path(reply_message.file.name).name if reply_message.file.name else 'unknown',
    )

    if original_file == 'unknown':
        original_filename = f"{datetime.now(UTC).strftime('%Y-%m-%d_%H-%M-%S')}.{mime_type}"
    original_ext = Path(original_filename).suffix or f'.{mime_type}'

    if not new_filename:
        return Path(original_filename)

    new_filename_with_ext = Path(new_filename)
    if original_ext and new_filename_with_ext.suffix != original_ext:
        new_filename_with_ext = new_filename_with_ext.with_suffix(original_ext)
    return new_filename_with_ext
