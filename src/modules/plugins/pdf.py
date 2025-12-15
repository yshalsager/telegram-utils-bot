from collections import defaultdict
from contextlib import suppress
from io import BytesIO
from os import getenv
from pathlib import Path
from shutil import rmtree
from tempfile import NamedTemporaryFile
from typing import Any, ClassVar
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

import pymupdf
import regex as re
from telethon import Button, TelegramClient
from telethon.events import CallbackQuery, NewMessage, StopPropagation
from telethon.tl.custom import Message

from src import TMP_DIR
from src.modules.base import ModuleBase
from src.modules.plugins.run import stream_shell_output
from src.utils.command import Command
from src.utils.downloads import (
    download_to_temp_file,
    get_download_name,
    upload_file_and_cleanup,
)
from src.utils.filters import has_pdf_file, has_photo_or_photo_file
from src.utils.i18n import t
from src.utils.reply import MergeState, StateT
from src.utils.telegram import delete_message_after, get_reply_message, send_progress_message

PDF_SAVE_KWARGS = {
    'garbage': 4,
    'deflate': True,
    'deflate_images': True,
    'deflate_fonts': False,
    'use_objstms': True,
}

merge_states: StateT = defaultdict[int, dict[str, Any]](
    lambda: {'state': MergeState.IDLE, 'files': []}
)


async def extract_pdf_text(event: NewMessage.Event | CallbackQuery.Event) -> None:
    reply_message = await get_reply_message(event, previous=True)
    progress_message = await send_progress_message(event, t('extracting_text_from_pdf'))

    async with download_to_temp_file(
        event,
        reply_message,
        progress_message,
        suffix='.pdf',
    ) as temp_file_path:
        output_file = temp_file_path.with_suffix('.txt')
        with pymupdf.open(temp_file_path) as doc, output_file.open('wb') as out:
            for page in doc:
                text = page.get_text().encode('utf8')
                out.write(text)
                # write page delimiter (form feed 0x0C)
                out.write(bytes((12,)))
        output_file = output_file.rename(
            output_file.with_stem(get_download_name(reply_message).stem)
        )
        await upload_file_and_cleanup(event, output_file, progress_message)
        await progress_message.delete()


async def merge_pdf_initial(event: NewMessage.Event | CallbackQuery.Event) -> None:
    merge_states[event.sender_id]['state'] = MergeState.COLLECTING
    merge_states[event.sender_id]['files'] = []
    reply_message = await get_reply_message(event, previous=True)
    merge_states[event.sender_id]['files'].append(reply_message.id)
    await event.reply(t('send_more_pdf_files_to_merge'))


async def merge_pdf_add(event: NewMessage.Event) -> None:
    merge_states[event.sender_id]['files'].append(event.id)
    await event.reply(t('file_added'), buttons=[Button.inline(t('finish'), 'finish_pdf_merge')])
    raise StopPropagation


async def merge_pdf_process(event: CallbackQuery.Event) -> None:
    merge_states[event.sender_id]['state'] = MergeState.MERGING
    files = merge_states[event.sender_id]['files']
    await event.answer(t('merging'))

    if len(files) < 2:
        await event.answer(t('not_enough_files'))
        merge_states[event.sender_id]['state'] = MergeState.IDLE
        return

    status_message = await event.respond(t('starting_merge'))
    progress_message = await event.respond(t('merging'))

    with pymupdf.open() as merged_pdf:
        for file_id in files:
            message = await event.client.get_messages(event.chat_id, ids=file_id)
            async with download_to_temp_file(
                event,
                message,
                progress_message,
                suffix='.pdf',
            ) as temp_file_path:
                with pymupdf.open(temp_file_path) as pdf_doc:
                    merged_pdf.insert_pdf(pdf_doc)
        with NamedTemporaryFile(dir=TMP_DIR, suffix='.pdf') as out_file:
            merged_pdf.save(out_file.name, **PDF_SAVE_KWARGS)
            output_file_path = Path(out_file.name)
            if output_file_path.exists() and output_file_path.stat().st_size:
                output_file_path = output_file_path.rename(
                    output_file_path.with_stem(f'merged_{Path(message.file.name).stem}')
                )
                await upload_file_and_cleanup(event, output_file_path, progress_message)
                await status_message.edit(t('merge_completed'))
            else:
                await status_message.edit(t('merge_failed'))

    await progress_message.delete()
    merge_states.pop(event.sender_id)


