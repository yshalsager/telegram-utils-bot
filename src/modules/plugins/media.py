from functools import partial
from math import floor
from os import getenv
from pathlib import Path
from shlex import quote
from shutil import rmtree
from sys import executable
from typing import Any, ClassVar, cast
from uuid import uuid4

import aiohttp
import orjson
import regex as re
from llm import Attachment, get_model
from pydub import AudioSegment
from pydub.silence import split_on_silence
from telethon import TelegramClient
from telethon.events import CallbackQuery, NewMessage, StopPropagation
from telethon.tl.custom import Message
from telethon.tl.types import DocumentAttributeAudio, DocumentAttributeVideo

from src import TMP_DIR
from src.modules.base import ModuleBase
from src.modules.plugins.run import stream_shell_output
from src.utils.command import Command
from src.utils.downloads import (
    download_file,
    download_to_temp_file,
    get_download_name,
    upload_file,
    upload_file_and_cleanup,
)
from src.utils.filters import has_media
from src.utils.i18n import t
from src.utils.json_processing import json_options, process_dict
from src.utils.run import run_command
from src.utils.subtitles import srt_to_txt
from src.utils.telegram import (
    delete_callback_after,
    delete_message_after,
    edit_or_send_as_file,
    get_reply_message,
    inline_choice_grid,
    send_progress_message,
)

ffprobe_command = 'ffprobe -v quiet -print_format json -show_format -show_streams "{input}"'

ALLOWED_SPEED_FACTORS = [1.25, 1.5, 1.75, 2.0, 2.25, 2.5, 2.75, 3.0]
ALLOWED_AUDIO_COMPRESS_BITRATES = [16, 32, 48, 64, 96, 128]
ALLOWED_AMPLIFY_FACTORS = [1.25, 1.5, 1.75, 2.0, 2.25, 2.5, 2.75, 3.0]
ALLOWED_VIDEO_COMPRESS_PERCENTAGES = list(range(20, 100, 10))
ALLOWED_VIDEO_X265_CRF = [18, 20, 22, 24, 26, 28, 30]
ALLOWED_TRANSCRIBE_METHODS = ['wit', 'whisper', 'vosk', 'google']
GOOGLE_SPEECH_V2_API_KEY = (
    getenv('GOOGLE_SPEECH_V2_KEY') or 'AIzaSyBOti4mM-6x9WDnZIjIeyEU21OpBXqWBgw'
)
GOOGLE_SPEECH_V2_API_URL = 'https://www.google.com/speech-api/v2/recognize?output=json&client=chromium&lang={lang}&key={key}'
TIME_RANGES_PATTERN = re.compile(
    r'^(\d{2}:\d{2}:\d{2}\s+\d{2}:\d{2}:\d{2}(\s+\d{2}:\d{2}:\d{2}\s+\d{2}:\d{2}:\d{2})*)$'
)
TIME_RANGE_PATTERN = re.compile(r'(\d{2}:\d{2}:\d{2})\s+(\d{2}:\d{2}:\d{2})')


def parse_timestamp(time_text: str) -> int:
    hours, minutes, seconds = [int(part) for part in time_text.split(':')]
    if minutes > 59 or seconds > 59:
        raise ValueError
    return hours * 3600 + minutes * 60 + seconds


def format_timestamp(seconds: float) -> str:
    hours, remainder = divmod(floor(seconds), 3600)
    minutes, seconds = divmod(remainder, 60)
    return f'{hours:02d}:{minutes:02d}:{seconds:02d}'


def format_ffmpeg_time(seconds: float) -> str:
    if seconds == floor(seconds):
        return str(floor(seconds))
    return f'{seconds:.3f}'.rstrip('0').rstrip('.')


def parse_time_ranges(time_ranges_text: str) -> list[tuple[int, int]]:
    ranges = []
    for start_time, end_time in TIME_RANGE_PATTERN.findall(time_ranges_text):
        start_seconds = parse_timestamp(start_time)
        end_seconds = parse_timestamp(end_time)
        if start_seconds >= end_seconds:
            raise ValueError
        ranges.append((start_seconds, end_seconds))
    if not ranges:
        raise ValueError
    return ranges


def merge_time_ranges(ranges: list[tuple[float, float]]) -> list[tuple[float, float]]:
    merged_ranges: list[tuple[float, float]] = []
    for start_seconds, end_seconds in sorted(ranges):
        if merged_ranges and start_seconds <= merged_ranges[-1][1]:
            merged_ranges[-1] = (
                merged_ranges[-1][0],
                max(merged_ranges[-1][1], end_seconds),
            )
            continue
        merged_ranges.append((start_seconds, end_seconds))
    return merged_ranges


def invert_time_ranges(ranges: list[tuple[int, int]], duration: float) -> list[tuple[float, float]]:
    remove_ranges = merge_time_ranges(
        [
            (start_seconds, min(end_seconds, duration))
            for start_seconds, end_seconds in ranges
            if start_seconds < duration
        ]
    )
    keep_ranges = []
    cursor = 0.0
    for start_seconds, end_seconds in remove_ranges:
        if start_seconds > cursor:
            keep_ranges.append((cursor, start_seconds))
        cursor = max(cursor, end_seconds)
    if cursor < duration:
        keep_ranges.append((cursor, duration))
    return keep_ranges


async def get_stream_info(stream_specifier: str, file_path: Path) -> dict[str, Any]:
    output, _ = await run_command(
        f'ffprobe -v error -select_streams {stream_specifier} -show_entries '
        f'stream=codec_name,duration,width,height -of json "{file_path}"'
    )
    info = orjson.loads(output)
    return cast(dict[str, Any], info['streams'][0]) if info and info.get('streams') else {}


async def get_format_info(file_path: Path) -> dict[str, Any]:
    output, _ = await run_command(
        f'ffprobe -v error -show_entries format=duration,tags -of json "{file_path}"'
    )
    info = orjson.loads(output)
    return cast(dict[str, Any], info['format']) if info.get('format') else {}


