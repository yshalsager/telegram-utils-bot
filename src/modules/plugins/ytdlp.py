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

from src import STATE_DIR, TMP_DIR
from src.modules.base import ModuleBase
from src.utils.command import Command
from src.utils.downloads import upload_file
from src.utils.filters import has_valid_url
from src.utils.i18n import t
from src.utils.json_processing import json_options, process_dict
from src.utils.patterns import HTTP_URL_PATTERN, YOUTUBE_URL_PATTERN
from src.utils.progress import progress_callback
from src.utils.subtitles import convert_subtitles
from src.utils.telegram import (
    delete_callback_after,
    edit_or_send_as_file,
    get_reply_message,
    inline_choice_grid,
    send_progress_message,
)

cookies_file = STATE_DIR / 'cookies.txt'
netrc_file = STATE_DIR / '.netrc'

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

# https://github.com/Brainicism/bgutil-ytdlp-pot-provider
params['extractor_args'] = {
    'youtubepot-bgutilhttp': {'base_url': 'http://bgutil-provider:4416'},
    'youtube': {'player_client': ['default,mweb,web_creator']},
}

FFMPEG_METADATA_PP = {
    'key': 'FFmpegMetadata',
    'add_metadata': True,
    'add_chapters': True,
}
THUMB_PP = {
    'key': 'FFmpegThumbnailsConvertor',
    'format': 'jpg',
    'when': 'before_dl',
}
AUDIO_EXTRACT_PP = {
    'key': 'FFmpegExtractAudio',
    'preferredcodec': 'opus',
    'preferredquality': '64',
}


def sanitize_filename(text: str) -> str:
    return str(re.sub(r'[/\\:*?\"<>|]', '_', text))


async def get_target_message(event: NewMessage.Event | CallbackQuery.Event) -> Message:
    return (
        await get_reply_message(event, previous=True)
        if isinstance(event, CallbackQuery.Event)
        else event.message
    )


def extract_link(text: str) -> str | None:
    if match := re.search(HTTP_URL_PATTERN, text):
        return str(match.group(0))
    return None


async def get_link_from_event(event: NewMessage.Event | CallbackQuery.Event) -> str | None:
    message = await get_target_message(event)
    return extract_link(message.raw_text)


def ydl_progress_hooks(message: Message) -> list[Any]:
    return [partial(download_hook, message=message, loop=get_running_loop())]


async def ydl_extract(link: str, ydl_opts: dict[str, Any], *, download: bool) -> dict[str, Any]:
    return await get_running_loop().run_in_executor(
        None,
        partial(YoutubeDL(ydl_opts).extract_info, link, download=download),
    )


def build_caption(
    title: str,
    url: str,
    lines: list[str],
    *,
    title_suffix: str = '',
    blank_line_before_url: bool = True,
) -> str:
    caption = f'<b>{title}</b>{title_suffix}'
    if lines:
        caption += f'\n\n{"\n".join(lines)}'
    caption += ('\n\n' if blank_line_before_url else '\n') + url
    return caption


def mime_type_for_path(file_path: Path) -> str | None:
    suffix = file_path.suffix.lower()
    return 'video/mp4' if suffix == '.mp4' else 'audio/ogg' if suffix == '.ogg' else None


def cleanup_paths(paths: set[Path]) -> None:
    for path in paths:
        path.unlink(missing_ok=True)


def media_attributes(
    entry: dict[str, Any],
    *,
    is_audio: bool,
    duration: int | None = None,
    title: str | None = None,
) -> list[Any]:
    if is_audio:
        return [
            DocumentAttributeAudio(
                duration=int(duration if duration is not None else entry.get('duration') or 0),
                title=title or entry.get('title'),
                performer=entry.get('uploader'),
                voice=False,
            )
        ]

    return [
        DocumentAttributeVideo(
            duration=int(duration if duration is not None else entry.get('duration') or 0),
            w=entry.get('width') or 0,
            h=entry.get('height') or 0,
            supports_streaming=True,
        )
    ]


