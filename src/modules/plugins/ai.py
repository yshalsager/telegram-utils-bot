import logging
from asyncio import sleep
from os import getenv
from pathlib import Path
from shutil import rmtree
from tempfile import NamedTemporaryFile
from time import time
from typing import ClassVar
from uuid import uuid4

import llm
import pymupdf
import regex as re
from telethon.events import CallbackQuery, NewMessage

from src import TMP_DIR
from src.modules.base import ModuleBase
from src.utils.command import Command
from src.utils.downloads import download_file, get_download_name, upload_file
from src.utils.filters import has_pdf_file, has_photo_or_photo_file
from src.utils.i18n import t
from src.utils.telegram import get_reply_message, send_progress_message

OCR_MODEL = 'gemini-2.5-flash'
OCR_MODEL_RPM = 10
OCR_PROMPT = (
    'OCR this PDF page. DONt REMOVE ARABIC Taskheel. '
    'NO text modifications. NO entries from you. '
    'Add \n\n between each paragraph. '
    'Correct spelling and punctuations if there are any problems with them.'
)


logger = logging.getLogger(__name__)


class RateLimiter:
    """Simple rate limiter to control API request rates."""

    def __init__(self, max_requests_per_minute: int) -> None:
        self.max_requests_per_minute = max_requests_per_minute
        self.request_timestamps: list[float] = []

    async def wait_if_needed(self) -> None:
        """Wait if the current request would exceed the rate limit."""
        current_time = time()
        # Remove timestamps older than 1 minute
        self.request_timestamps = [ts for ts in self.request_timestamps if current_time - ts < 60]

        # If we've reached the limit, wait until we can make another request
        if len(self.request_timestamps) >= self.max_requests_per_minute:
            oldest_timestamp = self.request_timestamps[0]
            wait_time = 60 - (current_time - oldest_timestamp)
            if wait_time > 0:
                logger.info(f'Rate limit reached. Waiting for {wait_time:.2f} seconds...')
                await sleep(wait_time)

        # Add the current request timestamp
        self.request_timestamps.append(time())


rate_limiter = RateLimiter(max_requests_per_minute=OCR_MODEL_RPM)
max_retries = 3


async def gemini_ocr_pdf(event: NewMessage.Event | CallbackQuery.Event) -> None:
    api_key = getenv('LLM_GEMINI_KEY')
    if not api_key:
        await event.reply(f'{t("missing_api_key")}: <code>LLM_GEMINI_KEY</code>')
        return
    try:
        model = llm.get_async_model(OCR_MODEL)
    except llm.UnknownModelError:
        await event.reply(f'{t("invalid_model")}: <code>{OCR_MODEL}</code>')
        return

    reply_message = await get_reply_message(event, previous=True)
    status_message = await send_progress_message(event, t('starting_process'))
    progress_message = await send_progress_message(event, t('performing_ocr'))
    output_dir = Path(TMP_DIR / str(uuid4()))
    output_dir.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=output_dir, suffix=reply_message.file.ext) as temp_file:
        temp_file_path = await download_file(event, temp_file, reply_message, progress_message)
        with pymupdf.open(temp_file_path) as doc:
            total_pages = doc.page_count
            await progress_message.edit(t('converting_pdf_to_images'))
            for idx, page in enumerate(doc, start=1):
                (output_dir / f'{str(idx).zfill(5)}.png').write_bytes(
                    page.get_pixmap().tobytes('png')
                )
        output_file = temp_file_path.with_suffix('.txt')
        with output_file.open('w') as out:
            for idx, page in enumerate(sorted(output_dir.glob('*.png')), start=1):
                retry_count = 0
                backoff_time = 10
                try:
                    await rate_limiter.wait_if_needed()
                    while retry_count <= max_retries:
                        response = await model.prompt(
                            OCR_PROMPT, attachments=[llm.Attachment(path=str(page))]
                        )
                        out.write(await response.text() + '\n\n')
                        break
                except llm.errors.ModelError as e:
                    retry_count += 1
                    if 'The model is overloaded' in str(e) and retry_count <= max_retries:
                        await sleep(backoff_time)
                        backoff_time *= 2
                    else:
                        logger.error(f'Failed to process page {idx}: {e}')
                        out.write(f'[Error processing page {idx}]\n\n')
                        break

                if idx % 10 == 0:
                    await progress_message.edit(f'<pre>{idx} / {total_pages}</pre>')

        output_file = output_file.rename(
            output_file.with_stem(get_download_name(reply_message).stem)
        )
        await upload_file(event, output_file, progress_message)

    await status_message.edit(t('pdf_ocr_process_completed'))
    rmtree(output_dir, ignore_errors=True)


class AI(ModuleBase):
    name = 'AI'
    description = t('_ai_module_description')
    commands: ClassVar[ModuleBase.CommandsT] = {
        'gemini ocr': Command(
            name='ocr',
            handler=gemini_ocr_pdf,
            description=t('_gemini_ocr_description'),
            pattern=re.compile(r'^/(gemini)\s+(ocr)$'),
            condition=lambda e, m: (has_pdf_file(e, m) or has_photo_or_photo_file(e, m)),
            is_applicable_for_reply=True,
        ),
    }