async def get_output_info(file_path: Path) -> dict[str, Any]:
    video_info = await get_stream_info('v:0', file_path)
    audio_info = await get_stream_info('a:0', file_path)
    format_info = await get_format_info(file_path)

    return {
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


async def build_media_upload_params(
    output_file: Path,
    *,
    is_voice: bool = False,
) -> dict[str, Any]:
    output_info = await get_output_info(output_file)
    if output_info.get('vcodec') == 'none':
        attributes = [
            DocumentAttributeAudio(
                duration=int(output_info.get('duration', 0)),
                title=output_info.get('title'),
                performer=output_info.get('uploader'),
                voice=is_voice or None,
            )
        ]
        supports_streaming = False
        mime_type = None
    else:
        attributes = [
            DocumentAttributeVideo(
                duration=int(output_info.get('duration', 0)),
                w=output_info.get('width', 0),
                h=output_info.get('height', 0),
                supports_streaming=True,
            )
        ]
        supports_streaming = True
        mime_type = 'video/mp4' if output_file.suffix.lower() == '.mp4' else None

    return {
        'attributes': attributes,
        'supports_streaming': supports_streaming,
        'mime_type': mime_type,
    }


async def get_media_bitrate(file_path: str) -> tuple[int, int]:
    def parse_numeric_output(output: str) -> int:
        for line in output.splitlines():
            value = line.strip()
            if value.isdigit():
                return int(value)
        return 0

    async def get_bitrate(stream_specifier: str) -> int:
        _output, _ = await run_command(
            f'ffprobe -v error -select_streams {stream_specifier} -show_entries '
            f'stream=bit_rate -of csv=p=0 "{file_path}"'
        )
        return parse_numeric_output(_output)

    video_bitrate = await get_bitrate('v:0')
    audio_bitrate = await get_bitrate('a:0')

    if video_bitrate == 0 and audio_bitrate == 0:
        output, _ = await run_command(
            f'ffprobe -v error -show_entries format=bit_rate -of csv=p=0 "{file_path}"'
        )
        # Assume it's all audio if we couldn't get separate streams
        audio_bitrate = parse_numeric_output(output)

    return video_bitrate, audio_bitrate


def get_google_transcript(response_text: str) -> str | None:
    for line in response_text.splitlines():
        if not line.strip():
            continue
        result = orjson.loads(line)
        results = result.get('result') or []
        if not results:
            continue
        alternatives = results[0].get('alternative', [])
        if not alternatives:
            continue
        transcript = alternatives[0].get('transcript')
        if transcript:
            return transcript[:1].upper() + transcript[1:]
    return None


async def transcribe_with_google(
    input_file_path: Path, output_dir: Path, language: str
) -> Path | None:
    audio_file_path = output_dir / f'{input_file_path.stem}.wav'
    output, status_code = await run_command(
        f'ffmpeg -hide_banner -y -i "{input_file_path}" -vn -acodec pcm_s16le -ac 1 -ar 16000 "{audio_file_path}"'
    )
    if status_code != 0:
        raise RuntimeError(output)

    async with (
        aiohttp.ClientSession() as session,
        session.post(
            GOOGLE_SPEECH_V2_API_URL.format(lang=language, key=GOOGLE_SPEECH_V2_API_KEY),
            data=audio_file_path.read_bytes(),
            headers={'Content-Type': 'audio/l16; rate=16000;'},
        ) as response,
    ):
        transcript = get_google_transcript(await response.text())

    audio_file_path.unlink(missing_ok=True)
    if not transcript:
        return None

    output_file = output_dir / f'{input_file_path.stem}.txt'
    output_file.write_text(transcript)
    return output_file


async def process_media(
    event: NewMessage.Event | CallbackQuery.Event,
    ffmpeg_command: str,
    output_suffix: str,
    reply_message: Message | None = None,
    is_voice: bool = False,
    get_file_name: bool = True,
    get_bitrate: bool = False,
    feedback_text: str = t('file_processed'),
) -> dict[str, Any]:
    data: dict[str, Any] = {}
    if not reply_message:
        reply_message = await get_reply_message(event, previous=True)
    status_message = await send_progress_message(event, t('starting_process'))
    progress_message = await send_progress_message(event, f'<pre>{t("process_output")}:</pre>')

    async with download_to_temp_file(
        event, reply_message, progress_message, temp_dir=TMP_DIR
    ) as temp_file_path:
        if get_file_name:
            input_file = get_download_name(reply_message)
            output_file = (temp_file_path.parent / input_file).with_suffix(output_suffix)
            if output_file.name == input_file.name:
                output_file = output_file.with_name(f'_{output_file.name}')
        else:
            output_file = temp_file_path.with_suffix(output_suffix)

        input_path = str(temp_file_path)
        if get_bitrate:
            video_bitrate, audio_bitrate = await get_media_bitrate(input_path)
            ffmpeg_command = ffmpeg_command.format(
                input=input_path,
                output=output_file,
                video_bitrate=video_bitrate,
                audio_bitrate=audio_bitrate,
            )
        else:
            ffmpeg_command = ffmpeg_command.format(input=input_path, output=output_file)

        status = await stream_shell_output(event, ffmpeg_command, status_message, progress_message)
        data['status_text'] = status
        failed_marker = t('process_failed_with_return_code', code=1).split('1', 1)[0]
        if failed_marker and failed_marker in status:
            await status_message.edit(t('process_failed'))
            return data
        if not output_file.exists() or not output_file.stat().st_size:
            await status_message.edit(t('process_failed'))
            return data
        upload_params = await build_media_upload_params(output_file, is_voice=is_voice)

        await upload_file(
            event,
            output_file,
            progress_message,
            is_voice,
            force_document=False,
            **upload_params,
        )
        data['output_size'] = output_file.stat().st_size

        output_file.unlink(missing_ok=True)

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
        feedback_text=t('converted_to_voice_note'),
    )


async def compress_audio(event: NewMessage.Event | CallbackQuery.Event) -> None:
    delete_message_after_process = False
    if isinstance(event, CallbackQuery.Event):
        audio_bitrate = await inline_choice_grid(
            event,
            prefix='m|audio_compress|',
            prompt_text=f'{t("choose_bitrate")}:',
            pairs=[
                (f'{bitrate}kbps', f'm|audio_compress|{bitrate}')
                for bitrate in ALLOWED_AUDIO_COMPRESS_BITRATES
            ],
            cols=3,
            cast=str,
        )
        if audio_bitrate is None:
            return
        delete_message_after_process = True
    elif match := re.search(r'(\d+)$', event.message.text):
        audio_bitrate = match.group(1)
    else:
        await event.reply(t('invalid_bitrate'))
        return
    ffmpeg_command = (
        f'ffmpeg -hide_banner -y -i "{{input}}" -vn -c:a aac -b:a {audio_bitrate}k "{{output}}"'
    )
    await process_media(
        event,
        ffmpeg_command,
        '.m4a',
        feedback_text=t('audio_successfully_compressed'),
    )
    if delete_message_after_process:
        delete_callback_after(event)


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
        feedback_text=t('converted_to_audio'),
    )


async def _cut_media_process(
    event: NewMessage.Event,
    reply_message: Message | None,
    match: Any,
) -> None:
    assert reply_message is not None

    try:
        cut_points = parse_time_ranges(match.group(1))
    except ValueError:
        await event.reply(t('invalid_time_format'))
        return

    status_message = await send_progress_message(event, t('starting_cut'))
    progress_message = await send_progress_message(event, f'<pre>{t("process_output")}:</pre>')

    async with download_to_temp_file(
        event,
        reply_message,
        progress_message,
        suffix=reply_message.file.ext,
    ) as temp_file_path:
        input_file = get_download_name(reply_message)
        output_file_base = (temp_file_path.parent / input_file).with_suffix('')

        for idx, (start_seconds, end_seconds) in enumerate(cut_points, 1):
            output_file = output_file_base.with_name(
                f'{output_file_base.stem}_cut_{idx}{reply_message.file.ext}'
            )
            ffmpeg_command = (
                f'ffmpeg -hide_banner -y -i "{temp_file_path}" '
                f'-ss {format_ffmpeg_time(start_seconds)} -to {format_ffmpeg_time(end_seconds)} '
                f'-c copy -map 0 "{output_file}"'
            )
            await stream_shell_output(event, ffmpeg_command, status_message, progress_message)
            if output_file.exists() and output_file.stat().st_size:
                upload_params = await build_media_upload_params(
                    output_file, is_voice=bool(reply_message.voice)
                )
                await upload_file_and_cleanup(
                    event,
                    output_file,
                    progress_message,
                    is_voice=bool(reply_message.voice),
                    caption=f'{format_timestamp(start_seconds)} - {format_timestamp(end_seconds)}',
                    **upload_params,
                )
            else:
                await status_message.edit(t('cut_failed_for_item', item=idx))

    await status_message.edit(t('cut_completed'))
    raise StopPropagation