async def _split_pdf_process(
    event: NewMessage.Event,
    reply_message: Message | None,
    match: re.Match,
) -> None:
    assert reply_message is not None
    pages_count = int(match.group(1))
    progress_message = await send_progress_message(event, t('splitting_pdf'))

    async with download_to_temp_file(
        event,
        reply_message,
        progress_message,
        suffix='.pdf',
    ) as temp_file_path:
        with pymupdf.open(temp_file_path) as doc:
            total_pages = len(doc)
            split_size = total_pages // pages_count
            remainder = total_pages % pages_count

            for i in range(pages_count):
                start = i * split_size
                end = start + split_size + (1 if i < remainder else 0)

                with pymupdf.open() as new_doc:
                    new_doc.insert_pdf(doc, from_page=start, to_page=end - 1)
                    output_file = temp_file_path.with_name(
                        f'{Path(reply_message.file.name).stem}_{i + 1}.pdf'
                    )
                    new_doc.save(output_file, **PDF_SAVE_KWARGS)
                    await upload_file_and_cleanup(event, output_file, progress_message)

        await progress_message.edit(t('pdf_split_completed'))

    raise StopPropagation


async def split_pdf(event: NewMessage.Event | CallbackQuery.Event) -> None:
    if isinstance(event, CallbackQuery.Event):
        await event.client.reply_prompts.ask(
            event,
            f'{t("pdf_split_pages_number")}:',
            pattern=re.compile(r'^(\d+)$'),
            handler=_split_pdf_process,
            invalid_reply_text=t('invalid_pdf_split_pages_number'),
        )
        return

    reply_message = await get_reply_message(event, previous=True)
    if match := re.search(r'(\d+)', event.message.text):
        await _split_pdf_process(event, reply_message, match)
        return

    await event.reply(t('invalid_pdf_split_pages_number'))
    raise StopPropagation


def parse_page_numbers(input_string: str) -> list[int]:
    pages: set[int] = set()
    for part in re.split(r'[,\s]+', input_string):
        if '-' in part:
            with suppress(ValueError):
                start, end = map(int, part.split('-'))
                pages.update(range(start - 1, end))
        else:
            with suppress(ValueError):
                pages.add(int(part))
    return sorted(pages)


async def _extract_pdf_pages_process(
    event: NewMessage.Event,
    reply_message: Message | None,
    match: re.Match,
) -> None:
    assert reply_message is not None
    pages_input = match.group(1)
    pages_to_extract = parse_page_numbers(pages_input)
    if not pages_to_extract:
        await event.reply(t('invalid_pdf_extract_pages'))
        return

    progress_message = await send_progress_message(event, t('extracting_pdf_pages'))

    async with download_to_temp_file(
        event,
        reply_message,
        progress_message,
        suffix='.pdf',
    ) as temp_file_path:
        with pymupdf.open(temp_file_path) as doc:
            doc.select(pages_to_extract)
            output_file = temp_file_path.with_name(
                f'{Path(reply_message.file.name).stem}_extracted.pdf'
            )
            doc.save(output_file, **PDF_SAVE_KWARGS)
            await upload_file_and_cleanup(event, output_file, progress_message)

    await progress_message.edit(t('pdf_extraction_completed'))
    raise StopPropagation


async def extract_pdf_pages(event: NewMessage.Event | CallbackQuery.Event) -> None:
    if isinstance(event, CallbackQuery.Event):
        await event.client.reply_prompts.ask(
            event,
            f'{t("pdf_extract_pages")}:',
            pattern=re.compile(r'^(?=.*\d)([\d,\-\s]+)$'),
            handler=_extract_pdf_pages_process,
            invalid_reply_text=t('invalid_pdf_extract_pages'),
        )
        return

    reply_message = await get_reply_message(event, previous=True)
    pages_input = event.message.text.split(' ', 2)[-1]
    match = re.match(r'^(?=.*\d)([\d,\-\s]+)$', pages_input)
    if not match:
        await event.reply(t('invalid_pdf_extract_pages'))
        raise StopPropagation

    await _extract_pdf_pages_process(event, reply_message, match)
    return


