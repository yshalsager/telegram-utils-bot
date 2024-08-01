from asyncio import get_running_loop, run_coroutine_threadsafe
from functools import partial
from pathlib import Path
from typing import Any, ClassVar

import orjson
import regex as re
from humanize import naturalsize
from telethon import Button
from telethon.events import CallbackQuery, NewMessage
from telethon.tl.custom import Message
from telethon.tl.types import DocumentAttributeAudio, DocumentAttributeVideo
from yt_dlp import YoutubeDL

from src import PARENT_DIR, TMP_DIR
from src.modules.base import ModuleBase
from src.utils.command import Command
from src.utils.downloads import upload_file
from src.utils.filters import has_valid_url
from src.utils.json import json_options, process_dict
from src.utils.patterns import HTTP_URL_PATTERN, YOUTUBE_URL_PATTERN
from src.utils.progress import progress_callback
from src.utils.subtitles import convert_subtitles
from src.utils.telegram import edit_or_send_as_file, get_reply_message

cookies_file = Path(PARENT_DIR) / 'cookies.txt'
netrc_file = Path(PARENT_DIR) / '.netrc'
cookies = {'cookiefile': str(cookies_file.absolute())} if cookies_file.exists() else {}
params = {
    **cookies,
    'quiet': True,
    'no_color': True,
    'nocheckcertificate': True,
    'external_downloader': 'aria2c',
    'external_downloader_args': [
        '--min-split-size=1M',
        '--max-connection-per-server=16',
        '--max-concurrent-downloads=16',
        '--split=16',
    ],
    'format_sort': ['res:480', '+size', 'ext'],
    'restrictfilenames': True,
    'windowsfilenames': True,
}
if netrc_file.exists():
    params['usenetrc'] = True
    params['netrc_location'] = str(netrc_file.absolute())


def download_hook(d: dict[str, Any], message: Message) -> None:
    if d['status'] == 'downloading':
        total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
        current = d.get('downloaded_bytes', 0)
        run_coroutine_threadsafe(
            progress_callback(current, total, message, 'Downloading'),
            get_running_loop(),
        )


def calculate_common_formats_and_sizes(
    info_dict: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, int], int, int]:
    entries = info_dict.get('entries', [info_dict])
    common_formats: dict[str, Any] = {}
    total_sizes: dict[str, int] = {}
    worst_audio_size = 0

    for entry in entries:
        formats = entry.get('formats', [])
        entry_formats = {
            f['format_id']: {
                'format_id': f['format_id'],
                'acodec': f.get('acodec'),
                'vcodec': f.get('vcodec'),
                'format': f.get('format'),
                'ext': f.get('ext'),
            }
            for f in formats
        }

        if not common_formats:
            common_formats = entry_formats
        else:
            common_formats = {k: v for k, v in common_formats.items() if k in entry_formats}

        for f in formats:
            format_id = f['format_id']
            size = f.get('filesize_approx', 0) or 0
            total_sizes[format_id] = total_sizes.get(format_id, 0) + size

            if f.get('acodec') != 'none' and f.get('vcodec') == 'none':
                worst_audio_size = max(worst_audio_size, size)

    return common_formats, total_sizes, worst_audio_size, len(entries)


async def get_info(event: NewMessage.Event | CallbackQuery.Event) -> None:
    progress_message = await event.reply('Fetching information...')
    message = (
        await get_reply_message(event, previous=True)
        if isinstance(event, CallbackQuery.Event)
        else event.message
    )
    link = re.search(HTTP_URL_PATTERN, message.raw_text).group(0)
    ydl_opts = {
        **params,
        'progress_hooks': [lambda d: download_hook(d, progress_message)],
    }
    try:
        info_dict = await get_running_loop().run_in_executor(
            None, partial(YoutubeDL(ydl_opts).extract_info, link, download=False)
        )
        processed_info = process_dict(info_dict)
        json_str = orjson.dumps(processed_info, option=json_options).decode()
        edited = await edit_or_send_as_file(
            event,
            progress_message,
            text=f'<pre>{json_str}</pre>',
            file_name=f"{info_dict['id']}.json",
            caption=f"<b>{info_dict['title']}</b>\n\n"
            f"👤 <a href='{info_dict.get('uploader_url', '')}'>{info_dict.get('uploader', '')}</a>\n"
            f"📽️ {len(info_dict.get('entries', [1]))} items\n"
            f"{info_dict['webpage_url']}",
        )
        if not edited:
            await progress_message.delete()
    except Exception as e:  # noqa: BLE001
        await progress_message.edit(f'An error occurred:\n<pre>{e!s}</pre>')