async def cut_media(event: NewMessage.Event | CallbackQuery.Event) -> None:
    if isinstance(event, CallbackQuery.Event):
        await event.client.reply_prompts.ask(
            event,
            f'{t("enter_cut_points")} (<code>00:00:00 00:30:00 00:45:00 01:15:00</code>)',
            pattern=TIME_RANGES_PATTERN,
            handler=_cut_media_process,
            invalid_reply_text=t('invalid_cut_points'),
        )
        return

    reply_message = await get_reply_message(event, previous=True)

    if not (
        match := re.match(
            r'^/media\s+cut\s+(\d{2}:\d{2}:\d{2}\s+\d{2}:\d{2}:\d{2}(\s+\d{2}:\d{2}:\d{2}\s+\d{2}:\d{2}:\d{2})*)$',
            event.message.text,
        )
    ):
        await event.reply(t('invalid_cut_points'))
        return

    await _cut_media_process(event, reply_message, match)
    raise StopPropagation


async def _crop_out_media_process(
    event: NewMessage.Event,
    reply_message: Message | None,
    match: Any,
) -> None:
    assert reply_message is not None

    try:
        crop_out_points = parse_time_ranges(match.group(1))
    except ValueError:
        await event.reply(t('invalid_time_format'))
        return

    status_message = await send_progress_message(event, t('starting_crop_out'))
    progress_message = await send_progress_message(event, f'<pre>{t("process_output")}:</pre>')

    async with download_to_temp_file(
        event,
        reply_message,
        progress_message,
        suffix=reply_message.file.ext,
    ) as temp_file_path:
        output_info = await get_output_info(temp_file_path)
        duration = output_info.get('duration', 0)
        if not duration or not any(
            start_seconds < duration for start_seconds, _ in crop_out_points
        ):
            await status_message.edit(t('invalid_crop_out_points'))
            return
        keep_ranges = invert_time_ranges(crop_out_points, duration)
        if not keep_ranges:
            await status_message.edit(t('invalid_crop_out_points'))
            return

        input_file = get_download_name(reply_message)
        output_file_base = (temp_file_path.parent / input_file).with_suffix('')
        file_list_path = temp_file_path.parent / 'crop_out_files.txt'

        with file_list_path.open('w') as file_list:
            for idx, (start_seconds, end_seconds) in enumerate(keep_ranges, 1):
                segment_file = output_file_base.with_name(
                    f'{output_file_base.stem}_crop_out_{idx}{reply_message.file.ext}'
                )
                ffmpeg_command = (
                    f'ffmpeg -hide_banner -y -ss {format_ffmpeg_time(start_seconds)} '
                    f'-i "{temp_file_path}" -t {format_ffmpeg_time(end_seconds - start_seconds)} '
                    f'-c copy -map 0 "{segment_file}"'
                )
                await stream_shell_output(event, ffmpeg_command, status_message, progress_message)
                if not segment_file.exists() or not segment_file.stat().st_size:
                    await status_message.edit(t('crop_out_failed'))
                    return
                file_list.write(f"file '{segment_file.absolute()}'\n")

        output_file = output_file_base.with_name(
            f'{output_file_base.stem}_crop_out{reply_message.file.ext}'
        )
        ffmpeg_command = f'ffmpeg -hide_banner -y -f concat -safe 0 -i "{file_list_path}" -c copy "{output_file}"'
        await stream_shell_output(event, ffmpeg_command, status_message, progress_message)
        if output_file.exists() and output_file.stat().st_size:
            upload_params = await build_media_upload_params(
                output_file, is_voice=bool(reply_message.voice)
            )
            await upload_file_and_cleanup(
                event,
                output_file,
                progress_message,
                is_voice=bool(reply_message.voice),
                **upload_params,
            )
            await status_message.edit(t('crop_out_completed'))
        else:
            await status_message.edit(t('crop_out_failed'))

    raise StopPropagation


async def crop_out_media(event: NewMessage.Event | CallbackQuery.Event) -> None:
    if isinstance(event, CallbackQuery.Event):
        await event.client.reply_prompts.ask(
            event,
            f'{t("enter_crop_out_points")} (<code>00:01:00 00:02:00 00:05:00 00:05:30</code>)',
            pattern=TIME_RANGES_PATTERN,
            handler=_crop_out_media_process,
            invalid_reply_text=t('invalid_crop_out_points'),
        )
        return

    reply_message = await get_reply_message(event, previous=True)

    if not (
        match := re.match(
            r'^/media\s+crop\s+out\s+(\d{2}:\d{2}:\d{2}\s+\d{2}:\d{2}:\d{2}(\s+\d{2}:\d{2}:\d{2}\s+\d{2}:\d{2}:\d{2})*)$',
            event.message.text,
        )
    ):
        await event.reply(t('invalid_crop_out_points'))
        return

    await _crop_out_media_process(event, reply_message, match)
    raise StopPropagation


async def _split_media_process(
    event: NewMessage.Event,
    reply_message: Message | None,
    match: Any,
) -> None:
    assert reply_message is not None
    args = match.group(1)

    unit = args[-1] if args[-1].isalpha() else 's'
    duration = int(args[:-1])
    if unit == 'h':
        segment_duration = duration * 3600
    elif unit == 'm':
        segment_duration = duration * 60
    else:
        segment_duration = duration
    status_message = await send_progress_message(event, t('starting_process'))
    progress_message = await send_progress_message(event, f'<pre>{t("process_output")}:</pre>')

    async with download_to_temp_file(
        event,
        reply_message,
        progress_message,
        suffix=reply_message.file.ext,
    ) as temp_file_path:
        input_file = get_download_name(reply_message)
        output_file_base = (temp_file_path.parent / input_file).with_suffix('')

        output_pattern = f'{output_file_base.stem}_segment_%03d{input_file.suffix}'
        ffmpeg_command = (
            f'ffmpeg -hide_banner -y -i "{temp_file_path}" -f segment -segment_time {segment_duration} '
            f'-c copy "{output_file_base.parent / output_pattern}"'
        )
        await stream_shell_output(event, ffmpeg_command, status_message, progress_message)

        for output_file in sorted(
            output_file_base.parent.glob(f'{output_file_base.stem}_segment_*{input_file.suffix}')
        ):
            if output_file.exists() and output_file.stat().st_size:
                upload_params = await build_media_upload_params(
                    output_file, is_voice=bool(reply_message.voice)
                )
                await upload_file_and_cleanup(
                    event,
                    output_file,
                    progress_message,
                    is_voice=bool(reply_message.voice),
                    caption=f'<code>{output_file.stem}</code>',
                    **upload_params,
                )
            else:
                await status_message.edit(t('process_failed_for_file', file_name=output_file.name))

    await progress_message.edit(t('file_split_and_uploaded'))
    raise StopPropagation


async def split_media(event: NewMessage.Event | CallbackQuery.Event) -> None:
    if isinstance(event, CallbackQuery.Event):
        await event.client.reply_prompts.ask(
            event,
            t('enter_split_duration'),
            pattern=re.compile(r'^(\d+[hms])$'),
            handler=_split_media_process,
            invalid_reply_text=t('enter_split_duration'),
        )
        return

    reply_message = await get_reply_message(event, previous=True)
    args = (
        event.message.text.split()[2] if len(event.message.text.split()) > 2 else event.message.text
    )
    match = re.match(r'^(\d+[hms])$', args)
    if not match:
        await event.reply(t('enter_split_duration'))
        raise StopPropagation

    await _split_media_process(event, reply_message, match)
    raise StopPropagation