async def convert_to_images(event: NewMessage.Event | CallbackQuery.Event) -> None:
    delete_message_after_process = False
    if isinstance(event, CallbackQuery.Event):
        if event.data.decode().startswith('m|pdf_images|'):
            output_format = event.data.decode().split('|')[-1]
            delete_message_after_process = True
        else:
            buttons = [
                [Button.inline('ZIP', 'm|pdf_images|ZIP'), Button.inline('PDF', 'm|pdf_images|PDF')]
            ]
            await event.edit(f'{t("choose_output_format")}:', buttons=buttons)
            return
    else:
        args = event.message.text.split('images')
        output_format = 'ZIP' if len(args) == 1 else args[-1]

    reply_message = await get_reply_message(event, previous=True)
    progress_message = await send_progress_message(event, t('converting_pdf_to_images'))

    async with download_to_temp_file(
        event,
        reply_message,
        progress_message,
        suffix='.pdf',
    ) as temp_file_path:
        if output_format == 'ZIP':
            zip_buffer = BytesIO()
            with (
                ZipFile(zip_buffer, 'w', ZIP_DEFLATED) as zip_file,
                pymupdf.open(temp_file_path) as doc,
            ):
                for page in doc:
                    zip_file.writestr(
                        f'page-{page.number}.jpg', page.get_pixmap().tobytes('jpg', jpg_quality=75)
                    )
            zip_buffer.seek(0)
            output_file = temp_file_path.with_name(
                f'{Path(reply_message.file.name).stem}_images.zip'
            )
            output_file.write_bytes(zip_buffer.getvalue())
        else:
            with pymupdf.open() as new_doc, pymupdf.open(temp_file_path) as doc:
                for page in doc:
                    pix = page.get_pixmap()
                    img_page = new_doc.new_page(width=pix.width, height=pix.height)
                    img_page.insert_image(
                        pymupdf.Rect(0, 0, pix.width, pix.height), stream=pix.tobytes('jpg')
                    )
                output_file = temp_file_path.with_name(
                    f'{Path(reply_message.file.name).stem}_images.pdf'
                )
                new_doc.save(output_file)

        await upload_file_and_cleanup(event, output_file, progress_message)

    await progress_message.edit(t('pdf_to_images_conversion_completed'))
    if delete_message_after_process:
        delete_message_after(await event.get_message(), seconds=60 * 5)


async def image_to_pdf(event: NewMessage.Event) -> None:
    reply_message = await get_reply_message(event, previous=True)
    progress_message = await send_progress_message(event, t('converting_image_to_pdf'))

    async with download_to_temp_file(event, reply_message, progress_message) as temp_file_path:
        with pymupdf.open(temp_file_path) as img:
            rect = img[0].rect  # Get image dimensions
            pdf_bytes = img.convert_to_pdf()  # Convert image to PDF bytes

        with pymupdf.open() as pdf_doc:
            page = pdf_doc.new_page(width=rect.width, height=rect.height)
            img_pdf = pymupdf.open('pdf', pdf_bytes)
            page.show_pdf_page(rect, img_pdf, 0)
            output_file = temp_file_path.with_name(
                f'{Path(reply_message.file.name or "image").stem}.pdf'
            )
            pdf_doc.save(output_file)

        await upload_file_and_cleanup(event, output_file, progress_message)

    await progress_message.edit(t('image_to_pdf_conversion_completed'))


