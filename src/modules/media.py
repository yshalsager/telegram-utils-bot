import contextlib
from collections import defaultdict
from datetime import datetime
from enum import Enum, auto
from functools import partial
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, ClassVar

import orjson
import regex as re
from pydub import AudioSegment
from pydub.silence import split_on_silence
from telethon import Button, TelegramClient
from telethon.events import CallbackQuery, NewMessage, StopPropagation
from telethon.tl.custom import Message

from src.modules.base import ModuleBase
from src.modules.run import stream_shell_output
from src.utils.command import Command
from src.utils.downloads import download_file, get_download_name, upload_file
from src.utils.json import json_options, process_dict
from src.utils.reply import ReplyState, handle_callback_query_for_reply_state, reply_states
from src.utils.run import run_command
from src.utils.telegram import edit_or_send_as_file, get_reply_message

ffprobe_command = 'ffprobe -v quiet -print_format json -show_format -show_streams "{input}"'


class MergeState(Enum):
    IDLE = auto()
    COLLECTING = auto()
    MERGING = auto()


merge_states: defaultdict[int, dict[str, Any]] = defaultdict(
    lambda: {'state': MergeState.IDLE, 'files': []}
)


async def process_media(
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
        await download_file(event, temp_file, reply_message, progress_message)
        if get_file_name:
            input_file = get_download_name(reply_message)
            output_file = (Path(temp_file.name).parent / input_file).with_suffix(output_suffix)
            if output_file.name == input_file.name:
                output_file = output_file.with_name(f'_{output_file.name}')
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

        await upload_file(event, output_file, progress_message, is_voice)

    await status_message.edit('File successfully processed.')


async def convert_to_voice_note(event: NewMessage.Event | CallbackQuery.Event) -> None:
    ffmpeg_command = 'ffmpeg -hide_banner -y -i "{input}" -vn -c:a libopus -b:a 48k "{output}"'
    await process_media(event, ffmpeg_command, '.ogg', is_voice=True)


async def compress_audio(event: NewMessage.Event | CallbackQuery.Event) -> None:
    if isinstance(event, CallbackQuery.Event):
        audio_bitrate = '48'
    else:
        audio_bitrate = re.search(r'(\d+)$', event.message.text).group(1)
    ffmpeg_command = (
        f'ffmpeg -hide_banner -y -i "{{input}}" -vn -c:a aac -b:a {audio_bitrate}k "{{output}}"'
    )
    await process_media(event, ffmpeg_command, '.m4a')


async def convert_to_audio(event: NewMessage.Event | CallbackQuery.Event) -> None:
    reply_message = await get_reply_message(event, previous=True)
    if reply_message.file and reply_message.file.ext in ['aac', 'm4a', 'mp3']:
        ffmpeg_command = (
            'ffmpeg -hide_banner -y -i "{input}" -vn -c:a copy -movflags +faststart "{output}"'
        )
    else:
        ffmpeg_command = 'ffmpeg -hide_banner -y -i "{input}" -vn -c:a aac -b:a 64k -movflags +faststart "{output}"'
    await process_media(event, ffmpeg_command, '.m4a', reply_message=reply_message)


async def cut_media(event: NewMessage.Event | CallbackQuery.Event) -> None:
    if isinstance(event, CallbackQuery.Event):
        return await handle_callback_query_for_reply_state(
            event,
            'Please enter the start and end times in the format: [start time] [end time] '
            '(e.g., <code>00:00:00 00:30:00</code>)',
        )
    if event.sender_id in reply_states:
        reply_states[event.sender_id]['state'] = ReplyState.PROCESSING
        reply_message = await event.client.get_messages(
            event.chat_id, ids=reply_states[event.sender_id]['media_message_id']
        )
    else:
        reply_message = await get_reply_message(event, previous=True)

    start_time, end_time = re.search(
        r'(\d{2}:\d{2}:\d{2})\s+(\d{2}:\d{2}:\d{2})', event.message.text
    ).groups()
    try:
        # Simple validation of time format
        datetime.strptime(start_time, '%H:%M:%S')  # noqa: DTZ007
        datetime.strptime(end_time, '%H:%M:%S')  # noqa: DTZ007
    except ValueError:
        await event.reply('Invalid time format. Use HH:MM:SS for both start and end times.')
        return None

    ffmpeg_command = (
        f'ffmpeg -hide_banner -y -i "{{input}}" '
        f'-ss {start_time} -to {end_time} '
        f'-c copy -map 0 "{{output}}"'
    )
    await process_media(event, ffmpeg_command, reply_message.file.ext, reply_message)
    if event.sender_id in reply_states:
        del reply_states[event.sender_id]
    raise StopPropagation


async def split_media(event: NewMessage.Event | CallbackQuery.Event) -> None:
    if isinstance(event, CallbackQuery.Event):
        return await handle_callback_query_for_reply_state(
            event,
            'Please enter the split duration in the format: [duration]h/m/s (e.g., 30m, 1h, 90s)',
        )

    if event.sender_id in reply_states:
        reply_states[event.sender_id]['state'] = ReplyState.PROCESSING
        reply_message = await event.client.get_messages(
            event.chat_id, ids=reply_states[event.sender_id]['media_message_id']
        )
        args = event.message.text
    else:
        reply_message = await get_reply_message(event, previous=True)
        args = event.message.text.split()[2]

    unit = args[-1] if args[-1].isalpha() else 's'
    duration = int(args[:-1])
    if unit == 'h':
        segment_duration = duration * 3600
    elif unit == 'm':
        segment_duration = duration * 60
    else:
        segment_duration = duration
    status_message = await event.reply('Starting process...')

    progress_message = await event.reply('<pre>Process output:</pre>')
    with NamedTemporaryFile() as temp_file:
        await download_file(event, temp_file, reply_message, progress_message)
        input_file = get_download_name(reply_message)
        output_file_base = (Path(temp_file.name).parent / input_file).with_suffix('')

        output_pattern = f'{output_file_base.stem}_segment_%03d{input_file.suffix}'
        ffmpeg_command = (
            f'ffmpeg -hide_banner -y -i "{temp_file.name}" -f segment -segment_time {segment_duration} '
            f'-c copy "{output_file_base.parent / output_pattern}"'
        )
        await stream_shell_output(event, ffmpeg_command, status_message, progress_message)

        for output_file in sorted(
            output_file_base.parent.glob(f'{output_file_base.stem}_segment_*{input_file.suffix}')
        ):
            if output_file.exists() and output_file.stat().st_size:
                await upload_file(
                    event,
                    output_file,
                    progress_message,
                    is_voice=reply_message.voice is not None,
                    caption=f'<code>{output_file.stem}</code>',
                )
            else:
                await status_message.edit(f'Processing failed for {output_file.name}.')
            output_file.unlink(missing_ok=True)

    await progress_message.edit('Files successfully split and uploaded.')
    if event.sender_id in reply_states:
        del reply_states[event.sender_id]
    raise StopPropagation


async def media_info(event: NewMessage.Event | CallbackQuery.Event) -> None:
    reply_message = await get_reply_message(event, previous=True)
    progress_message = await event.reply('Starting process...')
    with NamedTemporaryFile() as temp_file:
        await download_file(event, temp_file, reply_message, progress_message)
        output, code = await run_command(ffprobe_command.format(input=temp_file.name))
        if code:
            message = f'Failed to get info.\n<pre>{output}</pre>'
        else:
            info = orjson.dumps(process_dict(orjson.loads(output)), option=json_options).decode()
            message = f'<pre>{info}</pre>'
        await edit_or_send_as_file(event, progress_message, message)


async def set_metadata(event: NewMessage.Event | CallbackQuery.Event) -> None:
    if isinstance(event, CallbackQuery.Event):
        return await handle_callback_query_for_reply_state(
            event, 'Please enter the title and artist in the format: Title - Artist'
        )

    if event.sender_id in reply_states:
        reply_states[event.sender_id]['state'] = ReplyState.PROCESSING
        reply_message = await event.client.get_messages(
            event.chat_id, ids=reply_states[event.sender_id]['media_message_id']
        )
        title, artist = event.message.text.split(' - ')
    else:
        reply_message = await get_reply_message(event, previous=True)
        title, artist = event.message.text.split('metadata ')[1].split(' - ')

    ffmpeg_command = (
        'ffmpeg -hide_banner -y -i "{input}" -c copy '
        f'-metadata title="{title}" -metadata artist="{artist}" '
        '"{output}"'
    )
    await process_media(event, ffmpeg_command, reply_message.file.ext, reply_message=reply_message)
    if event.sender_id in reply_states:
        del reply_states[event.sender_id]
    raise StopPropagation


async def merge_audio_initial(event: NewMessage.Event | CallbackQuery.Event) -> None:
    merge_states[event.sender_id]['state'] = MergeState.COLLECTING
    merge_states[event.sender_id]['files'] = []

    reply_message = await get_reply_message(event, previous=True)
    merge_states[event.sender_id]['files'].append(reply_message.id)
    await event.reply('Send more files to merge.')


async def merge_audio_add(event: NewMessage.Event) -> None:
    merge_states[event.sender_id]['files'].append(event.id)
    await event.reply(
        "File added. Send more or click 'Finish' to merge.",
        buttons=[Button.inline('Finish', 'finish_merge')],
    )
    raise StopPropagation


async def merge_audio_process(event: CallbackQuery.Event) -> None:
    merge_states[event.sender_id]['state'] = MergeState.MERGING
    files = merge_states[event.sender_id]['files']
    await event.answer('Merging...')

    if len(files) < 2:
        await event.answer('Not enough files to merge.')
        merge_states[event.sender_id]['state'] = MergeState.IDLE
        return

    status_message = await event.respond('Starting merge process...')
    progress_message = await event.respond('<pre>Merge output:</pre>')

    temp_files = []
    try:
        with NamedTemporaryFile(mode='w+', suffix='.txt', delete=False) as file_list:
            for file_id in files:
                message = await event.client.get_messages(event.chat_id, ids=file_id)
                temp_file = NamedTemporaryFile(suffix=message.file.ext, delete=False)
                temp_files.append(temp_file)
                await download_file(event, temp_file, message, progress_message)
                file_list.write(f"file '{temp_file.name}'\n")
                temp_file.close()  # Close but don't delete

        with NamedTemporaryFile(suffix=message.file.ext, delete=False) as output_file:
            ffmpeg_command = f'ffmpeg -hide_banner -y -f concat -safe 0 -i "{file_list.name}" -c copy "{output_file.name}"'
            await stream_shell_output(event, ffmpeg_command, status_message, progress_message)
            output_file_path = Path(output_file.name)
            if output_file_path.exists() and output_file_path.stat().st_size:
                await upload_file(
                    event,
                    output_file_path,
                    progress_message,
                    is_voice=message.voice is not None,
                )
                await status_message.edit('Files successfully merged.')
            else:
                await status_message.edit('Merging failed.')

    finally:
        # Clean up temporary files
        with contextlib.suppress(OSError):
            for temp_file in temp_files:
                Path(temp_file.name).unlink(missing_ok=True)
            Path(file_list.name).unlink(missing_ok=True)
            Path(output_file.name).unlink(missing_ok=True)
        merge_states.pop(event.sender_id)


async def trim_silence(event: NewMessage.Event) -> None:
    reply_message = await get_reply_message(event, previous=True)
    status_message = await event.reply('Starting silence trimming process...')
    progress_message = await event.reply('<pre>Process output:</pre>')
    extension = reply_message.file.ext

    with (
        NamedTemporaryFile(suffix=extension) as input_file,
        NamedTemporaryFile(suffix='.mp3') as output_file,
    ):
        output_file_path = Path(output_file.name).parent / output_file.name
        if reply_message.file.name:
            output_file_path = output_file_path.with_name(
                f'trimmed_{reply_message.file.name}'
            ).with_suffix('.mp3')
        await download_file(event, input_file, reply_message, progress_message)
        await progress_message.edit('Loading file...')
        sound = AudioSegment.from_file(Path(input_file.name))
        await progress_message.edit('Splitting...')
        chunks = split_on_silence(sound, min_silence_len=500, silence_thresh=-40)
        await progress_message.edit('Combining...')
        combined = AudioSegment.empty()
        for chunk in chunks:
            combined += chunk
        await progress_message.edit('Exporting...')
        combined.export(output_file_path, format='mp3')
        # command = (
        #     f'ffmpeg -hide_banner -y -i "{input_file.name}" -af '
        #     f'silenceremove=start_periods=1:start_duration=1:start_threshold=-50dB:'
        #     f'detection=peak,aformat=dblp,areverse,silenceremove=start_periods=1:start_duration=1:'
        #     f'start_threshold=-10dB:detection=peak,aformat=dblp,areverse "{output_file_path.name}"'
        # )
        # command = (
        #     f'ffmpeg -hide_banner -y -i "{input_file.name}" -f wav - | sox -t wav - "{output_file_path.name}" '
        #     f'silence -l 1 0.1 1% -1 1.0 1%'
        # )
        #
        # await stream_shell_output(event, command, status_message, progress_message)

        if not output_file_path.exists() or not output_file_path.stat().st_size:
            await status_message.edit('Silence trimming failed.')
            return

        await upload_file(
            event,
            output_file_path,
            progress_message,
            is_voice=bool(reply_message.voice),
            caption='Trimmed audio',
        )

    await status_message.edit('Silence successfully trimmed.')


async def mute_video(event: NewMessage.Event) -> None:
    ffmpeg_command = 'ffmpeg -hide_banner -y -i "{input}" -c copy -an "{output}"'
    await process_media(event, ffmpeg_command, '.mp4')


async def extract_subtitle(event: NewMessage.Event) -> None:
    reply_message = await get_reply_message(event, previous=True)
    status_message = await event.reply('Starting subtitle extraction process...')
    progress_message = await event.reply('<pre>Process output:</pre>')

    with NamedTemporaryFile(suffix=reply_message.file.ext) as input_file:
        await download_file(event, input_file, reply_message, progress_message)

        output, code = await run_command(
            f'ffprobe -v quiet -print_format json -show_streams "{input_file.name}"'
        )
        if code:
            await status_message.edit('Failed to get stream info.')
            return

        streams = orjson.loads(output)['streams']
        subtitle_streams = [s for s in streams if s['codec_type'] == 'subtitle']

        if not subtitle_streams:
            await status_message.edit('No subtitle streams found in the video.')
            return

        for i, stream in enumerate(subtitle_streams):
            ext = 'srt' if stream['codec_name'] == 'mov_text' else stream['codec_name']
            output_file = Path(input_file.name).with_suffix(f'.{ext}')

            ffmpeg_command = (
                f'ffmpeg -hide_banner -y -i "{input_file.name}" '
                f'-map 0:{stream["index"]} "{output_file}"'
            )
            await stream_shell_output(event, ffmpeg_command, status_message, progress_message)

            if output_file.exists() and output_file.stat().st_size:
                caption = f'Subtitle {i + 1}: {stream.get("tags", {}).get("language", "Unknown")}'
                await event.client.send_file(event.chat_id, output_file, caption=caption)
            else:
                await status_message.edit(f'Failed to extract subtitle stream {i + 1}.')

            output_file.unlink(missing_ok=True)

    await status_message.edit('Subtitle extraction completed.')


handlers = {
    'audio compress': compress_audio,
    'audio convert': convert_to_audio,
    'media cut': cut_media,
    'media info': media_info,
    'media merge': merge_audio_initial,
    'audio metadata': set_metadata,
    'media split': split_media,
    'audio trim': trim_silence,
    'video mute': mute_video,
    'video subtitle': extract_subtitle,
    'voice': convert_to_voice_note,
}


async def handler(event: NewMessage.Event | CallbackQuery.Event) -> None:
    if isinstance(event, CallbackQuery.Event):
        command = event.data.decode('utf-8')
        if command.startswith('m_'):
            command = command[2:]
        command = command.replace('_', ' ')
    else:
        command = ' '.join(' '.join(event.pattern_match.groups()).split(' ')[:2])
    if command not in handlers:
        await event.reply('Command not found.')
        return

    await handlers[command](event)


def check_media_conditions(
    event: NewMessage.Event, reply_message: Message | None, **media_types: bool
) -> bool:
    if not media_types:
        return True
    message = reply_message or event.message

    def check_media(_media_type: str) -> bool:
        return bool(getattr(message, _media_type, None))

    checks = []
    for media_type, should_have in media_types.items():
        if media_type == 'media':
            checks.append(
                any(check_media(t) for t in ['audio', 'voice', 'video', 'video_note'])
                == should_have
            )
        elif media_type.startswith('not_'):
            actual_type = media_type[4:]
            checks.append(check_media(actual_type) != should_have)
        elif '_or_' in media_type:
            types = media_type.split('_or_')
            checks.append(any(check_media(t) for t in types) == should_have)
        else:
            checks.append(check_media(media_type) == should_have)

    return all(checks)


class Media(ModuleBase):
    name = 'Media'
    description = 'Media processing commands'
    commands: ClassVar[ModuleBase.CommandsT] = {
        'audio compress': Command(
            name='audio compress',
            handler=handler,
            description='[bitrate] - compress audio to [bitrate] kbps',
            pattern=re.compile(r'^/(audio)\s+(compress)\s+(\d+)$'),
            condition=partial(check_media_conditions, audio=True),
            is_applicable_for_reply=True,
        ),
        'audio convert': Command(
            name='audio convert',
            handler=handler,
            description='Convert a video or voice note to an audio',
            pattern=re.compile(r'^/(audio)\s+(convert)$'),
            condition=partial(check_media_conditions, not_audio=True),
            is_applicable_for_reply=True,
        ),
        'audio metadata': Command(
            name='audio metadata',
            handler=handler,
            description='[title] - [artist] - Set title and artist of an audio file',
            pattern=re.compile(r'^/(audio)\s+(metadata)\s+.+\s+-\s+.+$'),
            condition=partial(check_media_conditions, audio=True),
            is_applicable_for_reply=True,
        ),
        'audio trim': Command(
            name='audio trim',
            handler=handler,
            description='Trim audio silence',
            pattern=re.compile(r'^/(audio)\s+(trim)$'),
            condition=partial(check_media_conditions, audio_or_voice=True),
            is_applicable_for_reply=True,
        ),
        'media cut': Command(
            name='media cut',
            handler=handler,
            description='[HH:MM:SS HH:MM:SS] - Cut audio/video from start time to end time',
            pattern=re.compile(r'^/(media)\s+(cut)\s+(\d{2}:\d{2}:\d{2})\s+(\d{2}:\d{2}:\d{2})$'),
            condition=partial(check_media_conditions, media=True),
            is_applicable_for_reply=True,
        ),
        'media split': Command(
            name='media split',
            handler=handler,
            description='[duration]h/m/s - Split audio/video into segments of specified duration '
            '(e.g., 30m, 1h, 90s)',
            pattern=re.compile(r'^/(media)\s+(split)\s+(\d+[hms])$'),
            condition=partial(check_media_conditions, media=True),
            is_applicable_for_reply=True,
        ),
        'media merge': Command(
            name='media merge',
            handler=handler,
            description='Merge multiple files',
            pattern=re.compile(r'^/(media)\s+(merge)$'),
            condition=partial(check_media_conditions, media=True),
            is_applicable_for_reply=True,
        ),
        'media info': Command(
            name='media info',
            handler=handler,
            description='Get media info',
            pattern=re.compile(r'^/(media)\s+(info)$'),
            condition=partial(check_media_conditions, media=True),
            is_applicable_for_reply=True,
        ),
        'video mute': Command(
            name='video mute',
            handler=handler,
            description='Mute video',
            pattern=re.compile(r'^/(video)\s+(mute)$'),
            condition=partial(check_media_conditions, video_or_video_note=True),
            is_applicable_for_reply=True,
        ),
        'video subtitle': Command(
            name='video subtitle',
            handler=handler,
            description='Extract subtitle streams from a video',
            pattern=re.compile(r'^/(video)\s+(subtitle)$'),
            condition=partial(check_media_conditions, video=True),
            is_applicable_for_reply=True,
        ),
        'voice': Command(
            name='voice',
            handler=convert_to_voice_note,
            description='Convert to voice note',
            pattern=re.compile(r'^/(voice)$'),
            condition=partial(check_media_conditions, not_voice=True),
            is_applicable_for_reply=True,
        ),
    }

    @staticmethod
    def register_handlers(bot: TelegramClient) -> None:
        bot.add_event_handler(
            merge_audio_add,
            NewMessage(
                func=lambda e: (
                    e.is_private
                    and (e.message.audio or e.message.voice)
                    and merge_states[e.sender_id]['state'] == MergeState.COLLECTING
                )
            ),
        )
        bot.add_event_handler(
            merge_audio_process,
            CallbackQuery(
                pattern=b'finish_merge',
                func=lambda e: e.is_private
                and merge_states[e.sender_id]['state'] == MergeState.COLLECTING,
            ),
        )
        bot.add_event_handler(
            set_metadata,
            NewMessage(
                func=lambda e: (
                    e.is_private
                    and e.is_reply
                    and e.sender_id in reply_states
                    and re.match(r'^.+\s+-\s+.+$', e.message.text)
                    and reply_states[e.sender_id]['state'] == ReplyState.WAITING
                    and e.message.reply_to_msg_id == reply_states[e.sender_id]['reply_message_id']
                )
            ),
        )
        bot.add_event_handler(
            split_media,
            NewMessage(
                func=lambda e: (
                    e.is_private
                    and e.is_reply
                    and e.sender_id in reply_states
                    and re.match(r'^(\d+[hms])$', e.message.text)
                    and reply_states[e.sender_id]['state'] == ReplyState.WAITING
                    and e.message.reply_to_msg_id == reply_states[e.sender_id]['reply_message_id']
                )
            ),
        )
        bot.add_event_handler(
            cut_media,
            NewMessage(
                func=lambda e: (
                    e.is_private
                    and e.is_reply
                    and e.sender_id in reply_states
                    and re.match(r'^(\d{2}:\d{2}:\d{2})\s+(\d{2}:\d{2}:\d{2})$', e.message.text)
                    and reply_states[e.sender_id]['state'] == ReplyState.WAITING
                    and e.message.reply_to_msg_id == reply_states[e.sender_id]['reply_message_id']
                )
            ),
        )