async def media_info(event: NewMessage.Event | CallbackQuery.Event) -> None:
    reply_message = await get_reply_message(event, previous=True)
    progress_message = await send_progress_message(event, t('starting_process'))

    async with download_to_temp_file(
        event,
        reply_message,
        progress_message,
        suffix=reply_message.file.ext,
    ) as temp_file_path:
        output, code = await run_command(ffprobe_command.format(input=temp_file_path))
        if code:
            message = f'{t("failed_to_get_info")}\n<pre>{output}</pre>'
        else:
            info = orjson.dumps(process_dict(orjson.loads(output)), option=json_options).decode()
            message = f'<pre>{info}</pre>'
        await edit_or_send_as_file(event, progress_message, message)


async def _set_metadata_process(
    event: NewMessage.Event,
    reply_message: Message | None,
    match: Any,
) -> None:
    assert reply_message is not None
    title, artist = match.group(1), match.group(2)

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
        feedback_text=t('audio_metadata_set'),
    )
    raise StopPropagation


async def set_metadata(event: NewMessage.Event | CallbackQuery.Event) -> None:
    if isinstance(event, CallbackQuery.Event):
        await event.client.reply_prompts.ask(
            event,
            t('enter_title_and_artist'),
            pattern=re.compile(r'^(.+)\s+-\s+(.+)$'),
            handler=_set_metadata_process,
            invalid_reply_text=t('enter_title_and_artist'),
        )
        return

    reply_message = await get_reply_message(event, previous=True)
    text = event.message.text.split('metadata ', 1)[-1]
    match = re.match(r'^(.+)\s+-\s+(.+)$', text)
    if not match:
        await event.reply(t('enter_title_and_artist'))
        raise StopPropagation

    await _set_metadata_process(event, reply_message, match)
    raise StopPropagation


async def merge_media_initial(event: NewMessage.Event | CallbackQuery.Event) -> None:
    reply_message = await get_reply_message(event, previous=True)
    await event.client.file_collectors.start(
        event,
        t('send_more_files'),
        first_message_id=reply_message.id,
        accept=lambda e: bool(
            e.message.audio or e.message.voice or e.message.video or e.message.video_note
        ),
        on_finish=_merge_media_process,
        min_files=2,
        not_enough_files_text=t('not_enough_files'),
        added_reply_text=t('file_added'),
        finish_button_text=t('finish'),
        allow_non_reply=True,
        reply_to=reply_message.id,
    )
    raise StopPropagation


async def _merge_media_process(event: CallbackQuery.Event, files: list[int]) -> None:
    await event.answer(t('merging'))
    status_message = await event.respond(t('starting_merge'))
    progress_message = await event.respond(f'<pre>{t("process_output")}:</pre>')

    output_dir = Path(TMP_DIR / str(uuid4()))
    output_dir.mkdir(parents=True, exist_ok=True)
    file_list_path = output_dir / 'files.txt'
    message: Message
    try:
        with file_list_path.open('w') as file_list:
            for idx, file_id in enumerate(files, start=1):
                message = await event.client.get_messages(event.chat_id, ids=file_id)
                input_path = output_dir / f'input_{idx:03d}{message.file.ext}'
                with input_path.open('wb') as temp_file:
                    await download_file(event, temp_file, message, progress_message)
                file_list.write(f"file '{input_path.absolute()}'\n")

        output_file_path = output_dir / f'merged{message.file.ext}'
        ffmpeg_command = f'ffmpeg -hide_banner -y -f concat -safe 0 -i "{file_list_path}" -c copy "{output_file_path}"'
        await stream_shell_output(event, ffmpeg_command, status_message, progress_message)
        if output_file_path.exists() and output_file_path.stat().st_size:
            upload_params = await build_media_upload_params(
                output_file_path, is_voice=message.voice is not None
            )
            await upload_file_and_cleanup(
                event,
                output_file_path,
                progress_message,
                is_voice=message.voice is not None,
                **upload_params,
            )
            await status_message.edit(t('merge_completed'))
        else:
            await status_message.edit(t('merge_failed'))

    finally:
        rmtree(output_dir, ignore_errors=True)


async def trim_silence(event: NewMessage.Event) -> None:
    reply_message = await get_reply_message(event, previous=True)
    status_message = await send_progress_message(event, t('starting_silence_trimming'))
    progress_message = await send_progress_message(event, f'<pre>{t("process_output")}:</pre>')
    extension = reply_message.file.ext

    async with download_to_temp_file(
        event,
        reply_message,
        progress_message,
        suffix=extension,
    ) as input_file_path:
        output_file_path = input_file_path.with_suffix('.mp3')
        if reply_message.file.name:
            output_file_path = output_file_path.with_name(
                f'trimmed_{reply_message.file.name}'
            ).with_suffix('.mp3')

        await progress_message.edit(t('loading_file'))
        sound = AudioSegment.from_file(input_file_path)
        await progress_message.edit(t('splitting'))
        chunks = split_on_silence(sound, min_silence_len=500, silence_thresh=-40)
        await progress_message.edit(t('combining'))
        combined = AudioSegment.empty()
        for chunk in chunks:
            combined += chunk
        await progress_message.edit(t('exporting'))
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
            await status_message.edit(t('silence_trimming_failed'))
            return

        upload_params = await build_media_upload_params(
            output_file_path, is_voice=bool(reply_message.voice)
        )
        await upload_file_and_cleanup(
            event,
            output_file_path,
            progress_message,
            is_voice=bool(reply_message.voice),
            caption=t('trimmed_audio'),
            **upload_params,
        )

    await status_message.edit(t('silence_trimmed'))


async def mute_video(event: NewMessage.Event) -> None:
    ffmpeg_command = 'ffmpeg -hide_banner -y -i "{input}" -c copy -an "{output}"'
    await process_media(
        event,
        ffmpeg_command,
        '.mp4',
        feedback_text=t('audio_removed_from_video'),
    )