async def ocrmypdf(event: NewMessage.Event | CallbackQuery.Event) -> None:
    reply_message = await get_reply_message(event, previous=True)
    status_message = await send_progress_message(event, t('starting_process'))
    progress_message = await send_progress_message(event, t('performing_ocr'))
    lang = 'ara'
    if isinstance(event, NewMessage.Event) and (
        match := re.search(r'^/pdf\s+ocr\s+([\w+]{3,})$', event.message.raw_text)
    ):
        lang = match.group(1)

    async with download_to_temp_file(
        event,
        reply_message,
        progress_message,
        suffix=reply_message.file.ext,
    ) as temp_file_path:
        output_file = temp_file_path.with_name(
            f'{Path(reply_message.file.name or "file").stem}_ocr.pdf'
        )
        text_file = output_file.with_suffix('.txt')
        command = f'ocrmypdf -l {lang} --force-ocr --sidecar "{text_file.name}" "{temp_file_path.name}" "{output_file.name}"'
        await stream_shell_output(event, command, status_message, progress_message)
        if output_file.exists() and output_file.stat().st_size:
            await upload_file_and_cleanup(event, output_file, progress_message)
            await upload_file_and_cleanup(event, text_file, progress_message)
        else:
            await status_message.edit(t('failed_to_ocr_pdf'))
            return

    await progress_message.edit(t('pdf_ocr_process_completed'))


async def ocr_pdf(event: NewMessage.Event | CallbackQuery.Event) -> None:
    """OCR PDF using tahweel."""
    service_account = getenv('SERVICE_ACCOUNT_FILE')
    if not service_account:
        await event.reply(t('please_set_service_account_file'))
        return

    reply_message = await get_reply_message(event, previous=True)
    status_message = await send_progress_message(event, t('starting_process'))
    progress_message = await send_progress_message(event, t('performing_ocr_tahweel'))
    output_dir = Path(TMP_DIR / str(uuid4()))
    output_dir.mkdir(parents=True, exist_ok=True)

    async with download_to_temp_file(
        event,
        reply_message,
        progress_message,
        suffix=reply_message.file.ext,
        temp_dir=output_dir,
    ) as temp_file_path:
        command = (
            f'tahweel --service-account-credentials {Path(service_account)} --txt-page-separator ___ '
            f'--output-dir "{output_dir.absolute()}" "{temp_file_path}"'
        )
        await stream_shell_output(event, command, status_message, progress_message)

        for file in filter(
            lambda f: f.is_file() and f.suffix in ('.txt', '.docx'), output_dir.iterdir()
        ):
            renamed_file = file.rename(file.with_stem(Path(reply_message.file.name).stem))
            await upload_file_and_cleanup(event, renamed_file, progress_message)
    await status_message.edit(t('pdf_ocr_process_completed'))
    rmtree(output_dir, ignore_errors=True)


async def compress_pdf(event: NewMessage.Event | CallbackQuery.Event) -> None:
    delete_message_after_process = False
    if isinstance(event, CallbackQuery.Event):
        if event.data.decode() == 'm|pdf_compress|gs':
            buttons = [
                [
                    Button.inline(opt, f'm|pdf_compress|gs|{opt}')
                    for opt in ['screen', 'ebook', 'printer', 'prepress', 'default']
                ]
            ]
            await event.edit(f'{t("choose_ghostscript_compression")}:', buttons=buttons)
            return
        if event.data.decode().startswith('m|pdf_compress|'):
            parts = event.data.decode().split('|')
            method = parts[2] if len(parts) > 2 else 'pymupdf'
            option = parts[3] if len(parts) > 3 else ''
            delete_message_after_process = True
        else:
            buttons = [
                [
                    Button.inline('Ghostscript', 'm|pdf_compress|gs'),
                    Button.inline('PyMuPDF', 'm|pdf_compress|pymupdf'),
                    Button.inline('OCRmyPDF', 'm|pdf_compress|ocrmypdf'),
                ]
            ]
            await event.edit(f'{t("choose_compression_method")}:', buttons=buttons)
            return
    else:
        method = 'pymupdf'
        option = ''

    reply_message = await get_reply_message(event, previous=True)
    status_message = await send_progress_message(event, t('starting_process'))
    progress_message = await send_progress_message(event, t('compressing_pdf'))

    async with download_to_temp_file(
        event,
        reply_message,
        progress_message,
        suffix='.pdf',
    ) as temp_file_path:
        output_file = temp_file_path.with_name(
            f'{Path(reply_message.file.name).stem}_compressed.pdf'
        )

        if method == 'gs':
            command = (
                f'gs -sDEVICE=pdfwrite -dCompatibilityLevel=1.4 -dPDFSETTINGS=/{option} -dNOPAUSE '
                f'-dQUIET -dBATCH -sOutputFile="{output_file}" "{temp_file_path}"'
            )
            await stream_shell_output(event, command, status_message, progress_message)
        elif method == 'pymupdf':
            with pymupdf.open(temp_file_path) as doc:
                doc.save(output_file, **PDF_SAVE_KWARGS)
        else:  # ocrmypdf
            command = f'ocrmypdf --clean --tesseract-timeout 0 "{temp_file_path.name}" "{output_file.name}"'
            await stream_shell_output(event, command, status_message, progress_message)

        if not output_file.exists() or not output_file.stat().st_size:
            await status_message.edit(
                t('process_failed_for_file', file_name=reply_message.file.name)
            )
            return

        compression_ratio = (1 - (output_file.stat().st_size / reply_message.file.size)) * 100
        await upload_file_and_cleanup(event, output_file, progress_message)
        feedback_text = f'{t("compression")}: {compression_ratio:.2f}%\n'
        await progress_message.edit(feedback_text)

    if delete_message_after_process:
        await status_message.delete()
        delete_message_after(await event.get_message(), seconds=60 * 5)


