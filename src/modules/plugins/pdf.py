from collections import defaultdict
from functools import partial
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import ClassVar

import pymupdf
import regex as re
from telethon import Button, TelegramClient
from telethon.events import CallbackQuery, NewMessage, StopPropagation

from src import TMP_DIR
from src.modules.base import CommandHandlerDict, ModuleBase, dynamic_handler
from src.utils.command import Command
from src.utils.downloads import download_file, get_download_name, upload_file
from src.utils.filters import has_pdf_file, is_valid_reply_state
from src.utils.reply import (
    MergeState,
    ReplyState,
    StateT,
    handle_callback_query_for_reply_state,
)
from src.utils.telegram import get_reply_message

reply_states: StateT = defaultdict(
    lambda: {'state': ReplyState.WAITING, 'media_message_id': None, 'reply_message_id': None}
)
merge_states: StateT = defaultdict(lambda: {'state': MergeState.IDLE, 'files': []})


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


async def merge_pdf_initial(event: NewMessage.Event | CallbackQuery.Event) -> None:
    merge_states[event.sender_id]['state'] = MergeState.COLLECTING
    merge_states[event.sender_id]['files'] = []
    reply_message = await get_reply_message(event, previous=True)
    merge_states[event.sender_id]['files'].append(reply_message.id)
    await event.reply('Send more PDF files to merge.')


async def merge_pdf_add(event: NewMessage.Event) -> None:
    merge_states[event.sender_id]['files'].append(event.id)
    await event.reply(
        "PDF added. Send more or click 'Finish' to merge.",
        buttons=[Button.inline('Finish', 'finish_pdf_merge')],
    )
    raise StopPropagation


async def merge_pdf_process(event: CallbackQuery.Event) -> None:
    merge_states[event.sender_id]['state'] = MergeState.MERGING
    files = merge_states[event.sender_id]['files']
    await event.answer('Merging PDFs...')

    if len(files) < 2:
        await event.answer('Not enough PDFs to merge.')
        merge_states[event.sender_id]['state'] = MergeState.IDLE
        return

    status_message = await event.respond('Starting PDF merge process...')
    progress_message = await event.respond('Merging PDFs...')

    with pymupdf.open() as merged_pdf:
        for file_id in files:
            message = await event.client.get_messages(event.chat_id, ids=file_id)
            with NamedTemporaryFile(dir=TMP_DIR, suffix='.pdf') as temp_file:
                temp_file_path = await download_file(event, temp_file, message, progress_message)
                with pymupdf.open(temp_file_path) as pdf_doc:
                    merged_pdf.insert_pdf(pdf_doc)
        with NamedTemporaryFile(dir=TMP_DIR, suffix='.pdf') as out_file:
            merged_pdf.save(out_file.name)
            output_file_path = Path(out_file.name)
            if output_file_path.exists() and output_file_path.stat().st_size:
                output_file_path = output_file_path.rename(
                    output_file_path.with_stem(f'merged_{Path(message.file.name).stem}')
                )
                await upload_file(
                    event,
                    output_file_path,
                    progress_message,
                )
                await status_message.edit('PDFs successfully merged.')
            else:
                await status_message.edit('Merging failed.')

    await progress_message.delete()
    merge_states.pop(event.sender_id)


async def split_pdf(event: NewMessage.Event | CallbackQuery.Event) -> None:
    if isinstance(event, CallbackQuery.Event):
        return await handle_callback_query_for_reply_state(
            event,
            reply_states,
            'Please enter number of pages to split PDF into:',
        )

    if event.sender_id in reply_states:
        reply_states[event.sender_id]['state'] = ReplyState.PROCESSING
        reply_message = await event.client.get_messages(
            event.chat_id, ids=reply_states[event.sender_id]['media_message_id']
        )
    else:
        reply_message = await get_reply_message(event, previous=True)
    pages_count = int(re.search(r'(\d+)', event.message.text).group(1))
    progress_message = await event.reply('Splitting PDF...')

    with NamedTemporaryFile(dir=TMP_DIR, suffix='.pdf') as temp_file:
        temp_file_path = await download_file(event, temp_file, reply_message, progress_message)
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
                    new_doc.save(output_file)
                    await upload_file(event, output_file, progress_message)
                    output_file.unlink(missing_ok=True)

        await progress_message.edit('PDF split complete.')

    if event.sender_id in reply_states:
        reply_states.pop(event.sender_id)
    raise StopPropagation


handlers: CommandHandlerDict = {
    'pdf merge': merge_pdf_initial,
    'pdf text': extract_pdf_text,
    'pdf split': split_pdf,
}

handler = partial(dynamic_handler, handlers)


class PDF(ModuleBase):
    name = 'PDF'
    description = 'PDF processing commands'
    commands: ClassVar[ModuleBase.CommandsT] = {
        'pdf merge': Command(
            name='pdf merge',
            handler=handler,
            description='Merge multiple PDF files',
            pattern=re.compile(r'^/(pdf)\s+(merge)$'),
            condition=has_pdf_file,
            is_applicable_for_reply=True,
        ),
        'pdf split': Command(
            name='pdf split',
            handler=handler,
            description='[pages] - Split PDF into multiple pages',
            pattern=re.compile(r'^/(pdf)\s+(split)\s+(\d+)$'),
            condition=has_pdf_file,
            is_applicable_for_reply=True,
        ),
        'pdf text': Command(
            name='pdf text',
            handler=handler,
            description='Extract text from PDF',
            pattern=re.compile(r'^/(pdf)\s+(text)$'),
            condition=has_pdf_file,
            is_applicable_for_reply=True,
        ),
    }

    @staticmethod
    def register_handlers(bot: TelegramClient) -> None:
        bot.add_event_handler(
            merge_pdf_add,
            NewMessage(
                func=lambda e: (
                    e.is_private
                    and has_pdf_file(e, None)
                    and merge_states[e.sender_id]['state'] == MergeState.COLLECTING
                )
            ),
        )
        bot.add_event_handler(
            merge_pdf_process,
            CallbackQuery(
                pattern=b'finish_pdf_merge',
                func=lambda e: e.is_private
                and merge_states[e.sender_id]['state'] == MergeState.COLLECTING,
            ),
        )
        bot.add_event_handler(
            split_pdf,
            NewMessage(
                func=lambda e: (
                    is_valid_reply_state(e, reply_states) and re.match(r'^(\d+)$', e.message.text)
                )
            ),
        )