async def extract_subtitle(event: NewMessage.Event) -> None:
    reply_message = await get_reply_message(event, previous=True)
    status_message = await send_progress_message(event, t('starting_subtitle_extraction'))
    progress_message = await send_progress_message(event, f'<pre>{t("process_output")}:</pre>')

    async with download_to_temp_file(
        event,
        reply_message,
        progress_message,
        suffix=reply_message.file.ext,
    ) as input_file_path:
        output, code = await run_command(
            f'ffprobe -v quiet -print_format json -show_streams "{input_file_path}"'
        )
        if code:
            await status_message.edit(t('failed_to_get_stream_info'))
            return

        streams = orjson.loads(output)['streams']
        subtitle_streams = [s for s in streams if s['codec_type'] == 'subtitle']

        if not subtitle_streams:
            await status_message.edit(t('no_subtitle_streams'))
            return

        for i, stream in enumerate(subtitle_streams):
            ext = 'srt' if stream['codec_name'] == 'mov_text' else stream['codec_name']
            output_file = input_file_path.with_suffix(f'.{ext}')

            ffmpeg_command = (
                f'ffmpeg -hide_banner -y -i "{input_file_path}" '
                f'-map 0:{stream["index"]} "{output_file}"'
            )
            await stream_shell_output(event, ffmpeg_command, status_message, progress_message)

            if output_file.exists() and output_file.stat().st_size:
                caption = f'Subtitle {i + 1}: {stream.get("tags", {}).get("language", "Unknown")}'
                await event.client.send_file(event.chat_id, output_file, caption=caption)
            else:
                await status_message.edit(t('failed_to_extract_subtitle_stream', stream=i + 1))

            output_file.unlink(missing_ok=True)

    await status_message.edit(t('subtitle_extraction_completed'))


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
        reply_message = await get_reply_message(event, previous=True)
        formats = (
            ALLOWED_AUDIO_FORMATS
            if (reply_message.audio or reply_message.voice)
            else ALLOWED_VIDEO_FORMATS
        )
        target_format = await inline_choice_grid(
            event,
            prefix='m|media_convert|',
            prompt_text=f'{t("choose_target_format")}:',
            pairs=[(str(ext), f'm|media_convert|{ext}') for ext in formats],
            cols=3,
            cast=str,
        )
        if target_format is None:
            return
        delete_message_after_process = True
    else:
        target_format = event.message.text.split('convert ')[1].lower()
        if target_format[0] == '.':
            target_format = target_format[1:]
        if target_format not in ALLOWED_VIDEO_FORMATS | ALLOWED_AUDIO_FORMATS:
            await event.reply(
                f'{t("unsupported_media_type")}.\n'
                f'{t("allowed_formats")}: {", ".join(ALLOWED_VIDEO_FORMATS | ALLOWED_AUDIO_FORMATS)}'
            )
            return
    reply_message = await get_reply_message(event, previous=True)
    if reply_message.file.ext == target_format:
        await event.reply(t('file_already_in_target_format', target_format=target_format))
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
        feedback_text=t('media_converted_to_target_format', target_format=target_format),
    )
    if delete_message_after_process:
        delete_callback_after(event)


ALLOWED_VIDEO_QUALITIES = {144, 240, 360, 480, 720}


async def resize_video(event: NewMessage.Event | CallbackQuery.Event) -> None:
    delete_message_after_process = False
    if isinstance(event, CallbackQuery.Event):
        quality = await inline_choice_grid(
            event,
            prefix='m|video_resize|',
            prompt_text=f'{t("choose_target_quality")}:',
            pairs=[
                (str(quality), f'm|video_resize|{quality}')
                for quality in sorted(ALLOWED_VIDEO_QUALITIES)
            ],
            cols=len(ALLOWED_VIDEO_QUALITIES),
            cast=int,
        )
        if quality is None:
            return
        delete_message_after_process = True
    else:
        quality = event.message.text.split('resize ')[1]

    quality = int(quality)

    if quality not in ALLOWED_VIDEO_QUALITIES:
        await event.reply(
            f'{t("invalid_target_quality")}. {t("please_choose_from")} {", ".join(map(str, ALLOWED_VIDEO_QUALITIES))}.'
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
        delete_callback_after(event)


async def video_update_initial(event: NewMessage.Event | CallbackQuery.Event) -> None:
    reply_message = await get_reply_message(event, previous=True)
    await event.client.file_collectors.start(
        event,
        t('send_media_to_use'),
        first_message_id=reply_message.id,
        accept=lambda e: bool(e.message.audio or e.message.voice or e.message.video),
        on_complete=_video_update_process,
        min_files=2,
        max_files=2,
        allow_non_reply=True,
        reply_to=reply_message.id,
    )
    raise StopPropagation


async def _video_update_process(event: NewMessage.Event, file_ids: list[int]) -> None:
    video_message = await event.client.get_messages(event.chat_id, ids=file_ids[0])
    audio_message = event.message
    status_message = await send_progress_message(event, t('starting_audio_update'))
    progress_message = await event.respond(f'<pre>{t("process_output")}:</pre>')

    output_dir = Path(TMP_DIR / str(uuid4()))
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        video_name = video_message.file.name or get_download_name(video_message).name
        audio_name = audio_message.file.name or get_download_name(audio_message).name

        with (output_dir / video_name).open('wb') as f:
            await download_file(event, f, video_message, progress_message)
        with (output_dir / audio_name).open('wb') as f:
            await download_file(event, f, audio_message, progress_message)

        video_file = output_dir / video_name
        audio_file = output_dir / audio_name
        output_file = output_dir / f'{Path(video_name).stem}_updated{Path(video_name).suffix}'

        ffmpeg_command = (
            f'ffmpeg -hide_banner -y -i "{video_file}" -i "{audio_file}" '
            f'-map "0:v" -map "1:a" -c:v copy -c:a copy "{output_file}"'
        )
        await stream_shell_output(event, ffmpeg_command, status_message, progress_message)
        if not output_file.exists() or not output_file.stat().st_size:
            await status_message.edit(t('audio_update_failed'))
            return

        upload_params = await build_media_upload_params(output_file, is_voice=False)
        await upload_file_and_cleanup(
            event,
            output_file,
            progress_message,
            **upload_params,
        )

    finally:
        rmtree(output_dir, ignore_errors=True)

    await status_message.edit(t('video_audio_updated'))
    raise StopPropagation


async def amplify_sound(event: NewMessage.Event | CallbackQuery.Event) -> None:
    delete_message_after_process = False
    if isinstance(event, CallbackQuery.Event):
        amplification_factor = await inline_choice_grid(
            event,
            prefix='m|media_amplify|',
            prompt_text=f'{t("choose_amplification_factor")}:',
            pairs=[
                (f'{factor}x', f'm|media_amplify|{factor}') for factor in ALLOWED_AMPLIFY_FACTORS
            ],
            cols=4,
            cast=float,
        )
        if amplification_factor is None:
            return
        delete_message_after_process = True
    else:
        amplification_factor = float(event.message.text.split('amplify ')[1])

    if amplification_factor <= 1:
        await event.reply(t('amplification_factor_must_be_greater_than_1'))
        return
    amplification_factor = min(amplification_factor, 3)

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
        feedback_text=t(
            'audio_amplified_by_amplification_factor', amplification_factor=amplification_factor
        ),
    )
    if delete_message_after_process:
        delete_callback_after(event)


async def speed_media(event: NewMessage.Event | CallbackQuery.Event) -> None:
    delete_message_after_process = False
    if isinstance(event, CallbackQuery.Event):
        speed_factor = await inline_choice_grid(
            event,
            prefix='m|media_speed|',
            prompt_text=f'{t("choose_speed_factor")}:',
            pairs=[(f'{factor}x', f'm|media_speed|{factor}') for factor in ALLOWED_SPEED_FACTORS],
            cols=4,
            cast=float,
        )
        if speed_factor is None:
            return
        delete_message_after_process = True
    else:
        match = Media.commands['media speed'].pattern.match(event.message.text)
        speed_factor = float(match.group(3)) if match else 1.0

    if speed_factor <= 1 or speed_factor > 3:
        await event.reply(t('speed_factor_must_be_between_1_and_3'))
        return

    reply_message = await get_reply_message(event, previous=True)
    atempo_filters = []
    remaining = float(speed_factor)
    while remaining > 2:
        atempo_filters.append('atempo=2')
        remaining /= 2
    atempo_filters.append(f'atempo={remaining:.5f}'.rstrip('0').rstrip('.'))
    atempo = ','.join(atempo_filters)

    if bool(reply_message.video or reply_message.video_note):
        ffmpeg_command = (
            'ffmpeg -hide_banner -y -i "{input}" '
            f'-filter_complex "[0:v]setpts=PTS/{speed_factor}[v];[0:a]{atempo}[a]" '
            '-map "[v]" -map "[a]" '
            '-c:v libx264 -preset ultrafast -c:a aac -b:a 128k -movflags +faststart '
            '"{output}"'
        )
        output_suffix = '.mp4'
        is_voice = False
    else:
        is_voice = bool(reply_message.voice)
        ffmpeg_command = (
            'ffmpeg -hide_banner -y -i "{input}" '
            f'-filter:a "{atempo}" '
            + ('-vn -c:a libopus -b:a 48k ' if is_voice else '-vn -c:a libmp3lame -q:a 2 ')
            + '"{output}"'
        )
        output_suffix = '.ogg' if is_voice else '.mp3'

    await process_media(
        event,
        ffmpeg_command,
        output_suffix,
        reply_message=reply_message,
        is_voice=is_voice,
        feedback_text=t('media_sped_up', factor=speed_factor),
    )
    if delete_message_after_process:
        delete_callback_after(event)


