import logging
from asyncio import sleep
from collections.abc import Awaitable, Callable
from functools import partial
from mimetypes import guess_type
from os import getenv
from pathlib import Path
from shutil import rmtree
from time import time
from typing import Any, ClassVar
from uuid import uuid4

import aiohttp
import llm
import llm_gemini
import pymupdf
import regex as re
from telethon.events import CallbackQuery, NewMessage
from telethon.tl.custom import Message

from src import TMP_DIR
from src.modules.base import ModuleBase
from src.modules.plugins.media import get_format_info, get_stream_info
from src.utils.command import Command
from src.utils.downloads import download_to_temp_file, get_download_name, upload_file_and_cleanup
from src.utils.filters import has_file, has_media, has_pdf_file, has_photo_or_photo_file
from src.utils.i18n import t
from src.utils.run import run_command
from src.utils.telegram import (
    delete_message_after,
    edit_or_send_as_file,
    get_reply_message,
    inline_choice_grid,
    send_progress_message,
)

OCR_MODEL = 'gemini-3.1-flash-lite-preview'
OCR_MODEL_RPM = 10
GEMINI_MODELS: list[str] = [
    'gemini-3.1-flash-lite-preview',
    'gemini-3-flash-preview',
    'gemini-2.5-flash-lite',
    'gemini-2.5-flash',
    'gemini-flash-lite-latest',
    'gemini-flash-latest',
    'gemma-4-31b-it',
    'gemma-4-26b-a4b-it',
]
OCR_PROMPT = (
    'OCR this PDF page. DONt REMOVE ARABIC Taskheel. '
    'NO text modifications. NO entries from you. '
    'Add \n\n between each paragraph. '
    'Correct spelling and punctuations if there are any problems with them.'
)
GEMINI_TRANSCRIBE_CHUNK_SECONDS = 30 * 60
GEMINI_FILES_API_BASE = 'https://generativelanguage.googleapis.com'

GEMINI_OCR_PATTERN = re.compile(r'^/(gemini)\s+(ocr)$')
GEMINI_TRANSCRIBE_PATTERN = re.compile(r'^/(gemini)\s+(transcribe)(?:\s+([a-zA-Z-]+))?$')
GEMINI_PROMPT_PATTERN = re.compile(r'^/(gemini)\s+(prompt)$')
PROMPT_TEXT_PATTERN = re.compile(r'(?s)^(.+)$')


logger = logging.getLogger(__name__)


class GeminiQuotaExceededError(RuntimeError):
    pass


def patch_llm_gemini_file_uris() -> None:
    if getattr(llm_gemini._SharedGemini, '_files_api_uri_patch', False):
        return

    original_build_attachment_part = llm_gemini._SharedGemini._build_attachment_part

    def _build_attachment_part(self: Any, attachment: Any, mime_type: str) -> dict[str, Any]:
        if attachment.url and '/v1beta/files/' in attachment.url:
            return {'fileData': {'mimeType': mime_type, 'fileUri': attachment.url}}
        return original_build_attachment_part(self, attachment, mime_type)

    llm_gemini._SharedGemini._build_attachment_part = _build_attachment_part  # type: ignore[assignment]
    llm_gemini._SharedGemini._files_api_uri_patch = True  # type: ignore[attr-defined]


patch_llm_gemini_file_uris()


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


