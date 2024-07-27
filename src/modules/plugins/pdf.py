from functools import partial
from tempfile import NamedTemporaryFile
from typing import ClassVar

import pymupdf
import regex as re
from telethon.events import CallbackQuery, NewMessage

from src import TMP_DIR
from src.modules.base import CommandHandlerDict, ModuleBase, dynamic_handler
from src.utils.command import Command
from src.utils.downloads import download_file, get_download_name, upload_file
from src.utils.filters import has_pdf_file
from src.utils.telegram import get_reply_message


async def extract_pdf_text(event: NewMessage.Event | CallbackQuery.Event) -> None:
    reply_message = await get_reply_message(event, previous=True)
    progress_message = await event.reply('Extracting text from PDF...')

    with NamedTemporaryFile(dir=TMP_DIR, suffix='.pdf') as temp_file:
        temp_file_path = await download_file(event, temp_file, reply_message, progress_message)
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
        await upload_file(event, output_file, progress_message)
        output_file.unlink(missing_ok=True)
        await progress_message.delete()


handlers: CommandHandlerDict = {
    'pdf text': extract_pdf_text,
}

handler = partial(dynamic_handler, handlers)


class PDF(ModuleBase):
    name = 'PDF'
    description = 'PDF processing commands'
    commands: ClassVar[ModuleBase.CommandsT] = {
        'pdf text': Command(
            name='pdf text',
            handler=handler,
            description='[bitrate] - compress audio to [bitrate] kbps',
            pattern=re.compile(r'^/(pdf)\s+(text)$'),
            condition=has_pdf_file,
            is_applicable_for_reply=True,
        )
    }
