from functools import partial
from itertools import zip_longest
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import ClassVar

import pymupdf
import regex as re
from telethon import Button
from telethon.events import CallbackQuery, NewMessage

from src import TMP_DIR
from src.modules.base import CommandHandlerDict, ModuleBase, dynamic_handler
from src.utils.command import Command
from src.utils.downloads import download_file, upload_file
from src.utils.filters import has_photo_or_photo_file
from src.utils.images import crop_image_white_borders
from src.utils.telegram import delete_message_after, get_reply_message

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
                for row in list(zip_longest(*[sorted(ALLOWED_OUTPUT_FORMATS)] * 3, fillvalue=None))
            ]
            await event.edit('Choose the target format:', buttons=buttons)
            return
    else:
        target_format = event.message.text.split('convert ')[1]
        if target_format not in ALLOWED_OUTPUT_FORMATS:
            await event.reply(
                'Unsupported media type for conversion.\n'
                f'Allowed formats: {", ".join(ALLOWED_OUTPUT_FORMATS)}'
            )
            return
    reply_message = await get_reply_message(event, previous=True)
    if reply_message.file.ext == target_format:
        await event.reply(f'The file is already in {target_format} format. Skipping conversion.')
        return

    progress_message = await event.reply(f'Converting image to {target_format}...')
    with NamedTemporaryFile(dir=TMP_DIR, suffix=target_format) as temp_file:
        temp_file_path = await download_file(event, temp_file, reply_message, progress_message)
        output_file = temp_file_path.with_name(
            f'{Path(reply_message.file.name or "image").stem}.{target_format}'
        )
        pymupdf.Pixmap(temp_file_path).save(output_file)
        await upload_file(event, output_file, progress_message)
        output_file.unlink(missing_ok=True)

    if delete_message_after_process:
        event.client.loop.create_task(delete_message_after(await event.get_message()))


async def trim_image(event: NewMessage.Event) -> None:
    reply_message = await get_reply_message(event, previous=True)
    progress_message = await event.reply('Trimming image...')
    with NamedTemporaryFile(dir=TMP_DIR, suffix=reply_message.file.ext) as temp_file:
        temp_file_path = await download_file(event, temp_file, reply_message, progress_message)
        try:
            trimmed_image = crop_image_white_borders(temp_file_path)
        except Exception as e:  # noqa: BLE001
            await progress_message.edit(
                f"Failed to trim the image. Make sure it's a valid image file.\n{e}"
            )
        output_file = temp_file_path.with_name(
            f'{Path(reply_message.file.name or "image").stem}_trimmed.jpg'
        )
        output_file.write_bytes(trimmed_image)
        await upload_file(event, output_file, progress_message)
        output_file.unlink(missing_ok=True)


handlers: CommandHandlerDict = {
    'image convert': convert_image,
    'image trim': trim_image,
}

handler = partial(dynamic_handler, handlers)


class Images(ModuleBase):
    name = 'Images'
    description = 'Images processing commands'
    commands: ClassVar[ModuleBase.CommandsT] = {
        'image convert': Command(
            name='image convert',
            handler=handler,
            description='[format] - Convert image to another format',
            pattern=re.compile(r'^/(image)\s+(convert)\s+([\d\w]{3,4})$'),
            condition=has_photo_or_photo_file,
            is_applicable_for_reply=True,
        ),
        'image trim': Command(
            name='image trim',
            handler=handler,
            description='Remove white space borders from the image',
            pattern=re.compile(r'^/(image)\s+(trim)$'),
            condition=has_photo_or_photo_file,
            is_applicable_for_reply=True,
        ),
    }