def download_hook(d: dict[str, Any], message: Message, loop: Any) -> None:
    # This won't work with external downloader
    if d['status'] == 'downloading':
        total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
        current = d.get('downloaded_bytes', 0)
        run_coroutine_threadsafe(
            progress_callback(current, total, message, t('downloading')),
            loop,
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


def hms_to_seconds(time_str: str) -> int:
    h, m, s = time_str.split(':')
    return int(h) * 3600 + int(m) * 60 + int(s)


def pick_thumb(dir_path: Path, pattern: str) -> tuple[Path | None, set[Path]]:
    candidates = [p for p in dir_path.glob(pattern) if p.suffix.lower() in {'.jpg', '.jpeg'}]
    candidates.sort(key=lambda p: p.name)
    thumb_path = candidates[0] if candidates else None
    return thumb_path, {thumb_path} if thumb_path else set()


async def download_video_segment(event: NewMessage.Event) -> None:
    progress_message = await send_progress_message(event, t('starting_download'))
    message = event.message
    match = re.search(
        rf'^/ytclip\s+(?P<url>{HTTP_URL_PATTERN})\s+(?P<start>\d{{2}}:\d{{2}}:\d{{2}})\s+(?P<end>\d{{2}}:\d{{2}}:\d{{2}})$',
        message.raw_text,
    )
    if not match:
        await progress_message.edit(t('invalid_ytclip_command'))
        return

    start_time = match.group('start')
    end_time = match.group('end')
    start_seconds = hms_to_seconds(start_time)
    end_seconds = hms_to_seconds(end_time)
    if start_seconds >= end_seconds:
        await progress_message.edit(t('invalid_ytclip_command'))
        return

    def download_ranges(*_: Any) -> list[dict[str, float]]:
        return [{'start_time': float(start_seconds), 'end_time': float(end_seconds)}]

    ydl_opts = {
        **params,
        'noplaylist': True,
        'format': 'bv*[height<=480]+ba/b[height<=480]',
        'outtmpl': str(TMP_DIR / f'%(id)s-{start_seconds}-{end_seconds}.%(ext)s'),
        'progress_hooks': ydl_progress_hooks(progress_message),
        'postprocessors': [FFMPEG_METADATA_PP, THUMB_PP],
        'writethumbnail': True,
        'download_ranges': download_ranges,
    }

    try:
        info_dict = await ydl_extract(match.group('url'), ydl_opts, download=True)
        file_candidates = [
            p
            for p in TMP_DIR.glob(f'{info_dict["id"]}-{start_seconds}-{end_seconds}.*')
            if p.suffix.lower() not in {'.jpg', '.jpeg'}
        ]
        file_candidates.sort(key=lambda p: p.stat().st_size, reverse=True)
        if not file_candidates:
            await progress_message.edit(t('an_error_occurred', error=t('no_file_found')))
            return
        file_path = file_candidates[0]

        thumb_path, thumb_cleanup_paths = pick_thumb(
            TMP_DIR, f'{info_dict["id"]}-{start_seconds}-{end_seconds}.*'
        )
        await progress_message.edit(t('uploading_file'))

        safe_title = sanitize_filename(info_dict['title'])
        start_slug = start_time.replace(':', '-')
        end_slug = end_time.replace(':', '-')
        file_path = file_path.rename(
            file_path.with_name(f'{safe_title}-{start_slug}-{end_slug}{file_path.suffix}')
        )

        attributes = media_attributes(
            info_dict,
            is_audio=False,
            duration=int(info_dict.get('duration') or (end_seconds - start_seconds)),
        )
        await upload_file(
            event,
            file_path,
            progress_message,
            caption=build_caption(
                info_dict['title'],
                info_dict['webpage_url'],
                [
                    f'👤 {info_dict.get("uploader", "")}',
                    f'⏱ {start_time} - {end_time}',
                    f'💾 {naturalsize(file_path.stat().st_size, binary=True)}',
                ],
            ),
            attributes=attributes,
            supports_streaming=True,
            mime_type='video/mp4' if file_path.suffix.lower() == '.mp4' else None,
            thumb=str(thumb_path) if thumb_path else None,
        )
        file_path.unlink(missing_ok=True)
        cleanup_paths(thumb_cleanup_paths)
        await progress_message.edit(t('download_and_upload_completed'))
    except Exception as e:  # noqa: BLE001
        await progress_message.edit(t('an_error_occurred', error=f'\n<pre>{e!s}</pre>'))


async def get_info(event: NewMessage.Event | CallbackQuery.Event) -> None:
    progress_message = await send_progress_message(event, t('fetching_information'))
    link = await get_link_from_event(event)
    if not link:
        await progress_message.edit(t('no_valid_url_found'))
        return
    ydl_opts = {
        **params,
        'progress_hooks': ydl_progress_hooks(progress_message),
    }
    try:
        info_dict = await ydl_extract(link, ydl_opts, download=False)
        processed_info = process_dict(info_dict)
        json_str = orjson.dumps(processed_info, option=json_options).decode()
        edited = await edit_or_send_as_file(
            event,
            progress_message,
            text=f'<pre>{json_str}</pre>',
            file_name=f'{info_dict["id"]}.json',
            caption=f'<b>{info_dict["title"]}</b>\n\n'
            f"👤 <a href='{info_dict.get('uploader_url', '')}'>{info_dict.get('uploader', '')}</a>\n"
            f'📽️ {len(info_dict.get("entries", [1]))}\n'
            f'{info_dict["webpage_url"]}',
        )
        if not edited:
            await progress_message.delete()
    except Exception as e:  # noqa: BLE001
        await progress_message.edit(t('an_error_occurred', error=f'\n<pre>{e!s}</pre>'))


async def get_subtitles(event: NewMessage.Event) -> None:
    progress_message = await send_progress_message(event, t('downloading_subtitles'))
    message = await get_target_message(event)
    link = extract_link(message.raw_text)
    if not link:
        await progress_message.edit(t('no_valid_url_found'))
        return
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
        'progress_hooks': ydl_progress_hooks(progress_message),
    }
    try:
        info_dict = await ydl_extract(link, ydl_opts, download=True)
    except Exception as e:  # noqa: BLE001
        await progress_message.edit(t('an_error_occurred', error=f'\n<pre>{e!s}</pre>'))
        return

    entries = info_dict.get('entries', [info_dict])
    for entry in entries:
        subs = entry.get('requested_subtitles', {})
        if not subs:
            await progress_message.edit(t('no_subtitles_found', item=entry.get('id')))
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
                    file.with_stem(f'{sanitize_filename(entry["title"])}-{lang}')
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
    progress_message = await send_progress_message(event, t('fetching_available_formats'))
    link = await get_link_from_event(event)
    if not link:
        await progress_message.edit(t('no_valid_url_found'))
        return
    try:
        ydl_opts = {
            **params,
            'listformats': True,
        }
        info_dict = await ydl_extract(link, ydl_opts, download=False)
        common_formats, total_sizes, worst_audio_size, entry_count = (
            calculate_common_formats_and_sizes(info_dict)
        )
        if not common_formats:
            await progress_message.edit(t('no_formats_found'))
            return

        format_list = []
        for format_id, f in common_formats.items():
            if format_id not in total_sizes or total_sizes[format_id] == 0:
                continue
            total_size = total_sizes[format_id]
            if f['vcodec'] != 'none':
                total_size += worst_audio_size * entry_count
            format_name = (
                f'🖥 {f["format"]} | 📁 {f["ext"]} | 💾 {naturalsize(total_size, binary=True)}'
            )
            format_list.append(format_name)

        await edit_or_send_as_file(
            event,
            progress_message,
            text=f'<b>{t("available_formats")} ({entry_count} items):</b>\n\n{"\n".join(format_list)}',
            file_name=f'{info_dict["id"]}_formats.txt',
            caption=info_dict['webpage_url'],
        )
    except Exception as e:  # noqa: BLE001
        await progress_message.edit(f'An error occurred:\n<pre>{e!s}</pre>')