async def video_thumbnails(event: NewMessage.Event | CallbackQuery.Event) -> None:
    reply_message = await get_reply_message(event, previous=True)
    status_message = await send_progress_message(event, t('starting_thumbnail_generation'))
    progress_message = await send_progress_message(event, f'<pre>{t("process_output")}:</pre>')

    async with download_to_temp_file(
        event,
        reply_message,
        progress_message,
        suffix=reply_message.file.ext,
    ) as input_file_path:
        duration_output, _ = await run_command(
            f'ffprobe -v error -show_entries format=duration -of '
            f'default=noprint_wrappers=1:nokey=1 "{input_file_path}"'
        )
        duration = float(duration_output.strip())

        # Calculate timestamps for each thumbnail
        interval = duration / 16
        timestamps = [i * interval for i in range(16)]
        # Generate thumbnail grid
        output_file = input_file_path.with_suffix('.jpg')
        select_frames = '+'.join([f'eq(n,{int(i * 25)})' for i in timestamps])  # Assuming 25 fps
        ffmpeg_command = (
            f'ffmpeg -hide_banner -y -i "{input_file_path}" '
            f'-vf "select=\'{select_frames}\',scale=480:-1,tile=4x4" '
            f'-frames:v 1 "{output_file}"'
        )

        await stream_shell_output(event, ffmpeg_command, status_message, progress_message)
        if not output_file.exists() or not output_file.stat().st_size:
            await status_message.edit(t('thumbnail_generation_failed'))
            return
        await upload_file_and_cleanup(event, output_file, progress_message, unlink=False)
        await upload_file_and_cleanup(
            event,
            output_file,
            progress_message,
            force_document=True,
        )

    await status_message.edit(t('video_thumbnails_generated'))


async def compress_video(event: NewMessage.Event | CallbackQuery.Event) -> None:
    delete_message_after_process = False
    if isinstance(event, CallbackQuery.Event):
        target_percentage = await inline_choice_grid(
            event,
            prefix='m|video_compress|',
            prompt_text=f'{t("choose_target_compression_percentage")}:',
            pairs=[
                (f'{percentage}%', f'm|video_compress|{percentage}')
                for percentage in ALLOWED_VIDEO_COMPRESS_PERCENTAGES
            ],
            cols=4,
            cast=int,
        )
        if target_percentage is None:
            return
        delete_message_after_process = True
    else:
        target_percentage = int(event.message.text.split('compress ')[1])

    if target_percentage < 20 or target_percentage > 90:
        await event.reply(t('compression_percentage_must_be_between_20_and_90'))
        return

    reply_message = await get_reply_message(event, previous=True)
    # Calculate target bitrate
    calculated_percentage = 100 - target_percentage
    target_size = (calculated_percentage / 100) * reply_message.file.size
    target_bitrate = floor(target_size * 8 / reply_message.file.duration)
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
        event, ffmpeg_command, reply_message.file.ext, feedback_text=t('video_compressed')
    )
    compression_ratio = (1 - (data['output_size'] / reply_message.file.size)) * 100
    feedback_text = (
        f'\n{t("target_compression")}: {target_percentage}%\n'
        f'{t("actual_compression")}: {compression_ratio:.2f}%\n'
    )
    status_message = data['status_message']
    assert isinstance(status_message, Message)
    await status_message.edit(data['status_text'] + feedback_text)
    if delete_message_after_process:
        delete_callback_after(event)


async def video_encode_x265(event: NewMessage.Event | CallbackQuery.Event) -> None:
    delete_message_after_process = False
    if isinstance(event, CallbackQuery.Event):
        crf = await inline_choice_grid(
            event,
            prefix='m|video_x265|',
            prompt_text=f'{t("choose_crf")}:',
            pairs=[(str(crf), f'm|video_x265|{crf}') for crf in ALLOWED_VIDEO_X265_CRF],
            cols=len(ALLOWED_VIDEO_X265_CRF),
            cast=int,
        )
        if crf is None:
            return
        delete_message_after_process = True
    else:
        crf = int(event.message.text.split('x265 ')[1])

    if crf < 20 or crf > 28:
        await event.reply(t('crf_value_must_be_between_20_and_28'))
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
        feedback_text=t('video_x265_encoded'),
    )

    compression_ratio = (1 - (data['output_size'] / reply_message.file.size)) * 100
    feedback_text = f'\n{t("compression_ratio")}: {compression_ratio:.2f}%\n'
    status_message = data['status_message']
    assert isinstance(status_message, Message)
    await status_message.edit(data['status_text'] + feedback_text)
    if delete_message_after_process:
        delete_callback_after(event)


async def video_create_initial(event: NewMessage.Event | CallbackQuery.Event) -> None:
    reply_message = await get_reply_message(event, previous=True)
    await event.client.file_collectors.start(
        event,
        t('send_subtitle_or_photo'),
        first_message_id=reply_message.id,
        accept=lambda e: bool(
            (e.message.file and e.message.file.ext and e.message.file.ext.lower() == '.srt')
            or e.message.photo
        ),
        on_complete=_video_create_process,
        min_files=2,
        max_files=2,
        allow_non_reply=True,
        reply_to=reply_message.id,
    )
    raise StopPropagation


