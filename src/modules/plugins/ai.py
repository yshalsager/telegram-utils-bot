import logging
from asyncio import sleep
from functools import partial
from mimetypes import guess_type
from os import getenv
from pathlib import Path
from shutil import rmtree
from time import time
from typing import ClassVar
from uuid import uuid4

import llm
import pymupdf
import regex as re
from telethon.events import CallbackQuery, NewMessage
from telethon.tl.custom import Message

from src import TMP_DIR
from src.modules.base import ModuleBase
from src.utils.command import Command
from src.utils.downloads import download_to_temp_file, get_download_name, upload_file_and_cleanup
from src.utils.filters import has_media, has_pdf_file, has_photo_or_photo_file
from src.utils.i18n import t
from src.utils.run import run_command
from src.utils.telegram import (
    delete_message_after,
    edit_or_send_as_file,
    get_reply_message,
    inline_choice_grid,
    send_progress_message,
)

OCR_MODEL = 'gemini-2.5-flash'
OCR_MODEL_RPM = 10
GEMINI_MODELS = ['gemini-2.5-flash', 'gemini-2.5-pro', 'gemini-2.5-flash-lite']
OCR_PROMPT = (
    'OCR this PDF page. DONt REMOVE ARABIC Taskheel. '
    'NO text modifications. NO entries from you. '
    'Add \n\n between each paragraph. '
    'Correct spelling and punctuations if there are any problems with them.'
)

GEMINI_OCR_PATTERN = re.compile(r'^/(gemini)\s+(ocr)$')
GEMINI_TRANSCRIBE_PATTERN = re.compile(r'^/(gemini)\s+(transcribe)(?:\s+([a-zA-Z-]+))?$')
GEMINI_PROMPT_PATTERN = re.compile(r'^/(gemini)\s+(prompt)$')
PROMPT_TEXT_PATTERN = re.compile(r'(?s)^(.+)$')


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


async def get_message_for_processing(event: NewMessage.Event | CallbackQuery.Event) -> Message:
    if isinstance(event, CallbackQuery.Event):
        return await get_reply_message(event, previous=True)
    return (
        await get_reply_message(event, previous=True) if event.message.is_reply else event.message
    )


async def get_gemini_model(
    event: NewMessage.Event | CallbackQuery.Event, model_name: str
) -> llm.AsyncModel | None:
    if not getenv('LLM_GEMINI_KEY'):
        await event.reply(f'{t("missing_api_key")}: <code>LLM_GEMINI_KEY</code>')
        return None
    try:
        return llm.get_async_model(model_name)
    except llm.UnknownModelError:
        await event.reply(f'{t("invalid_model")}: <code>{model_name}</code>')
        return None


async def choose_gemini_model(
    event: NewMessage.Event | CallbackQuery.Event, *, prefix: str
) -> str | None:
    if not isinstance(event, CallbackQuery.Event):
        return OCR_MODEL
    return await inline_choice_grid(
        event,
        prefix=prefix,
        prompt_text=f'{t("choose_model")}:',
        pairs=[
            (
                name.replace('gemini-', '').replace('-', ' ').title(),
                f'{prefix}{name}',
            )
            for name in GEMINI_MODELS
        ],
        cols=2,
        cast=str,
    )


def get_message_mime_type(message: Message) -> str | None:
    if message.file and message.file.mime_type:
        return message.file.mime_type or ''
    if message.file and message.file.name:
        return guess_type(message.file.name)[0]
    if message.file and message.file.ext:
        return guess_type(f'x{message.file.ext}')[0]
    return None


async def gemini_ocr_pdf(event: NewMessage.Event | CallbackQuery.Event) -> None:
    model_name = await choose_gemini_model(event, prefix='m|gemini_ocr|model|')
    if model_name is None:
        return

    model = await get_gemini_model(event, model_name)
    if not model:
        return

    reply_message = await get_message_for_processing(event)
    mime_type = reply_message.file.mime_type if reply_message.file else None
    status_message = await send_progress_message(event, t('starting_process'))
    progress_message = await send_progress_message(event, t('performing_ocr'))
    output_dir = Path(TMP_DIR / str(uuid4()))
    output_dir.mkdir(parents=True, exist_ok=True)

    async with download_to_temp_file(
        event,
        reply_message,
        progress_message,
        suffix=reply_message.file.ext,
        temp_dir=output_dir,
    ) as temp_file_path:
        if not mime_type:
            mime_type = guess_type(temp_file_path)[0]
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
        await upload_file_and_cleanup(event, output_file, progress_message)

    await status_message.edit(t('pdf_ocr_process_completed'))
    rmtree(output_dir, ignore_errors=True)


