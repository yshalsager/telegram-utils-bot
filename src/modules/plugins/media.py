import contextlib
from collections import defaultdict
from datetime import datetime
from functools import partial
from itertools import zip_longest
from math import floor
from os import getenv
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, ClassVar, cast
from uuid import uuid4

import orjson
import regex as re
from pydub import AudioSegment
from pydub.silence import split_on_silence
from telethon import Button, TelegramClient
from telethon.events import CallbackQuery, NewMessage, StopPropagation
from telethon.tl.custom import Message
from telethon.tl.types import DocumentAttributeAudio, DocumentAttributeVideo

from src import TMP_DIR
from src.modules.base import CommandHandlerDict, ModuleBase, dynamic_handler
from src.modules.plugins.run import stream_shell_output
from src.utils.command import Command
from src.utils.downloads import download_file, get_download_name, upload_file
from src.utils.filters import has_media, is_valid_reply_state
from src.utils.json import json_options, process_dict
from src.utils.reply import (
    MergeState,
    ReplyState,
    StateT,
    handle_callback_query_for_reply_state,
)
from src.utils.run import run_command
from src.utils.subtitles import srt_to_txt
from src.utils.telegram import delete_message_after, edit_or_send_as_file, get_reply_message

ffprobe_command = 'ffprobe -v quiet -print_format json -show_format -show_streams "{input}"'
reply_states: StateT = defaultdict(
    lambda: {'state': ReplyState.WAITING, 'media_message_id': None, 'reply_message_id': None}
)
merge_states: StateT = defaultdict(lambda: {'state': MergeState.IDLE, 'files': []})
video_create_states: StateT = defaultdict(lambda: {'state': MergeState.IDLE, 'files': []})
video_update_states: StateT = defaultdict(lambda: {'state': MergeState.IDLE, 'files': []})


async def get_stream_info(stream_specifier: str, file_path: Path) -> dict[str, Any]:
    output, _ = await run_command(
        f'ffprobe -v error -select_streams {stream_specifier} -show_entries '
        f'stream=codec_name,duration,width,height -of json "{file_path}"'
    )
    _info = orjson.loads(output)
    return cast(dict[str, Any], _info['streams'][0]) if _info and 'streams' in _info else {}


async def get_format_info(file_path: Path) -> dict[str, Any]:
    output, _ = await run_command(
        f'ffprobe -v error -show_entries format=duration,tags -of json "{file_path}"'
    )
    _info = orjson.loads(output)
    return cast(dict[str, Any], _info['format']) if _info and 'format' in _info else {}


async def get_output_info(file_path: Path) -> dict[str, Any]:
    video_info = await get_stream_info('v:0', file_path)
    audio_info = await get_stream_info('a:0', file_path)
    format_info = await get_format_info(file_path)

    info = {
        'vcodec': video_info.get('codec_name', 'none'),
        'acodec': audio_info.get('codec_name', 'none'),
        'duration': float(
            video_info.get('duration')
            or audio_info.get('duration')
            or format_info.get('duration', 0)
        ),
        'width': video_info.get('width', 0),
        'height': video_info.get('height', 0),
        'title': format_info.get('tags', {}).get('title', ''),
        'uploader': format_info.get('tags', {}).get('artist', ''),
    }

    return info


async def get_media_bitrate(file_path: str) -> tuple[int, int]:
    async def get_bitrate(stream_specifier: str) -> int:
        _output, _ = await run_command(
            f'ffprobe -v error -select_streams {stream_specifier} -show_entries '
            f'stream=bit_rate -of csv=p=0 "{file_path}"'
        )
        _output = _output.strip()
        return int(_output) if _output.isdigit() else 0

    video_bitrate = await get_bitrate('v:0')
    audio_bitrate = await get_bitrate('a:0')

    if video_bitrate == 0 and audio_bitrate == 0:
        output, _ = await run_command(
            f'ffprobe -v error -show_entries format=bit_rate -of csv=p=0 "{file_path}"'
        )
        # Assume it's all audio if we couldn't get separate streams
        audio_bitrate = int(output.strip() or 0)

    return video_bitrate, audio_bitrate


