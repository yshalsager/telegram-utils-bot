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
from src.utils.filters import has_media_or_reply_with_media, is_valid_reply_state
from src.utils.json import json_options, process_dict
from src.utils.reply import ReplyState, handle_callback_query_for_reply_state, reply_states
from src.utils.run import run_command
from src.utils.telegram import delete_message_after, edit_or_send_as_file, get_reply_message

ffprobe_command = 'ffprobe -v quiet -print_format json -show_format -show_streams "{input}"'


class MergeState(Enum):
    IDLE = auto()
    COLLECTING = auto()
    MERGING = auto()


StateT = defaultdict[int, dict[str, Any]]

merge_states: StateT = defaultdict(lambda: {'state': MergeState.IDLE, 'files': []})
video_update_states: StateT = defaultdict(lambda: {'state': MergeState.IDLE, 'files': []})


async def get_media_bitrate(file_path: str) -> tuple[int, int]:
    async def get_bitrate(stream_specifier: str) -> int:
        _output, _ = await run_command(
            f'ffprobe -v error -select_streams {stream_specifier} -show_entries '
            f'stream=bit_rate -of csv=p=0 "{file_path}"'
        )
        return int(_output.strip() or 0)

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

        await stream_shell_output(event, ffmpeg_command, status_message, progress_message)
        if not output_file.exists() or not output_file.stat().st_size:
            await status_message.edit('Processing failed.')
            return

        await upload_file(event, output_file, progress_message, is_voice)

    await status_message.edit(feedback_text)


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
    if isinstance(event, CallbackQuery.Event):
        return await handle_callback_query_for_reply_state(
            event,
            'Please specify the target format (e.g., mp4, mp3, etc.)',
        )

    if event.sender_id in reply_states:
        reply_states[event.sender_id]['state'] = ReplyState.PROCESSING
        reply_message = await event.client.get_messages(
            event.chat_id, ids=reply_states[event.sender_id]['media_message_id']
        )
        target_format = event.message.text.lower()
    else:
        reply_message = await get_reply_message(event, previous=True)
        target_format = event.message.text.split('convert ')[1].lower()

    if target_format[0] == '.':
        target_format = target_format[1:]

    if target_format not in ALLOWED_VIDEO_FORMATS | ALLOWED_AUDIO_FORMATS:
        await event.reply(
            'Unsupported media type for conversion.\n'
            f'Allowed formats: {", ".join(ALLOWED_VIDEO_FORMATS | ALLOWED_AUDIO_FORMATS)}'
        )
        return None

    if reply_message.file.ext == target_format:
        await event.reply(f'The file is already in {target_format} format. Skipping conversion.')
        return None

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
    if event.sender_id in reply_states:
        del reply_states[event.sender_id]
    raise StopPropagation


ALLOWED_VIDEO_QUALITIES = {144, 240, 360, 480, 720}


async def resize_video(event: NewMessage.Event | CallbackQuery.Event) -> None:
    if isinstance(event, CallbackQuery.Event):
        return await handle_callback_query_for_reply_state(
            event,
            f'Please specify the target quality ({'/'.join(map(str, ALLOWED_VIDEO_QUALITIES))})',
        )

    if event.sender_id in reply_states:
        reply_states[event.sender_id]['state'] = ReplyState.PROCESSING
        reply_message = await event.client.get_messages(
            event.chat_id, ids=reply_states[event.sender_id]['media_message_id']
        )
        quality = event.message.text
    else:
        reply_message = await get_reply_message(event, previous=True)
        quality = event.message.text.split('resize ')[1]

    quality = int(quality)
    if quality not in ALLOWED_VIDEO_QUALITIES:
        await event.reply(
            f'Invalid quality. Please choose from {", ".join(map(str, ALLOWED_VIDEO_QUALITIES))}.'
        )
        return None

    ffmpeg_command = (
        f'ffmpeg -hide_banner -y -i "{{input}}" -filter_complex '
        f'"scale=width=-1:height={quality}:force_original_aspect_ratio=decrease,pad=ceil(iw/2)*2:ceil(ih/2)*2" '
        f'-c:v libx264 -b:v {{video_bitrate}} -maxrate {{video_bitrate}} -bufsize {{video_bitrate}} '
        f'-c:a copy "{{output}}"'
    )
    await process_media(
        event, ffmpeg_command, reply_message.file.ext, reply_message=reply_message, get_bitrate=True
    )
    if event.sender_id in reply_states:
        del reply_states[event.sender_id]
    raise StopPropagation


