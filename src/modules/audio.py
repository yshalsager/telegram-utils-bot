from pathlib import Path
from tempfile import NamedTemporaryFile, _TemporaryFileWrapper

import regex as re
from telethon.events import CallbackQuery, NewMessage
from telethon.tl.custom import Message

from src.modules.base import ModuleBase
from src.modules.run import stream_shell_output
from src.utils.downloads import get_download_name
from src.utils.fast_telethon import download_file, upload_file
from src.utils.progress import progress_callback
from src.utils.telegram import get_reply_message


async def process_audio(
    event: NewMessage.Event,
    ffmpeg_command: str,
    output_suffix: str,
    is_voice: bool = False,
    get_file_name: bool = True,
) -> None:
    reply_message = await get_reply_message(event, previous=True)
    if not reply_message.audio:
        await event.reply('The replied message is not an audio file.')
        return

    status_message = await event.reply('Starting process...')
    progress_message = await event.reply('<pre>Process output:</pre>')

    with NamedTemporaryFile(delete=False) as temp_file:
        await download_audio(event, temp_file, reply_message, progress_message)
        if get_file_name:
            input_file_name = get_download_name(reply_message.document, reply_message)
            output_file = (Path(temp_file.name).parent / input_file_name).with_suffix(output_suffix)
        else:
            output_file = Path(temp_file.name).with_suffix(output_suffix)

        await stream_shell_output(
            event,
            ffmpeg_command.format(input=temp_file.name, output=output_file),
            status_message,
            progress_message,
        )

        if not output_file.exists() or not output_file.stat().st_size:
            await status_message.edit('Processing failed.')
            return

        await upload_audio(event, output_file, progress_message, is_voice)

    await status_message.edit('File successfully processed.')


async def download_audio(
    event: NewMessage.Event,
    temp_file: _TemporaryFileWrapper,
    reply_message: Message,
    progress_message: Message,
) -> None:
    await download_file(
        event.client,
        reply_message.document,
        temp_file,
        progress_callback=lambda current, total: progress_callback(
            current, total, progress_message, 'Downloading'
        ),
    )


async def upload_audio(
    event: NewMessage.Event, output_file: Path, progress_message: Message, is_voice: bool
) -> None:
    with output_file.open('rb') as file_to_upload:
        uploaded_file = await upload_file(
            event.client,
            file_to_upload,
            output_file.name,
            progress_callback=lambda current, total: progress_callback(
                current, total, progress_message, 'Uploading'
            ),
        )
    await event.client.send_file(
        event.chat_id,
        file=uploaded_file,
        voice_note=is_voice,
        reply_to=event.message.id,
    )


async def convert_to_voice_note(event: NewMessage.Event) -> None:
    ffmpeg_command = 'ffmpeg -hide_banner -y -i "{input}" -vn -c:a libopus -b:a 48k "{output}"'
    await process_audio(event, ffmpeg_command, '.ogg', is_voice=True)


async def compress_audio(event: NewMessage.Event | CallbackQuery.Event) -> None:
    if isinstance(event, CallbackQuery.Event):
        audio_bitrate = '48'
    else:
        audio_bitrate = re.search(r'(\d+)$', event.message.text).group(1)
    ffmpeg_command = (
        f'ffmpeg -hide_banner -y -i "{{input}}" -vn -c:a aac -b:a {audio_bitrate}k "{{output}}"'
    )
    await process_audio(event, ffmpeg_command, '.m4a')


handlers = {
    'audio compress': compress_audio,
    'voice': convert_to_voice_note,
}


async def handler(event: NewMessage.Event | CallbackQuery.Event) -> None:
    if isinstance(event, CallbackQuery.Event):
        command = event.data.decode('utf-8').lstrip('m_').replace('_', ' ')
    else:
        command_with_args = event.message.text.rstrip('audio').split(maxsplit=1)[1]
        command = command_with_args.split()[0]
    if command not in handlers:
        await event.reply('Command not found.')
        return

    await handlers[command](event)


class Audio(ModuleBase):
    @property
    def name(self) -> str:
        return 'Audio'

    @property
    def description(self) -> str:
        return 'Audio processing commands'

    def commands(self) -> ModuleBase.CommandsT:
        return {
            'voice': {
                'handler': convert_to_voice_note,
                'description': 'Convert an audio to voice note',
                'is_applicable_for_reply': True,
            },
            'audio compress': {
                'handler': handler,
                'description': '[bitrate] - compress audio to [bitrate] kbps',
                'is_applicable_for_reply': True,
            },
        }

    def is_applicable(self, event: NewMessage.Event) -> bool:
        return bool(
            re.match(r'^/voice', event.message.text)
            or re.match(r'^/audio\s+compress\s+(\d+)$', event.message.text)
            and event.message.is_reply
        )

    @staticmethod
    def is_applicable_for_reply(event: NewMessage.Event) -> bool:
        return bool(event.message.audio)