async def process_media(
    event: NewMessage.Event,
    ffmpeg_command: str,
    output_suffix: str,
    reply_message: Message | None = None,
    is_voice: bool = False,
    get_file_name: bool = True,
    get_bitrate: bool = False,
    feedback_text: str = 'File successfully processed.',
) -> dict[str, Any]:
    data: dict[str, Any] = {}
    if not reply_message:
        reply_message = await get_reply_message(event, previous=True)
    status_message = await event.reply('Starting process...')
    progress_message = await event.reply('<pre>Process output:</pre>')

    with NamedTemporaryFile() as temp_file:
        temp_file_path = await download_file(event, temp_file, reply_message, progress_message)
        if get_file_name:
            input_file = get_download_name(reply_message)
            output_file = (temp_file_path.parent / input_file).with_suffix(output_suffix)
            if output_file.name == input_file.name:
                output_file = output_file.with_name(f'_{output_file.name}')
        else:
            output_file = temp_file_path.with_suffix(output_suffix)

        if get_bitrate:
            video_bitrate, audio_bitrate = await get_media_bitrate(temp_file.name)
            ffmpeg_command = ffmpeg_command.format(
                input=temp_file.name,
                output=output_file,
                video_bitrate=video_bitrate,
                audio_bitrate=audio_bitrate,
            )
        else:
            ffmpeg_command = ffmpeg_command.format(input=temp_file.name, output=output_file)

        status = await stream_shell_output(event, ffmpeg_command, status_message, progress_message)
        data['status_text'] = status
        if not output_file.exists() or not output_file.stat().st_size:
            await status_message.edit('Processing failed.')
            return data

        output_info = await get_output_info(output_file)
        if output_info.get('vcodec') == 'none':
            attributes = [
                DocumentAttributeAudio(
                    duration=int(output_info.get('duration', 0)),
                    title=output_info.get('title'),
                    performer=output_info.get('uploader'),
                )
            ]
        else:
            attributes = [
                DocumentAttributeVideo(
                    duration=int(output_info.get('duration', 0)),
                    w=output_info.get('width', 0),
                    h=output_info.get('height', 0),
                )
            ]

        await upload_file(
            event,
            output_file,
            progress_message,
            is_voice,
            force_document=False,
            attributes=attributes,
        )
        data['output_size'] = output_file.stat().st_size

    await status_message.edit(feedback_text)
    data['status_message'] = status_message
    return data


async def convert_to_voice_note(event: NewMessage.Event | CallbackQuery.Event) -> None:
    ffmpeg_command = 'ffmpeg -hide_banner -y -i "{input}" -vn -c:a libopus -b:a 48k "{output}"'
    await process_media(
        event,
        ffmpeg_command,
        '.ogg',
        is_voice=True,
        feedback_text='Converted to voice note successfully.',
    )


async def compress_audio(event: NewMessage.Event | CallbackQuery.Event) -> None:
    delete_message_after_process = False
    if isinstance(event, CallbackQuery.Event):
        if event.data.decode().startswith('m|audio_compress|'):
            audio_bitrate = event.data.decode().split('|')[-1]
            delete_message_after_process = True
        else:
            buttons = [
                [
                    Button.inline(f'{bitrate}kbps', f'm|audio_compress|{bitrate}')
                    for bitrate in [16, 32, 48]
                ],
                [
                    Button.inline(f'{bitrate}kbps', f'm|audio_compress|{bitrate}')
                    for bitrate in [64, 96, 128]
                ],
            ]
            await event.edit('Choose the desired bitrate:', buttons=buttons)
            return
    else:
        audio_bitrate = re.search(r'(\d+)$', event.message.text).group(1)
    ffmpeg_command = (
        f'ffmpeg -hide_banner -y -i "{{input}}" -vn -c:a aac -b:a {audio_bitrate}k "{{output}}"'
    )
    await process_media(
        event, ffmpeg_command, '.m4a', feedback_text='Audio successfully compressed.'
    )
    if delete_message_after_process:
        await delete_message_after(await event.get_message())


async def convert_to_audio(event: NewMessage.Event | CallbackQuery.Event) -> None:
    reply_message = await get_reply_message(event, previous=True)
    if reply_message.file and reply_message.file.ext in ['aac', 'm4a', 'mp3']:
        ffmpeg_command = 'ffmpeg -hide_banner -y -i "{input}" -vn -c:a copy "{output}"'
    else:
        ffmpeg_command = (
            'ffmpeg -hide_banner -y -i "{input}" -vn -c:a aac -b:a {audio_bitrate} "{output}"'
        )
    await process_media(
        event,
        ffmpeg_command,
        '.m4a',
        reply_message=reply_message,
        get_bitrate=True,
        feedback_text='Converted to audio successfully.',
    )