async def _video_create_process(event: NewMessage.Event, file_ids: list[int]) -> None:
    audio_message: Message = await event.client.get_messages(event.chat_id, ids=file_ids[0])
    input_message: Message = event.message
    status_message: Message = await event.reply(t('starting_video_creation'))
    progress_message: Message = await event.respond(f'<pre>{t("process_output")}:</pre>')

    output_dir = Path(TMP_DIR / str(uuid4()))
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        audio_name = audio_message.file.name or get_download_name(audio_message).name
        with (output_dir / audio_name).open('wb') as f:
            await download_file(event, f, audio_message, progress_message)
        audio_file = output_dir / audio_name
        input_name = input_message.file.name or get_download_name(input_message).name
        with (output_dir / input_name).open('wb') as f:
            await download_file(event, f, input_message, progress_message)
        input_file = output_dir / input_name
        output_file = output_dir / f'{audio_file.stem}.mp4'

        if (
            input_message.file
            and input_message.file.ext
            and input_message.file.ext.lower() == '.srt'
        ):
            ffmpeg_command = (
                f'ffmpeg -hide_banner -y -f lavfi -i color=c=black:s=854x480:d={audio_message.file.duration} '
                f'-i "{audio_file}" -i "{input_file}" '
                f"-filter_complex \"[0:v]subtitles=f='{input_file}':force_style='FontSize=28,Alignment=10,MarginV=190'[v]\" "
                f'-map "[v]" -map 1:a -map 2 '
                f'-c:v libx264 -preset ultrafast -c:a aac -b:a 48k '
                f'-c:s mov_text '
                f'-shortest "{output_file}"'
            )
        elif input_message.photo:
            ffmpeg_command = (
                f'ffmpeg -hide_banner -y -loop 1 -i "{input_file}" '
                f'-i "{audio_file}" '
                f'-vf "scale=trunc(iw/2)*2:trunc(ih/2)*2" '
                f'-c:v libx264 -preset ultrafast -tune stillimage '
                f'-c:a aac -b:a 48k -shortest '
                f'-pix_fmt yuv420p -movflags +faststart "{output_file}"'
            )
        else:
            await status_message.edit(t('unsupported_input_file_format'))
            raise StopPropagation

        await stream_shell_output(event, ffmpeg_command, status_message, progress_message)
        if not output_file.exists() or not output_file.stat().st_size:
            await status_message.edit(t('video_creation_failed'))
        else:
            upload_params = await build_media_upload_params(output_file, is_voice=False)
            await upload_file_and_cleanup(
                event,
                output_file,
                progress_message,
                **upload_params,
            )
            await status_message.edit(t('video_created'))

    finally:
        rmtree(output_dir, ignore_errors=True)
    raise StopPropagation


async def transcribe_media(event: NewMessage.Event | CallbackQuery.Event) -> None:  # noqa: C901, PLR0912, PLR0915
    delete_message_after_process = False
    if isinstance(event, CallbackQuery.Event):
        transcription_method = await inline_choice_grid(
            event,
            prefix='m|transcribe|',
            prompt_text=f'{t("choose_transcription_method")}:',
            pairs=[
                (method.capitalize(), f'm|transcribe|{method}')
                for method in ALLOWED_TRANSCRIBE_METHODS
            ],
            cols=len(ALLOWED_TRANSCRIBE_METHODS),
            cast=str,
        )
        if transcription_method is None:
            return
        delete_message_after_process = True
        language = 'ar'
    else:
        match = Media.commands['transcribe'].pattern.match(event.message.text)
        transcription_method = (match.group(2) if match else 'wit') or 'wit'
        language = (match.group(3) if match else 'ar') or 'ar'
    wit_access_tokens, whisper_api_key = '', None
    if transcription_method == 'whisper':
        whisper_api_key = getenv('GROQ_API_KEY')
        if not whisper_api_key:
            await event.reply(t('please_set_whisper_api_key'))
            return
    elif transcription_method == 'wit':
        wit_access_tokens = getenv('WIT_CLIENT_ACCESS_TOKENS')
        if not wit_access_tokens:
            await event.reply(t('please_set_wit_client_access_tokens'))
            return

    reply_message = await get_reply_message(event, previous=True)
    status_message = await send_progress_message(event, t('starting_transcription'))
    progress_message = await send_progress_message(event, f'<pre>{t("process_output")}:</pre>')
    output_dir = Path(TMP_DIR / str(uuid4()))
    output_dir.mkdir(parents=True, exist_ok=True)

    async with download_to_temp_file(
        event,
        reply_message,
        progress_message,
        suffix=reply_message.file.ext,
        temp_dir=output_dir,
    ) as input_file_path:
        if transcription_method == 'google':
            try:
                output_file = await transcribe_with_google(input_file_path, output_dir, language)
            except RuntimeError as e:
                await status_message.edit(t('an_error_occurred', error=f'\n<pre>{e}</pre>'))
                return
            if not output_file:
                await status_message.edit(f'{t("failed_to_transcribe")} {input_file_path.name}')
                return
        elif transcription_method == 'vosk':
            command = (
                f'vosk-transcriber --log-level warning -i {input_file_path} -l ar '
                f'-t srt -o {output_dir.name / input_file_path.with_suffix(".srt")}'
            )
            await stream_shell_output(
                event, command, status_message, progress_message, max_length=100
            )
        elif transcription_method == 'whisper' and whisper_api_key:
            model = get_model(getenv('LLM_TRANSCRIPTION_MODEL'))
            audio_file_path = input_file_path
            if audio_file_path.suffix not in (
                f'.{mime.split("/")[1]}' for mime in model.attachment_types
            ):
                ffmpeg_command = (
                    f'ffmpeg -hide_banner -y -i "{audio_file_path}" '
                    f'-vn -c:a libopus -b:a 32k "{audio_file_path.with_suffix(".ogg")}"'
                )
                output, status_code = await run_command(ffmpeg_command)
                if status_code != 0:
                    audio_file_path.unlink(missing_ok=True)
                    await status_message.edit(
                        t('an_error_occurred', error=f'\n<pre>{output}</pre>')
                    )
                    return
                audio_file_path = audio_file_path.with_suffix('.ogg')
            response = model.prompt(
                attachments=[Attachment(path=str(audio_file_path))],
                language=language,
            )
            response.on_done(lambda _: audio_file_path.unlink(missing_ok=True))
            transcription = response.text()
            await edit_or_send_as_file(
                event,
                status_message,
                transcription,
                file_name=audio_file_path.with_suffix('.txt').name,
            )
        elif transcription_method == 'wit':
            command = f'{quote(executable)} -m src.utils.tafrigh_compat {quote(str(input_file_path))} -o {quote(output_dir.name)} -f txt srt'
            command += f' -w {quote(wit_access_tokens)}'
            await stream_shell_output(
                event, command, status_message, progress_message, max_length=100
            )
        if transcription_method == 'vosk':
            srt_to_txt(input_file_path.with_suffix('.srt'))
        for output_file in output_dir.glob('*.[st][xr]t'):
            if output_file.exists() and output_file.stat().st_size:
                if reply_message.file.name:
                    renamed_file = output_file.rename(
                        output_file.with_stem(Path(reply_message.file.name).stem)
                    )
                else:
                    renamed_file = output_file
                await upload_file_and_cleanup(
                    event,
                    renamed_file,
                    progress_message,
                    caption=f'<code>{renamed_file.name}</code>',
                )
            else:
                await status_message.edit(f'{t("failed_to_transcribe")} {output_file.name}')
    if transcription_method != 'whisper':
        await status_message.edit(t('transcription_completed'))
    rmtree(output_dir)
    delete_message_after(progress_message)
    if delete_message_after_process:
        delete_callback_after(event)


async def fix_stereo_audio(event: NewMessage.Event | CallbackQuery.Event) -> None:
    delete_message_after_process = False
    if isinstance(event, CallbackQuery.Event):
        channel = await inline_choice_grid(
            event,
            prefix='m|media_stereo|',
            prompt_text=f'{t("use_audio_of_which_channel")}:',
            pairs=[(t(channel), f'm|media_stereo|{channel}') for channel in ('right', 'left')],
            cols=2,
            cast=str,
        )
        if channel is None:
            return
        delete_message_after_process = True
    else:
        channel = event.message.text.split('stereo ')[1]
    reply_message = await get_reply_message(event, previous=True)
    channel = 'FR' if channel == 'right' else 'FL'
    ffmpeg_command = (
        f'ffmpeg -hide_banner -y -i "{{input}}" '
        f'-af "pan=mono|c0={channel}" '
        f'-c:a aac -b:a {{audio_bitrate}} '
        f'"{{output}}"'
    )
    await process_media(
        event, ffmpeg_command, reply_message.file.ext, reply_message=reply_message, get_bitrate=True
    )
    if delete_message_after_process:
        delete_callback_after(event)
    if delete_message_after_process:
        delete_message_after(await event.get_message(), seconds=60 * 5)