async def video_update_initial(event: NewMessage.Event | CallbackQuery.Event) -> None:
    video_update_states[event.sender_id]['state'] = MergeState.COLLECTING
    video_update_states[event.sender_id]['files'] = []
    reply_message = await get_reply_message(event, previous=True)
    video_update_states[event.sender_id]['files'].append(reply_message.id)
    await event.reply(
        'Send the media file with audio to use.',
        reply_to=reply_message.id,
        buttons=Button.force_reply(),
    )


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
    if event.sender_id in reply_states:
        del reply_states[event.sender_id]


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


handlers = {
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
    'video mute': mute_video,
    'video resize': resize_video,
    'video subtitle': extract_subtitle,
    'video thumbnails': video_thumbnails,
    'video update': video_update_initial,
    'voice': convert_to_voice_note,
}


async def handler(event: NewMessage.Event | CallbackQuery.Event) -> None:
    if isinstance(event, CallbackQuery.Event):
        command = event.data.decode('utf-8')
        if command.startswith('m|'):
            command = command[2:]
        command = command.replace('_', ' ')
        if '|' in command:
            command, _ = command.split('|', 1)
    else:
        command = ' '.join(' '.join(event.pattern_match.groups()).split(' ')[:2])
    if command not in handlers:
        await event.reply('Command not found.')
        return

    await handlers[command](event)


