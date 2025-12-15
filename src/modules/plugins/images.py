from itertools import zip_longest
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import ClassVar

import pymupdf
import regex as re
from telethon import Button
from telethon.events import CallbackQuery, NewMessage

from src import TMP_DIR
from src.modules.base import ModuleBase
from src.utils.command import Command
from src.utils.downloads import download_file, upload_file
from src.utils.filters import has_photo_or_photo_file
from src.utils.i18n import t
from src.utils.images import crop_image_white_borders
from src.utils.run import run_command
from src.utils.telegram import (
    delete_message_after,
    edit_or_send_as_file,
    get_reply_message,
    send_progress_message,
)

ALLOWED_INPUT_FORMATS = {
    'jpg',
    'jpeg',
    'png',
    'bmp',
    'gif',
    'tiff',
    'pnm',
    'pgm',
    'pbm',
    'ppm',
    'pam',
    'jxr',
    'jpx',
    'jp2',
    'psd',
}
ALLOWED_OUTPUT_FORMATS = {'jpg', 'jpeg', 'png', 'pnm', 'pgm', 'pbm', 'ppm', 'pam', 'psd', 'ps'}


async def convert_image(event: NewMessage.Event | CallbackQuery.Event) -> None:
    delete_message_after_process = False
    if isinstance(event, CallbackQuery.Event):
        if event.data.decode().startswith('m|image_convert|'):
            target_format = event.data.decode().split('|')[-1]
            delete_message_after_process = True
        else:
            buttons = [
                [Button.inline(f'{ext}', f'm|image_convert|{ext}') for ext in row if ext]
                for row in list(
                    zip_longest(*[iter(sorted(ALLOWED_OUTPUT_FORMATS))] * 3, fillvalue=None)
                )
            ]
            await event.edit(f'{t("choose_target_format")}:', buttons=buttons)
            return
    else:
        target_format = event.message.text.split('convert ')[1]
        if target_format not in ALLOWED_OUTPUT_FORMATS:
            await event.reply(
                f'{t("unsupported_media_type")}.\n'
                f'{t("allowed_formats")}: {", ".join(ALLOWED_OUTPUT_FORMATS)}'
            )
            return
    reply_message = await get_reply_message(event, previous=True)
    if reply_message.file.ext == target_format:
        await event.reply(t('file_already_in_target_format', target_format=target_format))
        return

    progress_message = await send_progress_message(
        event, t('converting_image_to_target_format', target_format=target_format)
    )
    with NamedTemporaryFile(dir=TMP_DIR, suffix=target_format) as temp_file:
        temp_file_path = await download_file(event, temp_file, reply_message, progress_message)
        output_file = temp_file_path.with_name(
            f'{Path(reply_message.file.name or "image").stem}.{target_format}'
        )
        pymupdf.Pixmap(temp_file_path).save(output_file)
        await upload_file(event, output_file, progress_message)
        output_file.unlink(missing_ok=True)

    if delete_message_after_process:
        event.client.loop.create_task(
            delete_message_after(await event.get_message(), seconds=60 * 5)
        )


async def trim_image(event: NewMessage.Event) -> None:
    reply_message = await get_reply_message(event, previous=True)
    progress_message = await send_progress_message(event, f'{t("trimming_image")}…')
    with NamedTemporaryFile(dir=TMP_DIR, suffix=reply_message.file.ext) as temp_file:
        temp_file_path = await download_file(event, temp_file, reply_message, progress_message)
        try:
            trimmed_image = crop_image_white_borders(temp_file_path)
        except Exception as e:  # noqa: BLE001
            await progress_message.edit(f'{t("failed_to_trim_image")}\n{e}')
        output_file = temp_file_path.with_name(
            f'{Path(reply_message.file.name or "image").stem}_trimmed.jpg'
        )
        output_file.write_bytes(trimmed_image)
        await upload_file(event, output_file, progress_message)
        output_file.unlink(missing_ok=True)


async def ocr_image(event: NewMessage.Event) -> None:
    reply_message = await get_reply_message(event, previous=True)
    status_message = await send_progress_message(event, t('starting_process'))
    progress_message = await send_progress_message(event, t('performing_ocr_on_image'))
    lang = 'ara'
    if match := re.search(r'^/image\s+ocr\s+([\w+]{3,})$', event.message.raw_text):
        lang = match.group(1)

    with NamedTemporaryFile(dir=TMP_DIR, suffix=reply_message.file.ext) as temp_file:
        temp_file_path = await download_file(event, temp_file, reply_message, progress_message)
        command = f'tesseract "{temp_file_path.name}" "{temp_file_path.stem}" -l {lang}'
        output_file = temp_file_path.with_suffix('.txt')
        _, code = await run_command(command)
        if code == 0 and output_file.exists() and output_file.stat().st_size:
            await edit_or_send_as_file(event, status_message, output_file.read_text())
            output_file.unlink(missing_ok=True)
        else:
            await status_message.edit(t('failed_to_ocr'))
            return

    await progress_message.edit(t('image_ocr_complete'))


class Images(ModuleBase):
    name = 'Images'
    description = t('_images_module_description')
    commands: ClassVar[ModuleBase.CommandsT] = {
        'image convert': Command(
            name='image convert',
            handler=convert_image,
            description=t('_image_convert_description'),
            pattern=re.compile(r'^/(image)\s+(convert)\s+([\d\w]{3,4})$'),
            condition=has_photo_or_photo_file,
            is_applicable_for_reply=True,
        ),
        'image ocr': Command(
            name='image ocr',
            handler=ocr_image,
            description=t('_image_ocr_description'),
            pattern=re.compile(r'^/(image)\s+(ocr)\s+?([\w+]{3,})?$'),
            condition=has_photo_or_photo_file,
            is_applicable_for_reply=True,
        ),
        'image trim': Command(
            name='image trim',
            handler=trim_image,
            description=t('_image_trim_description'),
            pattern=re.compile(r'^/(image)\s+(trim)$'),
            condition=has_photo_or_photo_file,
            is_applicable_for_reply=True,
        ),
    }