async def pdf_bw(event: NewMessage.Event | CallbackQuery.Event) -> None:
    reply_message = await get_reply_message(event, previous=True)
    status_message = await send_progress_message(event, t('starting_process'))
    progress_message = await send_progress_message(event, t('converting_pdf_to_bw'))
    output_dir = Path(TMP_DIR / str(uuid4()))
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir = output_dir.absolute()

    async with download_to_temp_file(
        event,
        reply_message,
        progress_message,
        suffix='.pdf',
        temp_dir=output_dir,
    ) as temp_file_path:
        input_file = temp_file_path.absolute()
        command = (
            f'pdftoppm -r 300 -gray "{input_file}" "{work_dir / "page"}" '
            f'&& for f in "{work_dir}"/page-*.pgm; do '
            'n=${f##*/}; n=${n%.pgm}; n=${n#page-}; '
            f'unpaper --dpi 300 --type pbm --black-threshold 0.33 --white-threshold 0.9 "$f" "{work_dir}/cleaned-$n.pbm"; '
            'done'
        )
        await stream_shell_output(event, command, status_message, progress_message)

        cleaned_files = sorted(
            output_dir.glob('cleaned-*.pbm'),
            key=lambda p: int(p.stem.split('-')[-1]),
        )
        if not cleaned_files:
            await status_message.edit(
                t('process_failed_for_file', file_name=reply_message.file.name)
            )
            rmtree(output_dir, ignore_errors=True)
            return

        output_file = output_dir / f'{Path(reply_message.file.name).stem}_bw.pdf'
        with pymupdf.open() as doc:
            for file in cleaned_files:
                pix = pymupdf.Pixmap(str(file))
                page = doc.new_page(width=pix.width, height=pix.height)
                page.insert_image(
                    pymupdf.Rect(0, 0, pix.width, pix.height),
                    stream=pix.tobytes('png'),
                )
            doc.save(output_file, **PDF_SAVE_KWARGS)

        await upload_file_and_cleanup(event, output_file, progress_message)

    await progress_message.edit(t('pdf_bw_completed'))
    rmtree(output_dir, ignore_errors=True)


async def crop_pdf_whitespace(event: NewMessage.Event) -> None:
    reply_message = await get_reply_message(event, previous=True)
    progress_message = await send_progress_message(event, t('_pdf_crop_description'))

    async with download_to_temp_file(
        event,
        reply_message,
        progress_message,
        suffix='.pdf',
    ) as temp_file_path:
        with pymupdf.open(temp_file_path) as pdf_doc:
            for page in pdf_doc:
                rect = pymupdf.Rect()
                for item in page.get_bboxlog():
                    rect |= item[1]  # Join this bbox into the result

                margin = 20  # Add margin of 20 to all sides
                rect.x0 = max(0, rect.x0 - margin)
                rect.y0 = max(0, rect.y0 - margin)
                rect.x1 = min(page.rect.width, rect.x1 + margin)
                rect.y1 = min(page.rect.height, rect.y1 + margin)
                page.set_cropbox(rect)

            output_file = temp_file_path.with_name(
                f'{Path(reply_message.file.name or "document").stem}_cropped.pdf'
            )
            pdf_doc.save(output_file, **PDF_SAVE_KWARGS)

        await upload_file_and_cleanup(event, output_file, progress_message)

    await progress_message.edit(t('pdf_whitespace_cropping_completed'))