class Media(ModuleBase):
    name = 'Media'
    description = 'Media processing commands'
    commands: ClassVar[ModuleBase.CommandsT] = {
        'audio compress': Command(
            name='audio compress',
            handler=handler,
            description='[bitrate] - compress audio to [bitrate] kbps',
            pattern=re.compile(r'^/(audio)\s+(compress)\s+(\d+)$'),
            condition=partial(has_media_or_reply_with_media, audio=True),
            is_applicable_for_reply=True,
        ),
        'audio convert': Command(
            name='audio convert',
            handler=handler,
            description='Convert a video or voice note to an audio',
            pattern=re.compile(r'^/(audio)\s+(convert)$'),
            condition=partial(has_media_or_reply_with_media, not_audio=True),
            is_applicable_for_reply=True,
        ),
        'audio metadata': Command(
            name='audio metadata',
            handler=handler,
            description='[title] - [artist] - Set title and artist of an audio file',
            pattern=re.compile(r'^/(audio)\s+(metadata)\s+.+\s+-\s+.+$'),
            condition=partial(has_media_or_reply_with_media, audio=True),
            is_applicable_for_reply=True,
        ),
        'audio trim': Command(
            name='audio trim',
            handler=handler,
            description='Trim audio silence',
            pattern=re.compile(r'^/(audio)\s+(trim)$'),
            condition=partial(has_media_or_reply_with_media, audio_or_voice=True),
            is_applicable_for_reply=True,
        ),
        'media amplify': Command(
            name='audio amplify',
            handler=handler,
            description='[factor] - Amplify audio volume by the specified factor (e.g., 1.5 for 50% increase)',
            pattern=re.compile(r'^/(media)\s+(amplify)\s+(\d+(\.\d+)?)$'),
            condition=partial(has_media_or_reply_with_media, any=True),
            is_applicable_for_reply=True,
        ),
        'media convert': Command(
            name='media convert',
            handler=handler,
            description='[format] - Convert media to specified format',
            pattern=re.compile(r'^/(media)\s+(convert)\s+(\w+)$'),
            condition=partial(has_media_or_reply_with_media, any=True),
            is_applicable_for_reply=True,
        ),
        'media cut': Command(
            name='media cut',
            handler=handler,
            description='[HH:MM:SS HH:MM:SS] - Cut audio/video from start time to end time',
            pattern=re.compile(r'^/(media)\s+(cut)\s+(\d{2}:\d{2}:\d{2})\s+(\d{2}:\d{2}:\d{2})$'),
            condition=partial(has_media_or_reply_with_media, any=True),
            is_applicable_for_reply=True,
        ),
        'media split': Command(
            name='media split',
            handler=handler,
            description='[duration]h/m/s - Split audio/video into segments of specified duration '
            '(e.g., 30m, 1h, 90s)',
            pattern=re.compile(r'^/(media)\s+(split)\s+(\d+[hms])$'),
            condition=partial(has_media_or_reply_with_media, any=True),
            is_applicable_for_reply=True,
        ),
        'media merge': Command(
            name='media merge',
            handler=handler,
            description='Merge multiple files',
            pattern=re.compile(r'^/(media)\s+(merge)$'),
            condition=partial(has_media_or_reply_with_media, any=True),
            is_applicable_for_reply=True,
        ),
        'media info': Command(
            name='media info',
            handler=handler,
            description='Get media info',
            pattern=re.compile(r'^/(media)\s+(info)$'),
            condition=partial(has_media_or_reply_with_media, any=True),
            is_applicable_for_reply=True,
        ),
        'video mute': Command(
            name='video mute',
            handler=handler,
            description='Mute video',
            pattern=re.compile(r'^/(video)\s+(mute)$'),
            condition=partial(has_media_or_reply_with_media, video_or_video_note=True),
            is_applicable_for_reply=True,
        ),
        'video resize': Command(
            name='video resize',
            handler=handler,
            description='[quality] - Resize video to specified quality (144/240/360/480/720)',
            pattern=re.compile(
                rf'^/(video)\s+(resize)\s+({'|'.join(map(str, ALLOWED_VIDEO_QUALITIES))})$'
            ),
            condition=partial(has_media_or_reply_with_media, video=True),
            is_applicable_for_reply=True,
        ),
        'video subtitle': Command(
            name='video subtitle',
            handler=handler,
            description='Extract subtitle streams from a video',
            pattern=re.compile(r'^/(video)\s+(subtitle)$'),
            condition=partial(has_media_or_reply_with_media, video=True),
            is_applicable_for_reply=True,
        ),
        'video thumbnails': Command(
            name='video thumbnails',
            handler=handler,
            description='Generate a grid of 16 thumbnails from a video',
            pattern=re.compile(r'^/(video)\s+(thumbnails)$'),
            condition=partial(has_media_or_reply_with_media, video=True),
            is_applicable_for_reply=True,
        ),
        'video update': Command(
            name='video update',
            handler=handler,
            description='Replace audio track of a video without re-encoding',
            pattern=re.compile(r'^/(video)\s+(update)$'),
            condition=partial(has_media_or_reply_with_media, video=True),
            is_applicable_for_reply=True,
        ),
        'voice': Command(
            name='voice',
            handler=convert_to_voice_note,
            description='Convert to voice note',
            pattern=re.compile(r'^/(voice)$'),
            condition=partial(has_media_or_reply_with_media, not_voice=True),
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
            convert_media,
            NewMessage(
                func=lambda e: (
                    is_valid_reply_state(e)
                    and re.match(
                        rf'^({'|'.join(map(str, ALLOWED_AUDIO_FORMATS | ALLOWED_VIDEO_FORMATS))})$',
                        e.message.text,
                    )
                )
            ),
        )
        bot.add_event_handler(
            set_metadata,
            NewMessage(
                func=lambda e: (
                    is_valid_reply_state(e) and re.match(r'^.+\s+-\s+.+$', e.message.text)
                )
            ),
        )
        bot.add_event_handler(
            split_media,
            NewMessage(
                func=lambda e: (
                    is_valid_reply_state(e) and re.match(r'^(\d+[hms])$', e.message.text)
                )
            ),
        )
        bot.add_event_handler(
            cut_media,
            NewMessage(
                func=lambda e: (
                    is_valid_reply_state(e)
                    and re.match(r'^(\d{2}:\d{2}:\d{2})\s+(\d{2}:\d{2}:\d{2})$', e.message.text)
                )
            ),
        )
        bot.add_event_handler(
            resize_video,
            NewMessage(
                func=lambda e: (
                    is_valid_reply_state(e)
                    and re.match(
                        rf'^({"|".join(map(str, ALLOWED_VIDEO_QUALITIES))})$', e.message.text
                    )
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
