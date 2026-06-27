from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from io import BufferedRandom, BufferedWriter
from os import fsync
from pathlib import Path
from tempfile import NamedTemporaryFile, _TemporaryFileWrapper
from typing import Any
from urllib import parse
from uuid import uuid4

import pymupdf
from telethon.events import CallbackQuery, NewMessage
from telethon.tl.custom import Message
from telethon.tl.types import DocumentAttributeFilename

from src import TMP_DIR
from src.utils.fast_telethon import download_file as fast_download_file
from src.utils.fast_telethon import upload_file as fast_upload_file
from src.utils.i18n import t
from src.utils.progress import progress_callback

PDF_THUMBNAIL_MAX_SIDE = 320
PDF_THUMBNAIL_MAX_SIZE = 200_000


def get_default_filename() -> str:
    return f'{datetime.now(UTC).strftime("%Y-%m-%d_%H-%M-%S")}'


def prepare_pdf_thumbnail(input_file: Path, output_file: Path) -> bool:
    if input_file.suffix.lower() != '.pdf':
        return False

    with suppress(Exception), pymupdf.open(input_file) as doc:
        if not doc.page_count:
            return False
        page = doc[0]
        scale = min(
            PDF_THUMBNAIL_MAX_SIDE / page.rect.width, PDF_THUMBNAIL_MAX_SIDE / page.rect.height, 1
        )
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
        output_file.write_bytes(pixmap.tobytes('jpg'))
        if 0 < output_file.stat().st_size <= PDF_THUMBNAIL_MAX_SIZE:
            return True

        for quality in (95, 85, 75, 65, 55, 45, 35, 25):
            output_file.write_bytes(pixmap.tobytes('jpg', jpg_quality=quality))
            if 0 < output_file.stat().st_size <= PDF_THUMBNAIL_MAX_SIZE:
                return True

    output_file.unlink(missing_ok=True)
    return False


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


async def resolve_upload_caption(
    event: NewMessage.Event | CallbackQuery.Event, output_file: Path, caption: str = ''
) -> str:
    if caption:
        return caption
    message = await event.get_message() if isinstance(event, CallbackQuery.Event) else event.message
    reply_message = await message.get_reply_message() if message and message.is_reply else None
    if getattr(reply_message, 'out', False):
        return f'<code>{output_file.name}</code>'
    if reply_caption := getattr(reply_message, 'raw_text', ''):
        return reply_caption
    if (reply_file := getattr(reply_message, 'file', None)) and (
        file_name := getattr(reply_file, 'name', '')
    ):
        return f'<code>{file_name}</code>'
    return f'<code>{output_file.name}</code>'


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
        temp_file.flush()
        fsync(temp_file.fileno())
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
    temp_thumb = (
        output_file.with_name(f'{output_file.stem}_thumb_{uuid4().hex}.jpg')
        if 'thumb' not in kwargs
        else None
    )
    if temp_thumb and prepare_pdf_thumbnail(output_file, temp_thumb):
        kwargs['thumb'] = str(temp_thumb)
    with output_file.open('rb') as file_to_upload:
        uploaded_file = await fast_upload_file(
            event.client,
            file_to_upload,
            output_file.name,
            progress_callback=lambda current, total: progress_callback(
                current, total, progress_message, t('uploading')
            ),
        )
    try:
        await event.client.send_file(
            event.chat_id,
            file=uploaded_file,
            force_document=force_document,
            caption=await resolve_upload_caption(event, output_file, caption),
            voice_note=is_voice,
            reply_to=event.message.id if isinstance(event, NewMessage.Event) else None,
            **kwargs,
        )
    finally:
        if temp_thumb:
            temp_thumb.unlink(missing_ok=True)


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
        thumb = kwargs.get('thumb')
        if isinstance(thumb, str | Path):
            Path(thumb).unlink(missing_ok=True)


def get_filename_from_url(url: str) -> str:
    filename = Path(parse.unquote(parse.urlparse(url).path)).name
    return filename if filename else str(uuid4())
