from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile

import orjson
import regex as re
from telethon.events import CallbackQuery, NewMessage
from telethon.tl.custom import Message

from src.modules.base import ModuleBase
from src.modules.run import stream_shell_output
from src.utils.downloads import download_audio, get_download_name, upload_audio
from src.utils.json import json_options, process_dict
from src.utils.run import run_command
from src.utils.telegram import edit_or_send_as_file, get_reply_message

ffprobe_command = 'ffprobe -v quiet -print_format json -show_format -show_streams "{input}"'


async def process_audio(
    event: NewMessage.Event,
    ffmpeg_command: str,
    output_suffix: str,
    reply_message: Message | None = None,
    is_voice: bool = False,
    get_file_name: bool = True,
) -> None:
    if not reply_message:
        reply_message = await get_reply_message(event, previous=True)
    status_message = await event.reply('Starting process...')
    progress_message = await event.reply('<pre>Process output:</pre>')

    with NamedTemporaryFile() as temp_file:
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


async def convert_to_voice_note(event: NewMessage.Event | CallbackQuery.Event) -> None:
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


async def convert_to_audio(event: NewMessage.Event | CallbackQuery.Event) -> None:
    reply_message = await get_reply_message(event, previous=True)
    if reply_message.file and reply_message.file.ext in ['aac', 'm4a', 'mp3']:
        ffmpeg_command = (
            'ffmpeg -hide_banner -y -i "{input}" -vn -c:a copy -movflags +faststart "{output}"'
        )
    else:
        ffmpeg_command = 'ffmpeg -hide_banner -y -i "{input}" -vn -c:a aac -b:a 64k -movflags +faststart "{output}"'
    await process_audio(event, ffmpeg_command, '.m4a', reply_message=reply_message)


async def cut_audio(event: NewMessage.Event | CallbackQuery.Event) -> None:
    start_time, end_time = event.message.text.split()[2:]
    try:
        # Simple validation of time format
        datetime.strptime(start_time, '%H:%M:%S')  # noqa: DTZ007
        datetime.strptime(end_time, '%H:%M:%S')  # noqa: DTZ007
    except ValueError:
        await event.reply('Invalid time format. Use HH:MM:SS for both start and end times.')
        return

    reply_message = await get_reply_message(event, previous=True)
    ffmpeg_command = (
        f'ffmpeg -hide_banner -y -ss {start_time} -to {end_time} -i "{{input}}" '
        f'-c copy -map 0 "{{output}}"'
    )
    await process_audio(
        event,
        ffmpeg_command,
        reply_message.file.ext,
    )


async def get_info(event: NewMessage.Event | CallbackQuery.Event) -> None:
    # TODO enable for video
    # TODO rename module to media
    reply_message = await get_reply_message(event, previous=True)
    progress_message = await event.reply('Starting process...')
    with NamedTemporaryFile() as temp_file:
        await download_audio(event, temp_file, reply_message, progress_message)
        output, code = await run_command(ffprobe_command.format(input=temp_file.name))
        if code:
            message = f'Failed to get info.\n<pre>{output}</pre>'
        else:
            info = orjson.dumps(process_dict(orjson.loads(output)), option=json_options).decode()
            message = f'<pre>{info}</pre>'
        await edit_or_send_as_file(event, progress_message, message)


handlers = {
    'audio compress': compress_audio,
    'audio convert': convert_to_audio,
    'audio cut': cut_audio,
    'audio info': get_info,
    'voice': convert_to_voice_note,
}


async def handler(event: NewMessage.Event | CallbackQuery.Event) -> None:
    if isinstance(event, CallbackQuery.Event):
        command = event.data.decode('utf-8').lstrip('m_').replace('_', ' ')
    else:
        command = ' '.join(' '.join(event.pattern_match.groups()).split(' ')[:2])
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
            'audio convert': {
                'handler': handler,
                'description': 'Convert a video or voice note to an audio',
                'is_applicable_for_reply': True,
            },
            'audio cut': {
                'handler': handler,
                'description': 'HH:MM:SS HH:MM:SS - Cut audio/video from start time to end time',
                # 'is_applicable_for_reply': True,
            },
            'audio info': {
                'handler': handler,
                'description': 'Get audio info',
                'is_applicable_for_reply': True,
            },
        }

    def is_applicable(self, event: NewMessage.Event) -> bool:
        return bool(
            (
                re.match(r'^/(voice)', event.message.text)
                and (event.message.audio or event.message.video)
            )
            or (
                re.match(r'^/(audio)\s+(compress)\s+(\d+)$', event.message.text)
                and event.message.audio
            )
            or (
                re.match(r'^/(audio)\s+(convert)$', event.message.text)
                and (event.message.voice or event.message.video or event.message.video_note)
            )
            or (
                re.match(
                    r'^/(audio)\s+(cut)\s+(\d{2}:\d{2}:\d{2})\s+(\d{2}:\d{2}:\d{2})$',
                    event.message.text,
                )
                and (event.message.audio or event.message.voice or event.message.video)
            )
            or (
                re.match(r'^/(audio)\s+(info)$', event.message.text)
                and (event.message.audio or event.message.voice)
            )
            and event.message.is_reply
        )

    @staticmethod
    def is_applicable_for_reply(event: NewMessage.Event) -> bool:
        return bool(
            event.message.audio
            or event.message.voice
            or event.message.video
            or event.message.video_note
        )