async def get_subtitles(event: NewMessage.Event) -> None:
    progress_message = await event.reply('Downloading subtitles...')
    message = (
        await get_reply_message(event, previous=True)
        if isinstance(event, CallbackQuery.Event)
        else event.message
    )
    link = re.search(HTTP_URL_PATTERN, message.raw_text).group(0)
    if match := re.search(r'\s+([a-z]{2})\s+', message.raw_text):
        language = match.group(1)
    else:
        language = 'ar'
    ydl_opts = {
        **params,
        'skip_download': True,
        'writeautomaticsub': True,
        'writesubtitles': True,
        'subtitleslangs': [language, f'{language}-orig'],
        'outtmpl': str(TMP_DIR / '%(title)s.%(ext)s'),
        'progress_hooks': [lambda d: download_hook(d, progress_message)],
    }
    try:
        info_dict = await get_running_loop().run_in_executor(
            None, partial(YoutubeDL(ydl_opts).extract_info, link, download=True)
        )
    except Exception as e:  # noqa: BLE001
        await progress_message.edit(f'An error occurred:\n<pre>{e!s}</pre>')
        return

    entries = info_dict.get('entries', [info_dict])
    for entry in entries:
        subs = entry.get('requested_subtitles', {})
        if not subs:
            await progress_message.edit(f'No subtitles found for {entry.get("id")}.')
            continue
        for lang, sub_info in subs.items():
            vtt_path = Path(sub_info.get('filepath', ''))
            if not vtt_path.exists():
                continue
            srt_path = vtt_path.with_suffix('.srt')
            txt_path = vtt_path.with_suffix('.txt')
            await convert_subtitles(vtt_path, srt_path, txt_path)
            for file in [srt_path, txt_path]:
                file_path = file.rename(
                    file.with_stem(f"{re.sub('[/:*"\'<>|]', '_', entry['title'])}-{lang}")
                )
                await upload_file(
                    event,
                    file_path,
                    progress_message,
                    caption=entry.get('webpage_url', ''),
                )
                file_path.unlink(missing_ok=True)
            vtt_path.unlink(missing_ok=True)
    await progress_message.delete()


async def get_formats(event: NewMessage.Event | CallbackQuery.Event) -> None:
    progress_message = await event.reply('Fetching available formats...')
    message = (
        await get_reply_message(event, previous=True)
        if isinstance(event, CallbackQuery.Event)
        else event.message
    )
    link = re.search(HTTP_URL_PATTERN, message.raw_text).group(0)
    try:
        ydl_opts = {
            **params,
            'listformats': True,
        }
        info_dict = await get_running_loop().run_in_executor(
            None, partial(YoutubeDL(ydl_opts).extract_info, link, download=False)
        )
        common_formats, total_sizes, worst_audio_size, entry_count = (
            calculate_common_formats_and_sizes(info_dict)
        )
        if not common_formats:
            await progress_message.edit('No formats found.')
            return

        format_list = []
        for format_id, f in common_formats.items():
            if format_id not in total_sizes or total_sizes[format_id] == 0:
                continue
            total_size = total_sizes[format_id]
            if f['vcodec'] != 'none':
                total_size += worst_audio_size * entry_count
            format_name = (
                f"🖥 {f['format']} | 📁 {f['ext']} | 💾 {naturalsize(total_size, binary=True)}"
            )
            format_list.append(format_name)

        await edit_or_send_as_file(
            event,
            progress_message,
            text=f'<b>Available formats ({entry_count} items):</b>\n\n{'\n'.join(format_list)}',
            file_name=f"{info_dict['id']}_formats.txt",
            caption=info_dict['webpage_url'],
        )
    except Exception as e:  # noqa: BLE001
        await progress_message.edit(f'An error occurred:\n<pre>{e!s}</pre>')