async def download_media(event: NewMessage.Event | CallbackQuery.Event) -> None:  # noqa: C901, PLR0912, PLR0915
    is_url_event = (
        isinstance(event, CallbackQuery.Event)
        and event.data
        and event.data.decode().endswith('ytdown')
    )
    is_command_event = isinstance(event, NewMessage.Event)
    if is_command_event:
        text = t('choose_format_type')
        buttons = [
            [
                Button.inline(t('audio'), 'ytdown|type|audio'),
                Button.inline(t('video'), 'ytdown|type|video'),
            ]
        ]
        await event.reply(text, buttons=buttons)
        return

    if isinstance(event, CallbackQuery.Event) and event.data:
        data = event.data.decode('utf-8')
        if data.startswith('ytdown|type|') or is_url_event:
            _type = await inline_choice_grid(
                event,
                prefix='ytdown|type|',
                prompt_text=t('choose_format_type'),
                pairs=[
                    (t('audio'), 'ytdown|type|audio'),
                    (t('video'), 'ytdown|type|video'),
                ],
                cols=2,
                cast=str,
            )
            if _type is None:
                return
            _format = ''
        else:
            parts = data.split('|')
            _type = parts[1] if len(parts) > 1 else ''
            _format = parts[2] if len(parts) > 2 else ''

    reply_message = await get_reply_message(event, previous=True)
    link = extract_link(reply_message.raw_text)
    if not link:
        await event.edit(t('no_valid_url_found'))
        return
    progress_message = await send_progress_message(event, t('starting_process'))

    if _type in ('audio', 'video') and not _format:
        await progress_message.edit(t('fetching_available_formats'))
        ydl_opts = {**params, 'listformats': True}
        info_dict = await ydl_extract(link, ydl_opts, download=False)
        common_formats, total_sizes, worst_audio_size, entry_count = (
            calculate_common_formats_and_sizes(info_dict)
        )
        if not common_formats:
            await progress_message.edit(t('no_formats_found'))
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
                format_name = f'{f["format"]} | {f["ext"]} | {naturalsize(total_size, binary=True)}'
                buttons.append([Button.inline(format_name, f'ytdown|{_type}|{format_id}')])
        if not buttons:
            await progress_message.edit(t('no_suitable_formats_found'))
            return
        await event.edit(
            t('choose_format_for', type=t(_type), entry_count=entry_count), buttons=buttons
        )
        return

    # User selected a specific format
    await progress_message.edit(t('starting_download'))
    format_id = _format if _type == 'audio' else f'{_format}+worstaudio/best'
    delete_callback_after(event)
    post_processors = [FFMPEG_METADATA_PP, THUMB_PP]
    if _type == 'audio':
        post_processors.append(AUDIO_EXTRACT_PP)
    ydl_opts = {
        **params,
        'format': format_id,
        'outtmpl': str(TMP_DIR / '%(id)s.%(ext)s'),
        'progress_hooks': ydl_progress_hooks(progress_message),
        'writethumbnail': True,
        'postprocessors': post_processors,
    }

    try:
        info_dict = await ydl_extract(link, ydl_opts, download=True)
        entries = info_dict.get('entries', [info_dict])  # Handle both single videos and playlists
        for entry in entries:
            file_path = Path(
                TMP_DIR / f'{entry["id"]}.{entry["ext"] if _type == "video" else "opus"}'
            )
            thumb_path, thumb_cleanup_paths = pick_thumb(TMP_DIR, f'{entry["id"]}.*')
            await progress_message.edit(t('uploading_file'))
            is_audio = entry.get('vcodec') == 'none'
            attributes = media_attributes(entry, is_audio=is_audio)
            file_path = file_path.rename(file_path.with_stem(sanitize_filename(entry['title'])))

            if is_audio:
                file_path = file_path.rename(file_path.with_suffix('.ogg'))

            await upload_file(
                event,
                file_path,
                progress_message,
                caption=build_caption(
                    entry['title'],
                    entry['webpage_url'],
                    [
                        f'👤 {entry.get("uploader", "")}',
                        f'⏱ {entry.get("duration_string", "")}',
                        f'💾 {naturalsize(entry.get("filesize_approx", 0), binary=True)}',
                        f'📅 {entry.get("upload_date", "")}',
                    ],
                ),
                attributes=attributes,
                supports_streaming=entry.get('vcodec') != 'none',
                mime_type=mime_type_for_path(file_path),
                thumb=str(thumb_path) if thumb_path else None,
            )
            file_path.unlink(missing_ok=True)
            cleanup_paths(thumb_cleanup_paths)

        await progress_message.edit(t('download_and_upload_completed'))
    except Exception as e:  # noqa: BLE001
        await progress_message.edit(t('an_error_occurred', error=f'\n<pre>{e!s}</pre>'))