async def gemini_transcribe_media(event: NewMessage.Event | CallbackQuery.Event) -> None:
    model_name = await choose_gemini_model(event, prefix='m|gemini_transcribe|model|')
    if model_name is None:
        return

    model = await get_gemini_model(event, model_name)
    if not model:
        return

    input_message = await get_message_for_processing(event)
    language = 'ar'
    if isinstance(event, NewMessage.Event) and event.message.text:
        match = GEMINI_TRANSCRIBE_PATTERN.match(event.message.text)
        language = (match.group(3) if match else 'ar') or 'ar'

    status_message = await send_progress_message(event, t('starting_transcription'))
    progress_message = await send_progress_message(event, f'<pre>{t("process_output")}:</pre>')
    output_dir = Path(TMP_DIR / str(uuid4()))
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        async with download_to_temp_file(
            event, input_message, progress_message, temp_dir=output_dir
        ) as input_file_path:
            audio_file_path = input_file_path
            attachment_type = input_message.file.mime_type if input_message.file else None
            if (
                input_message.video
                or input_message.video_note
                or input_file_path.suffix.lower()
                not in ('.ogg', '.oga', '.opus', '.mp3', '.wav', '.aac', '.flac')
            ):
                audio_file_path = input_file_path.with_suffix('.ogg')
                attachment_type = 'audio/ogg'
                await progress_message.edit(t('converting_media'))
                output, status_code = await run_command(
                    f'ffmpeg -hide_banner -y -i "{input_file_path}" -vn -ac 1 -c:a libopus -b:a 32k "{audio_file_path}"'
                )
                if status_code != 0:
                    await status_message.edit(
                        t('an_error_occurred', error=f'\n<pre>{output}</pre>')
                    )
                    return

            if not attachment_type:
                attachment_type = guess_type(audio_file_path)[0]
            if not attachment_type:
                await status_message.edit(t('unsupported_file_type'))
                return

            await progress_message.edit(t('starting_transcription'))
            response = await model.prompt(
                f'Transcribe this audio into {language}. Output only the transcription.',
                attachments=[llm.Attachment(type=attachment_type, path=str(audio_file_path))],
            )
            transcription = await response.text()
            edited = await edit_or_send_as_file(
                event,
                status_message,
                transcription,
                file_name=f'{get_download_name(input_message).stem}.txt',
            )
            if not edited:
                await status_message.edit(t('transcription_completed'))
    finally:
        delete_message_after(progress_message)
        rmtree(output_dir, ignore_errors=True)


async def run_gemini_custom_prompt(
    event: NewMessage.Event,
    input_message: Message | None,
    match: re.Match,
    *,
    model_name: str,
) -> None:
    if not input_message:
        await event.reply(t('unsupported_file_type'))
        return

    prompt = match.group(1).strip()
    if not prompt:
        await event.reply(t('invalid_prompt'))
        return

    model = await get_gemini_model(event, model_name)
    if not model:
        return

    mime_type = get_message_mime_type(input_message)
    if not mime_type or mime_type not in model.attachment_types:
        await event.reply(t('unsupported_file_type'))
        return
    status_message = await send_progress_message(event, t('starting_process'))
    progress_message = await send_progress_message(event, t('downloading'))
    output_dir = Path(TMP_DIR / str(uuid4()))
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        async with download_to_temp_file(
            event,
            input_message,
            progress_message,
            suffix=input_message.file.ext,
            temp_dir=output_dir,
        ) as input_file_path:
            await progress_message.edit(t('starting_process'))
            response = await model.prompt(
                prompt,
                attachments=[llm.Attachment(type=mime_type, path=str(input_file_path))],
            )
            text = await response.text()
            await edit_or_send_as_file(
                event,
                status_message,
                text,
                file_name=f'{get_download_name(input_message).stem}.txt',
            )
    finally:
        delete_message_after(progress_message)
        rmtree(output_dir, ignore_errors=True)


async def gemini_prompt_with_file(event: NewMessage.Event | CallbackQuery.Event) -> None:
    input_message = await get_message_for_processing(event)
    if isinstance(event, CallbackQuery.Event):
        model_name = await choose_gemini_model(event, prefix='m|gemini_prompt|model|')
        if model_name is None:
            return
        model = await get_gemini_model(event, model_name)
        if not model:
            return

        mime_type = get_message_mime_type(input_message)
        if not mime_type or mime_type not in model.attachment_types:
            await event.reply(t('unsupported_file_type'))
            return
        await event.client.reply_prompts.ask(
            event,
            t('send_prompt'),
            pattern=PROMPT_TEXT_PATTERN,
            handler=partial(run_gemini_custom_prompt, model_name=model_name),
            invalid_reply_text=t('invalid_prompt'),
            media_message_id=input_message.id,
        )
        return

    await event.reply(t('use_inline_prompt'))


class AI(ModuleBase):
    name = 'AI'
    description = t('_ai_module_description')
    commands: ClassVar[ModuleBase.CommandsT] = {
        'gemini ocr': Command(
            handler=gemini_ocr_pdf,
            description=t('_gemini_ocr_description'),
            pattern=GEMINI_OCR_PATTERN,
            condition=lambda e, m: (has_pdf_file(e, m) or has_photo_or_photo_file(e, m)),
            is_applicable_for_reply=True,
        ),
        'gemini transcribe': Command(
            handler=gemini_transcribe_media,
            description=t('_gemini_transcribe_description'),
            pattern=GEMINI_TRANSCRIBE_PATTERN,
            condition=lambda e, m: has_media(e, m, any=True),
            is_applicable_for_reply=True,
        ),
        'gemini prompt': Command(
            handler=gemini_prompt_with_file,
            description=t('_gemini_prompt_description'),
            pattern=GEMINI_PROMPT_PATTERN,
            condition=lambda e, m: has_media(e, m, any=True),
            is_applicable_for_reply=True,
        ),
    }
