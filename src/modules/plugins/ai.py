from functools import partial
from os import getenv
from pathlib import Path
from shutil import rmtree
from tempfile import NamedTemporaryFile
from typing import ClassVar
from uuid import uuid4

import llm
import pymupdf
import regex as re
from telethon.events import CallbackQuery, NewMessage
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random_exponential,
)

from src import TMP_DIR
from src.modules.base import CommandHandlerDict, ModuleBase, dynamic_handler
from src.utils.command import Command
from src.utils.downloads import download_file, get_download_name, upload_file
from src.utils.filters import has_pdf_file, has_photo_or_photo_file
from src.utils.i18n import t
from src.utils.run import run_subprocess_exec
from src.utils.telegram import get_reply_message

OCR_MODEL = 'gemini-2.0-flash-exp'
OCR_PROMPT = (
    'OCR this PDF page. DONt REMOVE ARABIC Taskheel. '
    'NO text modifications. NO entries from you. '
    'Add \n\n between each paragraph. '
    'Correct spelling and punctuations if there are any problems with them.'
)


@retry(
    retry=(retry_if_exception_type(llm.errors.ModelError)),
    wait=wait_random_exponential(multiplier=1, max=40),
    stop=stop_after_attempt(3),
)
async def perform_ocr(model: llm.AsyncModel, page: Path) -> llm.AsyncResponse:
    return await model.prompt(OCR_PROMPT, attachments=[llm.Attachment(path=str(page))])


async def gemini_ocr_pdf(event: NewMessage.Event | CallbackQuery.Event) -> None:
    api_key = getenv('LLM_GEMINI_KEY')
    if not api_key:
        await event.reply(f'{t("missing_api_key")}: <code>LLM_GEMINI_KEY</code>')
        return
    try:
        model = llm.get_async_model(OCR_MODEL)
    except llm.UnknownModelError:
        async for _ in run_subprocess_exec('uv run --no-project llm install llm-gemini'):
            pass
        model = llm.get_async_model(OCR_MODEL)
    if not model:
        return

    reply_message = await get_reply_message(event, previous=True)
    status_message = await event.reply(t('starting_process'))
    progress_message = await event.reply(t('performing_ocr'))
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
                response = await perform_ocr(model, page)
                out.write(await response.text() + '\n\n')
                if idx % 15 == 0:  # each 15 pages, update the progress message
                    await progress_message.edit(f'<pre>{idx} / {total_pages}</pre>')

        output_file = output_file.rename(
            output_file.with_stem(get_download_name(reply_message).stem)
        )
        await upload_file(event, output_file, progress_message)

    await status_message.edit(t('pdf_ocr_process_completed'))
    rmtree(output_dir, ignore_errors=True)


handlers: CommandHandlerDict = {
    'gemini ocr': gemini_ocr_pdf,
}

handler = partial(dynamic_handler, handlers)


class AI(ModuleBase):
    name = 'AI'
    description = t('_ai_module_description')
    commands: ClassVar[ModuleBase.CommandsT] = {
        'gemini ocr': Command(
            name='ocr',
            handler=handler,
            description=t('_gemini_ocr_description'),
            pattern=re.compile(r'^/(gemini)\s+(ocr)$'),
            condition=lambda e, m: (has_pdf_file(e, m) or has_photo_or_photo_file(e, m)),
            is_applicable_for_reply=True,
        ),
    }