class PDF(ModuleBase):
    name = 'PDF'
    description = t('_pdf_module_description')
    commands: ClassVar[ModuleBase.CommandsT] = {
        'pdf': Command(
            handler=image_to_pdf,
            description=t('_pdf_description'),
            pattern=re.compile(r'^/(pdf)$'),
            condition=has_photo_or_photo_file,
            is_applicable_for_reply=True,
        ),
        'pdf compress': Command(
            handler=compress_pdf,
            description=t('_pdf_compress_description'),
            pattern=re.compile(r'^/(pdf)\s+(compress)$'),
            condition=has_pdf_file,
            is_applicable_for_reply=True,
        ),
        'pdf bw': Command(
            handler=pdf_bw,
            description=t('_pdf_bw_description'),
            pattern=re.compile(r'^/(pdf)\s+(bw)$'),
            condition=has_pdf_file,
            is_applicable_for_reply=True,
        ),
        'pdf crop': Command(
            handler=crop_pdf_whitespace,
            description=t('_pdf_crop_description'),
            pattern=re.compile(r'^/pdf\s+crop$'),
            condition=has_pdf_file,
            is_applicable_for_reply=True,
        ),
        'pdf extract': Command(
            handler=extract_pdf_pages,
            description=t('_pdf_extract_description'),
            pattern=re.compile(r'^/(pdf)\s+(extract)\s+([\d,\-\s]+)$'),
            condition=has_pdf_file,
            is_applicable_for_reply=True,
        ),
        'pdf images': Command(
            handler=convert_to_images,
            description=t('_pdf_images_description'),
            pattern=re.compile(r'^/(pdf)\s+(images)\s+?(ZIP|PDF)?$'),
            condition=has_pdf_file,
            is_applicable_for_reply=True,
        ),
        'pdf merge': Command(
            handler=merge_pdf_initial,
            description=t('_pdf_merge_description'),
            pattern=re.compile(r'^/(pdf)\s+(merge)$'),
            condition=has_pdf_file,
            is_applicable_for_reply=True,
        ),
        'pdf ocr': Command(
            handler=ocrmypdf,
            description=t('_pdf_ocr_description'),
            pattern=re.compile(r'^/(pdf)\s+(ocr)\s+?([\w+]{3,})?$'),
            condition=has_pdf_file,
            is_applicable_for_reply=True,
        ),
        'pdf split': Command(
            handler=split_pdf,
            description=t('_pdf_split_description'),
            pattern=re.compile(r'^/(pdf)\s+(split)\s+(\d+)$'),
            condition=has_pdf_file,
            is_applicable_for_reply=True,
        ),
        'pdf text': Command(
            handler=extract_pdf_text,
            description=t('_pdf_text_description'),
            pattern=re.compile(r'^/(pdf)\s+(text)$'),
            condition=has_pdf_file,
            is_applicable_for_reply=True,
        ),
        'ocr': Command(
            handler=ocr_pdf,
            description=t('_ocr_description'),
            pattern=re.compile(r'^/(ocr)\s+?([\w+]{3,})?$'),
            condition=lambda e, m: has_pdf_file(e, m) or has_photo_or_photo_file(e, m),
            is_applicable_for_reply=True,
        ),
    }

    @staticmethod
    def register_handlers(bot: TelegramClient) -> None:
        bot.add_event_handler(
            merge_pdf_add,
            NewMessage(
                func=lambda e: (
                    has_pdf_file(e, None)
                    and merge_states[e.sender_id]['state'] == MergeState.COLLECTING
                )
            ),
        )
        bot.add_event_handler(
            merge_pdf_process,
            CallbackQuery(
                pattern=b'finish_pdf_merge',
                func=lambda e: merge_states[e.sender_id]['state'] == MergeState.COLLECTING,
            ),
        )