async def download_audio_segment(event: NewMessage.Event) -> None:
    progress_message = await send_progress_message(event, t('starting_audio_download'))
    message = event.message
    match = re.search(
        rf'^/ytaudio\s+(?P<url>{HTTP_URL_PATTERN})\s+(?P<start>\d{{2}}:\d{{2}}:\d{{2}})\s+(?P<end>\d{{2}}:\d{{2}}:\d{{2}})$',
        message.raw_text,
    )
    if not match:
        await progress_message.edit(t('invalid_ytaudio_command'))
        return

    start_time = match.group('start')
    end_time = match.group('end')
    start_seconds = hms_to_seconds(start_time)
    end_seconds = hms_to_seconds(end_time)
    ydl_opts = {
        **params,
        'format': 'wa',
        'outtmpl': str(TMP_DIR / f'%(id)s-{start_seconds}-{end_seconds}.%(ext)s'),
        'progress_hooks': ydl_progress_hooks(progress_message),
        'postprocessors': [THUMB_PP, AUDIO_EXTRACT_PP],
        'writethumbnail': True,
        'external_downloader': 'ffmpeg_i',
        'external_downloader_args': ['-ss', str(start_seconds), '-to', str(end_seconds)],
    }

    try:
        info_dict = await ydl_extract(match.group('url'), ydl_opts, download=True)
        file_path = Path(TMP_DIR / f'{info_dict["id"]}-{start_seconds}-{end_seconds}.opus')
        file_path = file_path.rename(file_path.with_suffix('.ogg'))
        thumb_path, thumb_cleanup_paths = pick_thumb(
            TMP_DIR, f'{info_dict["id"]}-{start_seconds}-{end_seconds}.*'
        )
        await progress_message.edit(t('uploading_audio_segment'))
        attributes = media_attributes(
            info_dict,
            is_audio=True,
            duration=end_seconds - start_seconds,
            title=f'{info_dict["title"]} ({start_time} - {end_time})',
        )
        await upload_file(
            event,
            file_path,
            progress_message,
            caption=build_caption(
                info_dict['title'],
                info_dict['webpage_url'],
                [
                    f'👤 {info_dict.get("uploader", "")}',
                    f'⏱ {end_seconds - start_seconds} seconds',
                ],
                title_suffix=f' ({start_time} - {end_time})',
                blank_line_before_url=False,
            ),
            attributes=attributes,
            mime_type='audio/ogg',
            thumb=str(thumb_path) if thumb_path else None,
        )
        file_path.unlink(missing_ok=True)
        cleanup_paths(thumb_cleanup_paths)
        await progress_message.edit(t('audio_segment_download_and_upload_completed'))
    except Exception as e:  # noqa: BLE001
        await progress_message.edit(t('an_error_occurred', error=f'\n<pre>{e!s}</pre>'))