async def cut_media(event: NewMessage.Event | CallbackQuery.Event) -> None:
    if isinstance(event, CallbackQuery.Event):
        return await handle_callback_query_for_reply_state(
            event,
            reply_states,
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
    await process_media(
        event,
        ffmpeg_command,
        reply_message.file.ext,
        reply_message,
        feedback_text='Media cut successfully.',
    )
    if event.sender_id in reply_states:
        del reply_states[event.sender_id]
    raise StopPropagation


async def split_media(event: NewMessage.Event | CallbackQuery.Event) -> None:
    if isinstance(event, CallbackQuery.Event):
        return await handle_callback_query_for_reply_state(
            event,
            reply_states,
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
        temp_file_path = await download_file(event, temp_file, reply_message, progress_message)
        input_file = get_download_name(reply_message)
        output_file_base = (temp_file_path.parent / input_file).with_suffix('')

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
            event, reply_states, 'Please enter the title and artist in the format: Title - Artist'
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
    await process_media(
        event,
        ffmpeg_command,
        reply_message.file.ext,
        reply_message=reply_message,
        feedback_text='Audio metadata set successfully.',
    )
    if event.sender_id in reply_states:
        del reply_states[event.sender_id]
    raise StopPropagation


async def merge_media_initial(event: NewMessage.Event | CallbackQuery.Event) -> None:
    merge_states[event.sender_id]['state'] = MergeState.COLLECTING
    merge_states[event.sender_id]['files'] = []
    reply_message = await get_reply_message(event, previous=True)
    merge_states[event.sender_id]['files'].append(reply_message.id)
    await event.reply('Send more files to merge.')


async def merge_media_add(event: NewMessage.Event) -> None:
    merge_states[event.sender_id]['files'].append(event.id)
    await event.reply(
        "File added. Send more or click 'Finish' to merge.",
        buttons=[Button.inline('Finish', 'finish_merge')],
    )
    raise StopPropagation


async def merge_media_process(event: CallbackQuery.Event) -> None:
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
    await process_media(
        event,
        ffmpeg_command,
        '.mp4',
        feedback_text='Audio has been removed from video successfully.',
    )


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


ALLOWED_AUDIO_FORMATS = {
    'mp3',
    'aac',
    'm4a',
    'm4b',
    'ogg',
    'opus',
    'wav',
    'flac',
    'ra',
    'rm',
    'rma',
    'wma',
    'amr',
    'aif',
    'dts',
    'mpeg',
}
ALLOWED_VIDEO_FORMATS = {'mp4', 'mkv', 'avi', 'mov', 'webm', 'flv', 'mpeg', 'mpg', 'wmv', 'm4v'}


async def convert_media(event: NewMessage.Event | CallbackQuery.Event) -> None:
    delete_message_after_process = False
    if isinstance(event, CallbackQuery.Event):
        if event.data.decode().startswith('m|media_convert|'):
            target_format = event.data.decode().split('|')[-1]
            delete_message_after_process = True
        else:
            reply_message = await get_reply_message(event, previous=True)
            formats = (
                ALLOWED_AUDIO_FORMATS
                if (reply_message.audio or reply_message.voice)
                else ALLOWED_VIDEO_FORMATS
            )
            buttons = [
                [Button.inline(f'{ext}', f'm|media_convert|{ext}') for ext in row if ext]
                for row in list(zip_longest(*[iter(formats)] * 3, fillvalue=None))
            ]
            await event.edit('Choose the target format:', buttons=buttons)
            return
    else:
        target_format = event.message.text.split('convert ')[1].lower()
        if target_format[0] == '.':
            target_format = target_format[1:]
        if target_format not in ALLOWED_VIDEO_FORMATS | ALLOWED_AUDIO_FORMATS:
            await event.reply(
                'Unsupported media type for conversion.\n'
                f'Allowed formats: {", ".join(ALLOWED_VIDEO_FORMATS | ALLOWED_AUDIO_FORMATS)}'
            )
            return
    reply_message = await get_reply_message(event, previous=True)
    if reply_message.file.ext == target_format:
        await event.reply(f'The file is already in {target_format} format. Skipping conversion.')
        return

    if target_format in ALLOWED_AUDIO_FORMATS:
        ffmpeg_command = 'ffmpeg -hide_banner -y -i "{input}" -b:a {audio_bitrate} "{output}"'
    else:
        ffmpeg_command = (
            'ffmpeg -hide_banner -y -i "{input}" -c:v libx264 -b:v {video_bitrate} '
            '-c:a aac -b:a {audio_bitrate} "{output}"'
        )

    await process_media(
        event,
        ffmpeg_command,
        f'.{target_format}',
        reply_message=reply_message,
        get_bitrate=True,
        feedback_text=f'Media converted to {target_format} successfully.',
    )
    if delete_message_after_process:
        event.client.loop.create_task(delete_message_after(await event.get_message()))


ALLOWED_VIDEO_QUALITIES = {144, 240, 360, 480, 720}


async def resize_video(event: NewMessage.Event | CallbackQuery.Event) -> None:
    delete_message_after_process = False
    if isinstance(event, CallbackQuery.Event):
        if event.data.decode().startswith('m|video_resize|'):
            quality = event.data.decode().split('|')[-1]
            delete_message_after_process = True
        else:
            buttons = [
                [
                    Button.inline(str(quality), f'm|video_resize|{quality}')
                    for quality in ALLOWED_VIDEO_QUALITIES
                ]
            ]
            await event.edit('Choose the target quality:', buttons=buttons)
            return
    else:
        quality = event.message.text.split('resize ')[1]

    quality = int(quality)
    if quality not in ALLOWED_VIDEO_QUALITIES:
        await event.reply(
            f'Invalid quality. Please choose from {", ".join(map(str, ALLOWED_VIDEO_QUALITIES))}.'
        )
        return

    reply_message = await get_reply_message(event, previous=True)
    ffmpeg_command = (
        f'ffmpeg -hide_banner -y -i "{{input}}" -filter_complex '
        f'"scale=width=-1:height={quality}:force_original_aspect_ratio=decrease,pad=ceil(iw/2)*2:ceil(ih/2)*2" '
        f'-c:v libx264 -b:v {{video_bitrate}} -maxrate {{video_bitrate}} -bufsize {{video_bitrate}} '
        f'-c:a copy "{{output}}"'
    )
    await process_media(
        event, ffmpeg_command, reply_message.file.ext, reply_message=reply_message, get_bitrate=True
    )
    if delete_message_after_process:
        event.client.loop.create_task(delete_message_after(await event.get_message()))


async def video_update_initial(event: NewMessage.Event | CallbackQuery.Event) -> None:
    video_update_states[event.sender_id]['state'] = MergeState.COLLECTING
    video_update_states[event.sender_id]['files'] = []
    reply_message = await get_reply_message(event, previous=True)
    video_update_states[event.sender_id]['files'].append(reply_message.id)
    await event.reply('Send the media file with audio to use.', reply_to=reply_message.id)


async def video_update_process(event: NewMessage.Event) -> None:
    video_update_states[event.sender_id]['state'] = MergeState.MERGING
    video_message = await event.client.get_messages(
        event.chat_id, ids=video_update_states[event.sender_id]['files'][0]
    )
    audio_message = event.message
    status_message = await event.reply('Starting audio update process...')
    progress_message = await event.respond('<pre>Process output:</pre>')

    with (
        NamedTemporaryFile(suffix=video_message.file.ext) as video_file,
        NamedTemporaryFile(suffix=audio_message.file.ext) as audio_file,
        NamedTemporaryFile(suffix=video_message.file.ext) as output_file,
    ):
        await download_file(event, video_file, video_message, progress_message)
        await download_file(event, audio_file, audio_message, progress_message)

        ffmpeg_command = (
            f'ffmpeg -hide_banner -y -i "{video_file.name}" -i "{audio_file.name}" '
            f'-map "0:v" -map "1:a" -c:v copy -c:a copy "{output_file.name}"'
        )
        await stream_shell_output(event, ffmpeg_command, status_message, progress_message)
        if not Path(output_file.name).exists() or not Path(output_file.name).stat().st_size:
            await status_message.edit('Audio update failed.')
            return

        await upload_file(event, Path(output_file.name), progress_message)

    await status_message.edit('Video audio successfully updated.')
    video_update_states.pop(event.sender_id)
    raise StopPropagation


async def amplify_sound(event: NewMessage.Event | CallbackQuery.Event) -> None:
    delete_message_after_process = False
    if isinstance(event, CallbackQuery.Event):
        if event.data.decode().startswith('m|media_amplify|'):
            amplification_factor = float(event.data.decode().split('|')[-1])
            delete_message_after_process = True
        else:
            buttons = [
                [
                    Button.inline(f'{factor}x', f'm|media_amplify|{factor}')
                    for factor in [1.25, 1.5, 1.75, 2]
                ],
                [
                    Button.inline(f'{factor}x', f'm|media_amplify|{factor}')
                    for factor in [2.25, 2.5, 2.75, 3]
                ],
            ]
            await event.edit('Choose the amplification factor:', buttons=buttons)
            return
    else:
        amplification_factor = float(event.message.text.split('amplify ')[1])

    if amplification_factor <= 1:
        await event.reply('Amplification factor must be greater than 1.')
        return
    if amplification_factor > 3:
        amplification_factor = 3

    reply_message = await get_reply_message(event, previous=True)
    ffmpeg_command = (
        'ffmpeg -hide_banner -y -i "{input}" '
        f'-filter:a "volume={amplification_factor}" '
        '-b:a {audio_bitrate}'
    )

    if bool(reply_message.video or reply_message.video_note):
        ffmpeg_command += ' -c:v copy'
    else:
        ffmpeg_command += ' -vn'
    ffmpeg_command += ' "{output}"'

    await process_media(
        event,
        ffmpeg_command,
        reply_message.file.ext,
        reply_message=reply_message,
        get_bitrate=True,
        feedback_text=f'Audio amplified by {amplification_factor}x successfully.',
    )
    if delete_message_after_process:
        event.client.loop.create_task(delete_message_after(await event.get_message()))


async def video_thumbnails(event: NewMessage.Event | CallbackQuery.Event) -> None:
    reply_message = await get_reply_message(event, previous=True)
    status_message = await event.reply('Starting thumbnail generation process...')
    progress_message = await event.reply('<pre>Process output:</pre>')

    with NamedTemporaryFile(suffix=reply_message.file.ext) as input_file:
        await download_file(event, input_file, reply_message, progress_message)
        duration_output, _ = await run_command(
            f'ffprobe -v error -show_entries format=duration -of '
            f'default=noprint_wrappers=1:nokey=1 "{input_file.name}"'
        )
        duration = float(duration_output.strip())

        # Calculate timestamps for each thumbnail
        interval = duration / 16
        timestamps = [i * interval for i in range(16)]
        # Generate thumbnail grid
        output_file = Path(input_file.name).with_suffix('.jpg')
        select_frames = '+'.join([f'eq(n,{int(t * 25)})' for t in timestamps])  # Assuming 25 fps
        ffmpeg_command = (
            f'ffmpeg -hide_banner -y -i "{input_file.name}" '
            f'-vf "select=\'{select_frames}\',scale=480:-1,tile=4x4" '
            f'-frames:v 1 "{output_file}"'
        )

        await stream_shell_output(event, ffmpeg_command, status_message, progress_message)
        if not output_file.exists() or not output_file.stat().st_size:
            await status_message.edit('Thumbnail generation failed.')
            return
        await upload_file(event, output_file, progress_message)

    await status_message.edit('Video thumbnails successfully generated.')


async def compress_video(event: NewMessage.Event | CallbackQuery.Event) -> None:
    delete_message_after_process = False
    if isinstance(event, CallbackQuery.Event):
        if event.data.decode().startswith('m|video_compress|'):
            target_percentage = int(event.data.decode().split('|')[-1])
            delete_message_after_process = True
        else:
            buttons = [
                [
                    Button.inline(f'{percentage}%', f'm|video_compress|{percentage}')
                    for percentage in range(20, 60, 10)
                ],
                [
                    Button.inline(f'{percentage}%', f'm|video_compress|{percentage}')
                    for percentage in range(60, 100, 10)
                ],
            ]
            await event.edit('Choose the target compression percentage:', buttons=buttons)
            return
    else:
        target_percentage = int(event.message.text.split('compress ')[1])

    if target_percentage < 20 or target_percentage > 90:
        await event.reply('Compression percentage must be between 20 and 90.')
        return

    reply_message = await get_reply_message(event, previous=True)
    # Calculate target bitrate
    calculated_percentage = 100 - target_percentage
    target_size = (calculated_percentage / 100) * reply_message.file.size
    target_bitrate = int(floor(target_size * 8 / reply_message.file.duration))
    bitrate = (
        f'{target_bitrate // 1000000}M'
        if target_bitrate // 1000000 >= 1
        else f'{target_bitrate // 1000}k'
    )
    ffmpeg_command = (
        f'ffmpeg -hide_banner -y -i "{{input}}" '
        f'-c:v libx264 -b:v {bitrate} -bufsize {bitrate} '
        '-preset ultrafast '
        '-c:a aac -b:a 48k '
        '-movflags +faststart '
        f'"{{output}}"'
    )
    data = await process_media(
        event, ffmpeg_command, reply_message.file.ext, feedback_text='Video compressed successfully'
    )
    compression_ratio = (1 - (data['output_size'] / reply_message.file.size)) * 100
    feedback_text = (
        f'\nTarget compression: {target_percentage}%\n'
        f'Actual compression: {compression_ratio:.2f}%\n'
    )
    status_message = data['status_message']
    assert isinstance(status_message, Message)
    await status_message.edit(data['status_text'] + feedback_text)
    if delete_message_after_process:
        event.client.loop.create_task(delete_message_after(await event.get_message()))


async def video_encode_x265(event: NewMessage.Event | CallbackQuery.Event) -> None:
    delete_message_after_process = False
    if isinstance(event, CallbackQuery.Event):
        if event.data.decode().startswith('m|video_x265|'):
            crf = int(event.data.decode().split('|')[-1])
            delete_message_after_process = True
        else:
            buttons = [
                [Button.inline(f'CRF {crf}', f'm|video_x265|{crf}') for crf in range(20, 25, 2)],
                [Button.inline(f'CRF {crf}', f'm|video_x265|{crf}') for crf in range(25, 29, 2)],
            ]
            await event.edit(
                'Choose the CRF value (20-28, lower is better quality but larger file size):',
                buttons=buttons,
            )
            return
    else:
        crf = int(event.message.text.split('x265 ')[1])

    if crf < 20 or crf > 28:
        await event.reply('CRF value must be between 20 and 28.')
        return

    reply_message = await get_reply_message(event, previous=True)
    ffmpeg_command = (
        'ffmpeg -hide_banner -y -i "{input}" '
        f'-c:v libx265 -crf {crf} -preset ultrafast '
        '-c:a aac -b:a 48k '
        '-movflags +faststart '
        '"{output}"'
    )
    data = await process_media(
        event,
        ffmpeg_command,
        reply_message.file.ext,
        feedback_text='Video encoded with x265 successfully',
    )

    compression_ratio = (1 - (data['output_size'] / reply_message.file.size)) * 100
    feedback_text = f'\nCompression ratio: {compression_ratio:.2f}%\n'
    status_message = data['status_message']
    assert isinstance(status_message, Message)
    await status_message.edit(data['status_text'] + feedback_text)
    if delete_message_after_process:
        event.client.loop.create_task(delete_message_after(await event.get_message()))


async def video_create_initial(event: NewMessage.Event | CallbackQuery.Event) -> None:
    video_create_states[event.sender_id]['state'] = MergeState.COLLECTING
    video_create_states[event.sender_id]['files'] = []
    reply_message = await get_reply_message(event, previous=True)
    video_create_states[event.sender_id]['files'].append(reply_message.id)
    await event.reply(
        'Send the subtitle file (.srt) to use.',
        reply_to=reply_message.id,
        buttons=Button.clear(),
    )


async def video_create_process(event: NewMessage.Event) -> None:
    video_create_states[event.sender_id]['state'] = MergeState.MERGING
    audio_message = await event.client.get_messages(
        event.chat_id, ids=video_create_states[event.sender_id]['files'][0]
    )
    subtitle_message = event.message
    status_message = await event.reply('Starting video creation process...')
    progress_message = await event.respond('<pre>Process output:</pre>')

    audio_file = Path(TMP_DIR / audio_message.file.name)
    subtitle_file = Path(TMP_DIR / subtitle_message.file.name)
    output_file = audio_file.with_suffix('.mp4')
    with audio_file.open('wb+') as f:
        await download_file(event, f, audio_message, progress_message)
    with subtitle_file.open('wb+') as f:
        await download_file(event, f, subtitle_message, progress_message)

    ffmpeg_command = (
        f'ffmpeg -hide_banner -y -f lavfi -i color=c=black:s=854x480:d={audio_message.file.duration} '
        f'-i "{audio_file}" -i "{subtitle_file}" '
        f"-filter_complex \"[0:v]subtitles=f='{subtitle_file}':force_style='FontSize=28,Alignment=10,MarginV=190'[v]\" "
        f'-map "[v]" -map 1:a -map 2 '
        f'-c:v libx264 -preset ultrafast -c:a aac -b:a 48k '
        f'-c:s mov_text '
        f'-shortest "{output_file}"'
    )
    await stream_shell_output(event, ffmpeg_command, status_message, progress_message)
    if not output_file.exists() or not output_file.stat().st_size:
        await status_message.edit('Video creation failed.')
    else:
        await upload_file(event, output_file, progress_message)
        await status_message.edit('Video successfully created.')
        output_file.unlink(missing_ok=True)

    audio_file.unlink(missing_ok=True)
    subtitle_file.unlink(missing_ok=True)
    video_create_states.pop(event.sender_id)
    raise StopPropagation


async def transcribe_media(event: NewMessage.Event | CallbackQuery.Event) -> None:  # noqa: C901, PLR0912
    delete_message_after_process = False
    if isinstance(event, CallbackQuery.Event):
        if event.data.decode().startswith('m|transcribe|'):
            transcription_method = event.data.decode().split('|')[-1]
            delete_message_after_process = True
        else:
            buttons = [
                [
                    Button.inline('Vosk', 'm|transcribe|vosk'),
                    Button.inline('Whisper', 'm|transcribe|whisper'),
                    Button.inline('Wit', 'm|transcribe|wit'),
                ]
            ]
            await event.edit('Choose the transcription method:', buttons=buttons)
            return
    else:
        transcription_method = event.message.text.split(' ')[-1]
    wit_access_tokens, whisper_model_path = None, None
    if transcription_method == 'whisper':
        whisper_model_path = getenv('WHISPER_MODEL_PATH')
        if not whisper_model_path:
            await event.reply('Please set WHISPER_MODEL_PATH environment variable.')
            return
    # if transcription_method == 'wit':
    else:
        wit_access_tokens = getenv('WIT_CLIENT_ACCESS_TOKENS')
        if not wit_access_tokens:
            await event.reply('Please set WIT_CLIENT_ACCESS_TOKENS environment variable.')
            return

    reply_message = await get_reply_message(event, previous=True)
    status_message = await event.reply('Starting transcription process...')
    progress_message = await event.reply('<pre>Process output:</pre>')
    output_dir = Path(TMP_DIR / str(uuid4()))
    output_dir.mkdir(parents=True, exist_ok=True)

    with NamedTemporaryFile(suffix=reply_message.file.ext, dir=output_dir) as temp_file:
        await download_file(event, temp_file, reply_message, progress_message)
        tmp_file_path = Path(temp_file.name)
        if transcription_method == 'vosk':
            command = f'vosk-transcriber --log-level warning -i {temp_file.name} -l ar -t srt -o {output_dir / Path(temp_file.name).with_suffix(".srt")}'
        else:
            command = f'tafrigh "{temp_file.name}" -o "{output_dir}" -f txt srt'
            command += (
                f' -w {wit_access_tokens}'
                if transcription_method == 'wit'
                else f' -m {whisper_model_path} --use_faster_whisper'
            )
        if transcription_method == 'vosk':
            srt_to_txt(tmp_file_path.with_suffix('.srt'))
        await stream_shell_output(event, command, status_message, progress_message, max_length=500)
        for output_file in output_dir.glob('*.[st][xr]t'):
            if output_file.exists() and output_file.stat().st_size:
                renamed_file = output_file.with_stem(Path(reply_message.file.name).stem)
                output_file.rename(renamed_file)
                await upload_file(
                    event,
                    renamed_file,
                    progress_message,
                    caption=f'<code>{renamed_file.name}</code>',
                )
            else:
                await status_message.edit(f'Failed to transcribe {renamed_file.name}')
    await status_message.edit('Transcription completed.')
    await delete_message_after(progress_message)
    if delete_message_after_process:
        event.client.loop.create_task(delete_message_after(await event.get_message()))


handlers: CommandHandlerDict = {
    'audio compress': compress_audio,
    'audio convert': convert_to_audio,
    'audio metadata': set_metadata,
    'audio trim': trim_silence,
    'media amplify': amplify_sound,
    'media convert': convert_media,
    'media cut': cut_media,
    'media info': media_info,
    'media merge': merge_media_initial,
    'media split': split_media,
    'transcribe': transcribe_media,
    'video compress': compress_video,
    'video create': video_create_initial,
    'video mute': mute_video,
    'video resize': resize_video,
    'video subtitle': extract_subtitle,
    'video thumbnails': video_thumbnails,
    'video update': video_update_initial,
    'video x265': video_encode_x265,
    'voice': convert_to_voice_note,
}

handler = partial(dynamic_handler, handlers)


class Media(ModuleBase):
    name = 'Media'
    description = 'Media processing commands'
    commands: ClassVar[ModuleBase.CommandsT] = {
        'audio compress': Command(
            name='audio compress',
            handler=handler,
            description='[bitrate] - compress audio to [bitrate] kbps',
            pattern=re.compile(r'^/(audio)\s+(compress)\s+(\d+)$'),
            condition=partial(has_media, audio=True),
            is_applicable_for_reply=True,
        ),
        'audio convert': Command(
            name='audio convert',
            handler=handler,
            description='Convert a video or voice note to an audio',
            pattern=re.compile(r'^/(audio)\s+(convert)$'),
            condition=partial(has_media, not_audio=True),
            is_applicable_for_reply=True,
        ),
        'audio metadata': Command(
            name='audio metadata',
            handler=handler,
            description='[title] - [artist] - Set title and artist of an audio file',
            pattern=re.compile(r'^/(audio)\s+(metadata)\s+.+\s+-\s+.+$'),
            condition=partial(has_media, audio=True),
            is_applicable_for_reply=True,
        ),
        'audio trim': Command(
            name='audio trim',
            handler=handler,
            description='Trim audio silence',
            pattern=re.compile(r'^/(audio)\s+(trim)$'),
            condition=partial(has_media, audio_or_voice=True),
            is_applicable_for_reply=True,
        ),
        'media amplify': Command(
            name='audio amplify',
            handler=handler,
            description='[factor] - Amplify audio volume by the specified factor (e.g., 1.5 for 50% increase)',
            pattern=re.compile(r'^/(media)\s+(amplify)\s+(\d+(\.\d+)?)$'),
            condition=partial(has_media, any=True),
            is_applicable_for_reply=True,
        ),
        'media convert': Command(
            name='media convert',
            handler=handler,
            description='[format] - Convert media to specified format',
            pattern=re.compile(r'^/(media)\s+(convert)\s+(\w+)$'),
            condition=partial(has_media, any=True),
            is_applicable_for_reply=True,
        ),
        'media cut': Command(
            name='media cut',
            handler=handler,
            description='[HH:MM:SS HH:MM:SS] - Cut audio/video from start time to end time',
            pattern=re.compile(r'^/(media)\s+(cut)\s+(\d{2}:\d{2}:\d{2})\s+(\d{2}:\d{2}:\d{2})$'),
            condition=partial(has_media, any=True),
            is_applicable_for_reply=True,
        ),
        'media split': Command(
            name='media split',
            handler=handler,
            description='[duration]h/m/s - Split audio/video into segments of specified duration '
            '(e.g., 30m, 1h, 90s)',
            pattern=re.compile(r'^/(media)\s+(split)\s+(\d+[hms])$'),
            condition=partial(has_media, any=True),
            is_applicable_for_reply=True,
        ),
        'media merge': Command(
            name='media merge',
            handler=handler,
            description='Merge multiple files',
            pattern=re.compile(r'^/(media)\s+(merge)$'),
            condition=partial(has_media, any=True),
            is_applicable_for_reply=True,
        ),
        'media info': Command(
            name='media info',
            handler=handler,
            description='Get media info',
            pattern=re.compile(r'^/(media)\s+(info)$'),
            condition=partial(has_media, any=True),
            is_applicable_for_reply=True,
        ),
        'transcribe': Command(
            name='transcribe',
            handler=handler,
            description='[wit|whisper]: Transcribe audio or video to text and subtitle formats',
            pattern=re.compile(r'^/(media)\s+(transcribe)\s+(wit|whisper)$'),
            condition=partial(has_media, any=True),
            is_applicable_for_reply=True,
        ),
        'video create': Command(
            name='video create',
            handler=handler,
            description='Create a video from audio and subtitle files',
            pattern=re.compile(r'^/(video)\s+(create)$'),
            condition=partial(has_media, audio_or_voice=True),
            is_applicable_for_reply=True,
        ),
        'video compress': Command(
            name='video compress',
            handler=handler,
            description='[PERCENTAGE] - Compress video to target percentage of original size (20-90)',
            pattern=re.compile(r'^/(video)\s+(compress)\s+(\d{1,2})$'),
            condition=partial(has_media, video=True),
            is_applicable_for_reply=True,
        ),
        'video mute': Command(
            name='video mute',
            handler=handler,
            description='Mute video',
            pattern=re.compile(r'^/(video)\s+(mute)$'),
            condition=partial(has_media, video_or_video_note=True),
            is_applicable_for_reply=True,
        ),
        'video resize': Command(
            name='video resize',
            handler=handler,
            description='[quality] - Resize video to specified quality (144/240/360/480/720)',
            pattern=re.compile(
                rf'^/(video)\s+(resize)\s+({'|'.join(map(str, ALLOWED_VIDEO_QUALITIES))})$'
            ),
            condition=partial(has_media, video=True),
            is_applicable_for_reply=True,
        ),
        'video subtitle': Command(
            name='video subtitle',
            handler=handler,
            description='Extract subtitle streams from a video',
            pattern=re.compile(r'^/(video)\s+(subtitle)$'),
            condition=partial(has_media, video=True),
            is_applicable_for_reply=True,
        ),
        'video thumbnails': Command(
            name='video thumbnails',
            handler=handler,
            description='Generate a grid of 16 thumbnails from a video',
            pattern=re.compile(r'^/(video)\s+(thumbnails)$'),
            condition=partial(has_media, video=True),
            is_applicable_for_reply=True,
        ),
        'video update': Command(
            name='video update',
            handler=handler,
            description='Replace audio track of a video without re-encoding',
            pattern=re.compile(r'^/(video)\s+(update)$'),
            condition=partial(has_media, video=True),
            is_applicable_for_reply=True,
        ),
        'video x265': Command(
            name='video x265',
            handler=handler,
            description='[CRF] - Encode video with x265 codec and specified CRF value (20-28, lower is better quality)',
            pattern=re.compile(r'^/(video)\s+(x265)\s+(\d{2})$'),
            condition=partial(has_media, video=True),
            is_applicable_for_reply=True,
        ),
        'voice': Command(
            name='voice',
            handler=convert_to_voice_note,
            description='Convert to voice note',
            pattern=re.compile(r'^/(voice)$'),
            condition=partial(has_media, not_voice=True),
            is_applicable_for_reply=True,
        ),
    }

    @staticmethod
    def register_handlers(bot: TelegramClient) -> None:
        bot.add_event_handler(
            merge_media_add,
            NewMessage(
                func=lambda e: (
                    e.is_private
                    and (e.message.audio or e.message.voice)
                    and merge_states[e.sender_id]['state'] == MergeState.COLLECTING
                )
            ),
        )
        bot.add_event_handler(
            merge_media_process,
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
                    is_valid_reply_state(e, reply_states)
                    and re.match(r'^.+\s+-\s+.+$', e.message.text)
                )
            ),
        )
        bot.add_event_handler(
            split_media,
            NewMessage(
                func=lambda e: (
                    is_valid_reply_state(e, reply_states)
                    and re.match(r'^(\d+[hms])$', e.message.text)
                )
            ),
        )
        bot.add_event_handler(
            cut_media,
            NewMessage(
                func=lambda e: (
                    is_valid_reply_state(e, reply_states)
                    and re.match(r'^(\d{2}:\d{2}:\d{2})\s+(\d{2}:\d{2}:\d{2})$', e.message.text)
                )
            ),
        )
        bot.add_event_handler(
            video_update_process,
            NewMessage(
                func=lambda e: (
                    e.is_private
                    and e.sender_id in video_update_states
                    and video_update_states[e.sender_id]['state'] == MergeState.COLLECTING
                    and (e.audio or e.voice or e.video)
                )
            ),
        )
        bot.add_event_handler(
            video_create_process,
            NewMessage(
                func=lambda e: (
                    e.is_private
                    and e.sender_id in video_create_states
                    and video_create_states[e.sender_id]['state'] == MergeState.COLLECTING
                    and e.file.ext.lower() == '.srt'
                )
            ),
        )