class Media(ModuleBase):
    name = 'Media'
    description = t('_media_module_description')
    commands: ClassVar[ModuleBase.CommandsT] = {
        'audio compress': Command(
            handler=compress_audio,
            description=t('_audio_compress_description'),
            pattern=re.compile(r'^/(audio)\s+(compress)\s+(\d+)$'),
            condition=partial(has_media, audio=True),
            is_applicable_for_reply=True,
        ),
        'audio convert': Command(
            handler=convert_to_audio,
            description=t('_audio_convert_description'),
            pattern=re.compile(r'^/(audio)\s+(convert)$'),
            condition=partial(has_media, not_audio=True),
            is_applicable_for_reply=True,
        ),
        'audio metadata': Command(
            handler=set_metadata,
            description=t('_audio_metadata_description'),
            pattern=re.compile(r'^/(audio)\s+(metadata)\s+.+\s+-\s+.+$'),
            condition=partial(has_media, audio=True),
            is_applicable_for_reply=True,
        ),
        'audio trim': Command(
            handler=trim_silence,
            description=t('_audio_trim_description'),
            pattern=re.compile(r'^/(audio)\s+(trim)$'),
            condition=partial(has_media, audio_or_voice=True),
            is_applicable_for_reply=True,
        ),
        'media amplify': Command(
            handler=amplify_sound,
            description=t('_media_amplify_description'),
            pattern=re.compile(r'^/(media)\s+(amplify)\s+(\d+(\.\d+)?)$'),
            condition=partial(has_media, any=True),
            is_applicable_for_reply=True,
        ),
        'media speed': Command(
            handler=speed_media,
            description=t('_media_speed_description'),
            pattern=re.compile(r'^/(media)\s+(speed)\s+(\d+(\.\d+)?)$'),
            condition=partial(has_media, any=True),
            is_applicable_for_reply=True,
        ),
        'media convert': Command(
            handler=convert_media,
            description=t('_media_convert_description'),
            pattern=re.compile(r'^/(media)\s+(convert)\s+(\w+)$'),
            condition=partial(has_media, any=True),
            is_applicable_for_reply=True,
        ),
        'media cut': Command(
            handler=cut_media,
            description=t('_media_cut_description'),
            pattern=re.compile(
                r'^/(media)\s+(cut)\s+(\d{2}:\d{2}:\d{2}\s+\d{2}:\d{2}:\d{2}'
                r'(\s+\d{2}:\d{2}:\d{2}\s+\d{2}:\d{2}:\d{2})*)$'
            ),
            condition=partial(has_media, any=True),
            is_applicable_for_reply=True,
        ),
        'media crop': Command(
            handler=crop_out_media,
            description=t('_media_crop_description'),
            pattern=re.compile(
                r'^/(media)\s+(crop)\s+out\s+(\d{2}:\d{2}:\d{2}\s+\d{2}:\d{2}:\d{2}'
                r'(\s+\d{2}:\d{2}:\d{2}\s+\d{2}:\d{2}:\d{2})*)$'
            ),
            condition=partial(has_media, any=True),
            is_applicable_for_reply=True,
        ),
        'media split': Command(
            handler=split_media,
            description=t('_media_split_description'),
            pattern=re.compile(r'^/(media)\s+(split)\s+(\d+[hms])$'),
            condition=partial(has_media, any=True),
            is_applicable_for_reply=True,
        ),
        'media merge': Command(
            handler=merge_media_initial,
            description=t('_media_merge_description'),
            pattern=re.compile(r'^/(media)\s+(merge)$'),
            condition=partial(has_media, any=True),
            is_applicable_for_reply=True,
        ),
        'media info': Command(
            handler=media_info,
            description=t('_media_info_description'),
            pattern=re.compile(r'^/(media)\s+(info)$'),
            condition=partial(has_media, any=True),
            is_applicable_for_reply=True,
        ),
        'media stereo': Command(
            handler=fix_stereo_audio,
            description=t('_media_stereo_description'),
            pattern=re.compile(r'^/(media)\s+(stereo)\s+(right|left)$'),
            condition=partial(has_media, audio_or_voice=True),
            is_applicable_for_reply=True,
        ),
        'transcribe': Command(
            handler=transcribe_media,
            description=t('_transcribe_description'),
            pattern=re.compile(
                r'^/(transcribe)(?:\s+(wit|whisper|vosk|google))?(?:\s+(\w{2,3}))?$'
            ),
            condition=partial(has_media, any=True),
            is_applicable_for_reply=True,
        ),
        'video create': Command(
            handler=video_create_initial,
            description=t('_video_create_description'),
            pattern=re.compile(r'^/(video)\s+(create)$'),
            condition=partial(has_media, audio_or_voice=True),
            is_applicable_for_reply=True,
        ),
        'video compress': Command(
            handler=compress_video,
            description=t('_video_compress_description'),
            pattern=re.compile(r'^/(video)\s+(compress)\s+(\d{1,2})$'),
            condition=partial(has_media, video=True),
            is_applicable_for_reply=True,
        ),
        'video mute': Command(
            handler=mute_video,
            description=t('_video_mute_description'),
            pattern=re.compile(r'^/(video)\s+(mute)$'),
            condition=partial(has_media, video_or_video_note=True),
            is_applicable_for_reply=True,
        ),
        'video resize': Command(
            handler=resize_video,
            description=t('_video_resize_description'),
            pattern=re.compile(
                rf'^/(video)\s+(resize)\s+({"|".join(map(str, ALLOWED_VIDEO_QUALITIES))})$'
            ),
            condition=partial(has_media, video=True),
            is_applicable_for_reply=True,
        ),
        'video subtitle': Command(
            handler=extract_subtitle,
            description=t('_video_subtitle_description'),
            pattern=re.compile(r'^/(video)\s+(subtitle)$'),
            condition=partial(has_media, video=True),
            is_applicable_for_reply=True,
        ),
        'video thumbnails': Command(
            handler=video_thumbnails,
            description=t('_video_thumbnails_description'),
            pattern=re.compile(r'^/(video)\s+(thumbnails)$'),
            condition=partial(has_media, video=True),
            is_applicable_for_reply=True,
        ),
        'video update': Command(
            handler=video_update_initial,
            description=t('_video_update_description'),
            pattern=re.compile(r'^/(video)\s+(update)$'),
            condition=partial(has_media, video=True),
            is_applicable_for_reply=True,
        ),
        'video x265': Command(
            handler=video_encode_x265,
            description=t('_video_x265_description'),
            pattern=re.compile(r'^/(video)\s+(x265)\s+(\d{2})$'),
            condition=partial(has_media, video=True),
            is_applicable_for_reply=True,
        ),
        'voice': Command(
            handler=convert_to_voice_note,
            description=t('_voice_description'),
            pattern=re.compile(r'^/(voice)$'),
            condition=partial(has_media, not_voice=True),
            is_applicable_for_reply=True,
        ),
    }

    @staticmethod
    def register_handlers(bot: TelegramClient) -> None:
        return