class YTDLP(ModuleBase):
    name = 'YTDLP'
    description = t('_ytdlp_module_description')
    commands: ClassVar[ModuleBase.CommandsT] = {
        'ytclip': Command(
            handler=download_video_segment,
            description=t('_ytclip_description'),
            pattern=re.compile(
                rf'^/ytclip\s+{HTTP_URL_PATTERN}\s+\d{2}:\d{2}:\d{2}\s+\d{2}:\d{2}:\d{2}$'
            ),
            condition=has_valid_url,
            is_applicable_for_reply=False,
        ),
        'ytaudio': Command(
            handler=download_audio_segment,
            description=t('_ytaudio_description'),
            pattern=re.compile(
                rf'^/ytaudio\s+{HTTP_URL_PATTERN}\s+\d{2}:\d{2}:\d{2}\s+\d{2}:\d{2}:\d{2}$'
            ),
            condition=has_valid_url,
            is_applicable_for_reply=False,
        ),
        'ytdown': Command(
            handler=download_media,
            description=t('_ytdown_description'),
            pattern=re.compile(rf'^/ytdown\s+{HTTP_URL_PATTERN}$'),
            condition=has_valid_url,
            is_applicable_for_reply=True,
        ),
        'ytformats': Command(
            handler=get_formats,
            description=t('_ytformats_description'),
            pattern=re.compile(rf'^/ytformats\s+{HTTP_URL_PATTERN}$'),
            condition=has_valid_url,
            is_applicable_for_reply=True,
        ),
        'ytinfo': Command(
            handler=get_info,
            description=t('_ytinfo_description'),
            pattern=re.compile(rf'^/ytinfo\s+{HTTP_URL_PATTERN}$'),
            condition=has_valid_url,
            is_applicable_for_reply=True,
        ),
        'ytsub': Command(
            handler=get_subtitles,
            description=t('_ytsub_description'),
            pattern=re.compile(rf'^/ytsub\s+([a-z]{{2}})\s+{YOUTUBE_URL_PATTERN}$'),
            condition=has_valid_url,
            is_applicable_for_reply=True,
        ),
    }
