from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from io import BufferedRandom, BufferedWriter
from pathlib import Path
from tempfile import NamedTemporaryFile, _TemporaryFileWrapper
from typing import Any
from urllib import parse
from uuid import uuid4

from telethon.events import CallbackQuery, NewMessage
from telethon.tl.custom import Message
from telethon.tl.types import DocumentAttributeFilename

from src import TMP_DIR
from src.utils.fast_telethon import download_file as fast_download_file
from src.utils.fast_telethon import upload_file as fast_upload_file
from src.utils.i18n import t
from src.utils.progress import progress_callback


def get_default_filename() -> str:
    return f'{datetime.now(UTC).strftime("%Y-%m-%d_%H-%M-%S")}'


def get_download_name(message: Message, new_filename: str = '') -> Path:
    mime_type = ''
    if message.document:
        mime_type = message.document.mime_type.split('/')[-1] if message.document.mime_type else ''
        original_filename = (
            next(
                (
                    attr.file_name
                    for attr in message.document.attributes
                    if isinstance(attr, DocumentAttributeFilename)
                ),
                message.file.name if (message.file and message.file.name) else None,
            )
            or f'{get_default_filename()}.{mime_type or "unknown"}'
        )
    else:
        original_filename = (
            message.file.name
            if (message.file and message.file.name)
            else f'{get_default_filename()}{message.file.ext}'
        )

    original_ext = Path(original_filename).suffix or f'.{mime_type}' if mime_type else '.unknown'
    if not new_filename:
        return Path(original_filename)

    new_filename_with_ext = Path(new_filename)
    if original_ext and new_filename_with_ext.suffix != original_ext:
        new_filename_with_ext = new_filename_with_ext.with_suffix(original_ext)
    return new_filename_with_ext


async def download_file(
    event: NewMessage.Event | CallbackQuery.Event,
    temp_file: _TemporaryFileWrapper | BufferedRandom | BufferedWriter,
    reply_message: Message,
    progress_message: Message,
) -> Path:
    if reply_message.document:
        await fast_download_file(
            event.client,
            reply_message.document,
            temp_file,
            progress_callback=lambda current, total: progress_callback(
                current, total, progress_message, t('downloading')
            ),
        )
    else:
        await reply_message.download_media(
            file=temp_file,
            progress_callback=lambda current, total: progress_callback(
                current, total, progress_message, t('downloading')
            ),
        )
    return Path(temp_file.name)


@asynccontextmanager
async def download_to_temp_file(
    event: NewMessage.Event | CallbackQuery.Event,
    reply_message: Message,
    progress_message: Message,
    *,
    suffix: str | None = None,
    temp_dir: Path = TMP_DIR,
) -> AsyncIterator[Path]:
    if suffix is None:
        suffix = reply_message.file.ext if reply_message.file else ''
    with NamedTemporaryFile(dir=temp_dir, suffix=suffix) as temp_file:
        temp_file_path = await download_file(event, temp_file, reply_message, progress_message)
        yield temp_file_path


async def upload_file(
    event: NewMessage.Event | CallbackQuery.Event,
    output_file: Path,
    progress_message: Message,
    is_voice: bool = False,
    force_document: bool = False,
    caption: str = '',
    **kwargs: Any,
) -> None:
    with output_file.open('rb') as file_to_upload:
        uploaded_file = await fast_upload_file(
            event.client,
            file_to_upload,
            output_file.name,
            progress_callback=lambda current, total: progress_callback(
                current, total, progress_message, t('uploading')
            ),
        )
    await event.client.send_file(
        event.chat_id,
        file=uploaded_file,
        force_document=force_document,
        caption=caption if caption else None,
        voice_note=is_voice,
        reply_to=event.message.id if isinstance(event, NewMessage.Event) else None,
        **kwargs,
    )


async def upload_file_and_cleanup(
    event: NewMessage.Event | CallbackQuery.Event,
    output_file: Path,
    progress_message: Message,
    *,
    unlink: bool = True,
    **kwargs: Any,
) -> None:
    await upload_file(event, output_file, progress_message, **kwargs)
    if unlink:
        output_file.unlink(missing_ok=True)


def get_filename_from_url(url: str) -> str:
    filename = Path(parse.urlparse(url).path).name
    return filename if filename else str(uuid4())