async def download_media(event: NewMessage.Event | CallbackQuery.Event) -> None:  # noqa: C901
    is_url_event = (
        isinstance(event, CallbackQuery.Event)
        and event.data
        and event.data.decode().endswith('ytdown')
    )
    is_command_event = isinstance(event, NewMessage.Event)
    if is_command_event or is_url_event:
        text = 'Choose format type:'
        buttons = [
            [Button.inline('Audio', 'ytdown|audio|'), Button.inline('Video', 'ytdown|video|')]
        ]
        progress_message = (
            await event.reply(text, buttons=buttons)
            if is_command_event
            else await event.edit(text, buttons=buttons)
        )
        return

    reply_message = await get_reply_message(event, previous=True)
    link = re.search(HTTP_URL_PATTERN, reply_message.raw_text).group(0)
    _, _type, _format = event.data.decode().split('|')

    if _type in ('audio', 'video') and not _format:
        progress_message = await event.edit('Fetching available formats...')
        ydl_opts = {**params, 'listformats': True}
        info_dict = await get_running_loop().run_in_executor(
            None, partial(YoutubeDL(ydl_opts).extract_info, link, download=False)
        )
        common_formats, total_sizes, worst_audio_size, entry_count = (
            calculate_common_formats_and_sizes(info_dict)
        )
        if not common_formats:
            await progress_message.edit('No formats found.')
            return

        buttons = []
        for format_id, f in common_formats.items():
            if format_id not in total_sizes or total_sizes[format_id] == 0:
                continue
            total_size = total_sizes[format_id]
            if f['vcodec'] != 'none':
                total_size += worst_audio_size * entry_count
            if (_type == 'audio' and f['acodec'] != 'none' and f['vcodec'] == 'none') or (
                _type == 'video' and f['vcodec'] != 'none'
            ):
                format_name = f"{f['format']} | {f['ext']} | {naturalsize(total_size, binary=True)}"
                buttons.append([Button.inline(format_name, f'ytdown|{_type}|{format_id}')])
        if not buttons:
            await progress_message.edit('No suitable formats found.')
            return
        await event.edit(f'Choose {_type} format for ({entry_count}) items:', buttons=buttons)
        return

    # User selected a specific format
    progress_message = await event.edit('Starting download...')
    format_id = _format if _type == 'audio' else f'{_format}+worstaudio/best'
    ydl_opts = {
        **params,
        'format': format_id,
        'outtmpl': str(TMP_DIR / '%(id)s.%(ext)s'),
        'progress_hooks': [lambda d: download_hook(d, progress_message)],
        'writethumbnail': True,
        'postprocessors': [
            {
                'key': 'FFmpegMetadata',
                'add_metadata': True,
                'add_chapters': True,
            },
            {
                'key': 'EmbedThumbnail',
                'already_have_thumbnail': False,
            },
        ],
    }

    try:
        info_dict = await get_running_loop().run_in_executor(
            None, partial(YoutubeDL(ydl_opts).extract_info, link, download=True)
        )
        entries = info_dict.get('entries', [info_dict])  # Handle both single videos and playlists
        for entry in entries:
            file_path = Path(TMP_DIR / f"{entry['id']}.{entry['ext']}")
            await progress_message.edit('Uploading file...')
            if entry.get('vcodec') == 'none':  # audio
                attributes = [
                    DocumentAttributeAudio(
                        duration=entry.get('duration'),
                        title=entry.get('title'),
                        performer=entry.get('uploader'),
                    )
                ]
            else:
                attributes = [
                    DocumentAttributeVideo(
                        duration=entry.get('duration'),
                        w=entry.get('width'),
                        h=entry.get('height'),
                    )
                ]
            file_path = file_path.rename(
                file_path.with_name(f"{re.sub('[/:*"\'<>|]', '_', entry['title'])}.{entry['ext']}")
            )
            await upload_file(
                event,
                file_path,
                progress_message,
                caption=f"<b>{entry['title']}</b>\n\n"
                f"👤 {entry.get('uploader', '')}\n"
                f"⏱ {entry.get('duration_string', '')}\n"
                f"💾 {naturalsize(entry.get('filesize_approx', 0), binary=True)}\n"
                f"📅 {entry.get('upload_date', '')}\n\n"
                f"{entry['webpage_url']}",
                attributes=attributes,
            )
            file_path.unlink(missing_ok=True)

        await progress_message.edit('Download and upload completed.')
    except Exception as e:  # noqa: BLE001
        await progress_message.edit(f'An error occurred:\n<pre>{e!s}</pre>')


class YTDLP(ModuleBase):
    name = 'YTDLP'
    description = 'Use YT-DLP'
    commands: ClassVar[ModuleBase.CommandsT] = {
        'ytdown': Command(
            handler=download_media,
            description='[url]: Download YouTube video or audio.',
            pattern=re.compile(rf'^/ytdown\s+{HTTP_URL_PATTERN}$'),
            condition=has_valid_url,
            is_applicable_for_reply=True,
        ),
        'ytformats': Command(
            handler=get_formats,
            description='[url]: Get available formats for a YouTube video.',
            pattern=re.compile(rf'^/ytformats\s+{HTTP_URL_PATTERN}$'),
            condition=has_valid_url,
            is_applicable_for_reply=True,
        ),
        'ytinfo': Command(
            name='ytinfo',
            handler=get_info,
            description='[url]: Get video information as JSON.',
            pattern=re.compile(rf'^/ytinfo\s+{HTTP_URL_PATTERN}$'),
            condition=has_valid_url,
            is_applicable_for_reply=True,
        ),
        'ytsub': Command(
            name='ytsub',
            handler=get_subtitles,
            description='[lang] [url]: Get YouTube video subtitles.',
            pattern=re.compile(rf'^/ytsub\s+([a-z]{{2}})\s+{YOUTUBE_URL_PATTERN}$'),
            condition=has_valid_url,
            is_applicable_for_reply=True,
        ),
    }