async def prompt_with_gemini_file_attachment(
    model: llm.AsyncModel, prompt: str, chunk_path: Path, api_key: str
) -> str:
    num_bytes = chunk_path.stat().st_size
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f'{GEMINI_FILES_API_BASE}/upload/v1beta/files?key={api_key}',
            headers={
                'X-Goog-Upload-Protocol': 'resumable',
                'X-Goog-Upload-Command': 'start',
                'X-Goog-Upload-Header-Content-Length': str(num_bytes),
                'X-Goog-Upload-Header-Content-Type': 'audio/ogg',
                'Content-Type': 'application/json',
            },
            json={'file': {'display_name': chunk_path.name}},
        ) as response:
            response.raise_for_status()
            upload_url = response.headers.get('x-goog-upload-url')
            if not upload_url:
                raise RuntimeError('Gemini Files API upload URL was not returned')

        async with session.post(
            upload_url,
            headers={
                'Content-Length': str(num_bytes),
                'X-Goog-Upload-Offset': '0',
                'X-Goog-Upload-Command': 'upload, finalize',
            },
            data=chunk_path.read_bytes(),
        ) as response:
            response.raise_for_status()
            file_info = (await response.json()).get('file') or {}
            file_uri = file_info.get('uri')
            file_name = file_info.get('name')
            if not file_uri or not file_name:
                raise RuntimeError('Gemini Files API response missing file uri/name')

        try:
            for _ in range(60):
                async with session.get(
                    f'{GEMINI_FILES_API_BASE}/v1beta/{file_name}?key={api_key}'
                ) as response:
                    response.raise_for_status()
                    payload = await response.json()
                state = str((payload.get('file') or payload).get('state') or '').upper()
                if state == 'ACTIVE':
                    break
                if state in ('FAILED', 'ERROR'):
                    raise RuntimeError(f'Gemini file processing failed: {state}')
                await sleep(2)
            else:
                raise RuntimeError('Timed out waiting for Gemini uploaded file to become active')

            response = await call_gemini_with_retries(
                operation=f'transcription chunk file {chunk_path.name}',
                action=lambda: model.prompt(
                    prompt,
                    attachments=[llm.Attachment(type='audio/ogg', url=file_uri)],
                ),
            )
            return (await response.text()).strip()
        finally:
            async with session.delete(
                f'{GEMINI_FILES_API_BASE}/v1beta/{file_name}?key={api_key}'
            ) as response:
                if response.status >= 400:
                    logger.warning(
                        f'Failed to delete Gemini file {file_name}: HTTP {response.status}'
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
    model_name = await inline_choice_grid(
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
    if model_name and model_name not in GEMINI_MODELS:
        await event.reply(f'{t("invalid_model")}: <code>{model_name}</code>')
        return None
    return model_name


def get_message_mime_type(message: Message) -> str | None:
    if message.file and message.file.mime_type:
        return message.file.mime_type or ''
    if message.file and message.file.name:
        return guess_type(message.file.name)[0]
    if message.file and message.file.ext:
        return guess_type(f'x{message.file.ext}')[0]
    return None


def has_corrupt_media_error(output: str) -> bool:
    text = output.lower()
    return any(
        marker in text
        for marker in (
            'invalid data found when processing input',
            'error reading header',
            'stsz atom truncated',
            'contradictionary stsc and stco',
            'moov atom not found',
        )
    )


def is_retryable_gemini_error(error: Exception) -> bool:
    text = str(error).lower()
    return any(
        marker in text
        for marker in (
            'the model is overloaded',
            'high demand',
            'rate limit',
            '429',
            'quota',
            'exceeded your current quota',
            'free_tier_requests',
            'resource exhausted',
            'temporarily unavailable',
            'service unavailable',
            'internal',
            'timeout',
            'deadline exceeded',
        )
    )


def get_gemini_retry_after_seconds(error: Exception) -> float | None:
    text = str(error)
    if match := re.search(r'Please retry in\s+(\d+(?:\.\d+)?)s', text, re.IGNORECASE):
        return float(match.group(1))
    if match := re.search(r'retry in\s+(\d+(?:\.\d+)?)\s*seconds?', text, re.IGNORECASE):
        return float(match.group(1))
    return None


def is_quota_exceeded_gemini_error(error: Exception) -> bool:
    text = str(error).lower()
    return any(
        marker in text
        for marker in (
            'exceeded your current quota',
            'quota exceeded',
            'free_tier_requests',
            'resource exhausted',
        )
    )


async def call_gemini_with_retries[T](*, operation: str, action: Callable[[], Awaitable[T]]) -> T:
    retry_count = 0
    backoff_time = 10.0
    while True:
        try:
            await rate_limiter.wait_if_needed()
            return await action()
        except Exception as e:
            retry_count += 1
            if retry_count <= max_retries and is_retryable_gemini_error(e):
                retry_after = get_gemini_retry_after_seconds(e)
                wait_seconds = max(backoff_time, retry_after or 0)
                logger.warning(
                    f'Retrying {operation} in {wait_seconds:.1f}s '
                    f'({retry_count}/{max_retries}) after: {e}'
                )
                await sleep(wait_seconds)
                backoff_time *= 2
                continue
            if is_quota_exceeded_gemini_error(e):
                raise GeminiQuotaExceededError(str(e)) from e
            raise


async def media_preflight_ok(input_file_path: Path) -> bool:
    try:
        video_info = await get_stream_info('v:0', input_file_path)
        audio_info = await get_stream_info('a:0', input_file_path)
        format_info = await get_format_info(input_file_path)
        return bool(video_info or audio_info or format_info)
    except Exception as e:  # noqa: BLE001
        logger.warning(f'ffprobe preflight failed for {input_file_path}: {e}')
        return False


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
        quota_exceeded_error: GeminiQuotaExceededError | None = None
        with output_file.open('w') as out:
            for idx, page in enumerate(sorted(output_dir.glob('*.png')), start=1):
                try:
                    response = await call_gemini_with_retries(
                        operation=f'OCR page {idx}/{total_pages}',
                        action=lambda _page=page: model.prompt(
                            OCR_PROMPT, attachments=[llm.Attachment(path=str(_page))]
                        ),
                    )
                    out.write(await response.text() + '\n\n')
                except GeminiQuotaExceededError as e:
                    quota_exceeded_error = e
                    logger.error(f'Gemini quota exhausted during OCR page {idx}: {e}')
                    out.write(f'[Stopped at page {idx} due to Gemini quota limits]\n\n')
                    break
                except Exception as e:  # noqa: BLE001
                    logger.error(f'Failed to process page {idx}: {e}')
                    out.write(f'[Error processing page {idx}]\n\n')

                if idx % 10 == 0:
                    await progress_message.edit(f'<pre>{idx} / {total_pages}</pre>')

        output_file = output_file.rename(
            output_file.with_stem(get_download_name(reply_message).stem)
        )
        await upload_file_and_cleanup(event, output_file, progress_message)

    if quota_exceeded_error:
        await status_message.edit(t('gemini_quota_exceeded_partial'))
    else:
        await status_message.edit(t('pdf_ocr_process_completed'))
    rmtree(output_dir, ignore_errors=True)


async def gemini_transcribe_media(  # noqa: C901, PLR0911, PLR0912, PLR0915
    event: NewMessage.Event | CallbackQuery.Event,
) -> None:
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
            if not await media_preflight_ok(input_file_path):
                await status_message.edit(t('corrupt_media_file'))
                return
            audio_file_path = output_dir / 'gemini-input.ogg'
            await progress_message.edit(t('converting_media'))
            output, status_code = await run_command(
                f'ffmpeg -hide_banner -y -i "{input_file_path}" -vn -ac 1 -c:a libopus -b:a 32k "{audio_file_path}"'
            )
            if status_code != 0:
                if has_corrupt_media_error(output):
                    await status_message.edit(t('corrupt_media_file'))
                    return
                await status_message.edit(t('an_error_occurred', error=f'\n<pre>{output}</pre>'))
                return

            chunk_pattern = output_dir / 'gemini-part-%03d.ogg'
            output, status_code = await run_command(
                f'ffmpeg -hide_banner -y -i "{audio_file_path}" -vn -ac 1 -c:a libopus -b:a 32k '
                f'-f segment -segment_time {GEMINI_TRANSCRIBE_CHUNK_SECONDS} -reset_timestamps 1 "{chunk_pattern}"'
            )
            if status_code != 0:
                if has_corrupt_media_error(output):
                    await status_message.edit(t('corrupt_media_file'))
                    return
                await status_message.edit(t('an_error_occurred', error=f'\n<pre>{output}</pre>'))
                return
            audio_parts = sorted(
                path for path in output_dir.glob('gemini-part-*.ogg') if path.stat().st_size
            )
            if not audio_parts:
                await status_message.edit(t('unsupported_file_type'))
                return

            await progress_message.edit(t('starting_transcription'))
            transcription_parts = []
            chunk_count = len(audio_parts)
            total_chars = 0
            empty_chunks = 0
            quota_exceeded_partial = False
            api_key = model.get_key() or getenv('LLM_GEMINI_KEY') or ''
            if not api_key:
                await status_message.edit(f'{t("missing_api_key")}: <code>LLM_GEMINI_KEY</code>')
                return
            for idx, chunk_path in enumerate(audio_parts, start=1):
                if chunk_count > 1:
                    await progress_message.edit(f'<pre>{idx} / {chunk_count}</pre>')
                prompt = f'Transcribe this audio into {language}. Output only the transcription.'
                if chunk_count > 1:
                    prompt = (
                        f'Transcribe this audio chunk {idx} of {chunk_count} into {language}. '
                        'Output only the transcription for this chunk.'
                    )
                try:
                    part = await prompt_with_gemini_file_attachment(
                        model, prompt, chunk_path, api_key
                    )
                except GeminiQuotaExceededError:
                    if not transcription_parts:
                        await status_message.edit(t('gemini_quota_exceeded'))
                        return
                    quota_exceeded_partial = True
                    await status_message.edit(t('gemini_quota_exceeded_partial'))
                    break
                except Exception as e:  # noqa: BLE001
                    await status_message.edit(t('an_error_occurred', error=f'\n<pre>{e!s}</pre>'))
                    return
                if part:
                    transcription_parts.append(part)
                    part_chars = len(part)
                    total_chars += part_chars
                else:
                    part_chars = 0
                    empty_chunks += 1
                await progress_message.edit(
                    f'<pre>{idx} / {chunk_count} | chars:{part_chars} | total:{total_chars} | empty:{empty_chunks}</pre>'
                )
            transcription = '\n\n'.join(transcription_parts)
            edited = await edit_or_send_as_file(
                event,
                status_message,
                transcription,
                file_name=f'{get_download_name(input_message).stem}.txt',
            )
            if not edited:
                await status_message.edit(
                    t('gemini_quota_exceeded_partial')
                    if quota_exceeded_partial
                    else t('transcription_completed')
                )
    finally:
        delete_message_after(progress_message)
        rmtree(output_dir, ignore_errors=True)


async def run_gemini_custom_prompt(
    event: NewMessage.Event,
    input_message: Message | None,
    match: Any,
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
            try:
                response = await call_gemini_with_retries(
                    operation='gemini prompt',
                    action=lambda: model.prompt(
                        prompt,
                        attachments=[llm.Attachment(type=mime_type, path=str(input_file_path))],
                    ),
                )
            except GeminiQuotaExceededError:
                await status_message.edit(t('gemini_quota_exceeded'))
                return
            except Exception as e:  # noqa: BLE001
                await status_message.edit(t('an_error_occurred', error=f'\n<pre>{e!s}</pre>'))
                return
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
            condition=lambda e, m: has_pdf_file(e, m) or has_photo_or_photo_file(e, m),
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
            condition=has_file,
            is_applicable_for_reply=True,
        ),
    }
