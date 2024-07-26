from asyncio import get_running_loop, sleep
from collections import OrderedDict
from functools import partial
from pathlib import Path
from typing import ClassVar

import orjson
import regex as re
from humanize import naturalsize
from telethon.events import NewMessage
from yt_dlp import YoutubeDL

from src import PARENT_DIR, TMP_DIR
from src.modules.base import ModuleBase
from src.utils.command import Command
from src.utils.downloads import upload_file
from src.utils.json import json_options, process_dict
from src.utils.run import run_subprocess_shell
from src.utils.telegram import edit_or_send_as_file

YOUTUBE_URL_PATTERN = (
    r'(?:https?:\/\/)?(?:www\.)?(?:youtube\.com|youtu\.be)'
    r'\/(?:watch\?v=)?(?:embed\/)?(?:v\/)?(?:shorts\/)?(?:live\/)?'
    r'(?:(?:watch\?)?(?:time_continue=(?:\d+))?\&?(?:v=))?([^\s&]+)'
)
HTTP_URL_PATTERN = (
    r'https?:\/\/(www\.)?[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}'
    r'\b([-a-zA-Z0-9()@:%_\+.~#?&//=]*)'
)

cookies_file = Path(PARENT_DIR) / 'cookies.txt'
cookies = {'cookiefile': str(cookies_file.absolute())} if cookies_file.exists() else {}
params = {
    **cookies,
    'quiet': True,
}


async def get_youtube_info(event: NewMessage.Event) -> None:
    progress_message = await event.reply('Fetching video information...')
    link = event.message.raw_text.split('ytinfo ')[1].strip()
    try:
        info_dict = await get_running_loop().run_in_executor(
            None, partial(YoutubeDL(params).extract_info, link, download=False)
        )
        processed_info = process_dict(info_dict)
        json_str = orjson.dumps(processed_info, option=json_options).decode()
        await edit_or_send_as_file(
            event,
            progress_message,
            text=f'<pre>{json_str}</pre>',
            file_name=f"{info_dict['id']}.json",
            caption=info_dict['webpage_url'],
        )
        await progress_message.delete()
    except Exception as e:  # noqa: BLE001
        await progress_message.edit(f'An error occurred:\n<pre>{e!s}</pre>')


async def convert_subtitles(input_file: Path, srt_file: Path, txt_file: Path) -> None:
    """
    Convert VTT subtitle file to SRT and TXT formats.

    :param input_file: Path to the input VTT file
    :param srt_file: Path to the output SRT file
    :param txt_file: Path to the output TXT file
    """

    async for _output, _code in run_subprocess_shell(f'ffmpeg -i "{input_file}" "{srt_file}"'):
        await sleep(0.1)
        continue
    text_lines = OrderedDict.fromkeys(
        line.strip()
        for line in srt_file.read_text('utf-8').splitlines()
        if line.strip() and not re.match(r'^\d+$', line) and '-->' not in line
    )
    txt_file.write_text('\n'.join(text_lines.keys()))


async def get_youtube_subtitles(event: NewMessage.Event) -> None:
    progress_message = await event.reply('Downloading subtitles...')
    language, link = event.message.raw_text.split('ytsub ')[1].split(maxsplit=2)
    ydl_opts = {
        **params,
        'skip_download': True,
        'writeautomaticsub': True,
        'writesubtitles': True,
        'subtitleslangs': [language, f'{language}-orig'],
        'outtmpl': str(TMP_DIR / '%(title)s.%(ext)s'),
    }
    info_dict = {}
    try:
        info_dict = await get_running_loop().run_in_executor(
            None, partial(YoutubeDL(ydl_opts).extract_info, link, download=True)
        )
    except Exception as e:  # noqa: BLE001
        await progress_message.edit(f'An error occurred:\n<pre>{e!s}</pre>')

    subs = info_dict.get('requested_subtitles', {})
    if not subs:
        await progress_message.edit('No subtitles found.')
        return
    for _lang, sub_info in subs.items():
        vtt_path = Path(sub_info.get('filepath', ''))
        if not vtt_path.exists():
            continue
        srt_path = vtt_path.with_suffix('.srt')
        txt_path = vtt_path.with_suffix('.txt')
        await convert_subtitles(vtt_path, srt_path, txt_path)
        for file in [srt_path, txt_path]:
            await upload_file(
                event,
                file,
                progress_message,
                caption=f'https://youtu.be/{info_dict.get("id")}',
            )
            file.unlink(missing_ok=True)
        vtt_path.unlink(missing_ok=True)
    await progress_message.delete()


async def get_youtube_formats(event: NewMessage.Event) -> None:
    progress_message = await event.reply('Fetching available formats...')
    link = event.message.raw_text.split('ytformats ')[1].strip()
    try:
        ydl_opts = {**params, 'listformats': True}
        info_dict = await get_running_loop().run_in_executor(
            None, partial(YoutubeDL(ydl_opts).extract_info, link, download=False)
        )
        formats = info_dict.get('formats', [])
        if not formats:
            await progress_message.edit('No formats found.')
            return

        format_list = []
        for f in formats:
            format_id = f.get('format_id', 'N/A')
            ext = f.get('ext', 'N/A')
            resolution = f.get('resolution', 'N/A')
            filesize = f.get('filesize', 'N/A')
            if isinstance(filesize, int):
                filesize = naturalsize(filesize, binary=True)
            format_list.append(f'🆔 {format_id} | 📁 {ext} | 🖥️ {resolution} | 💾 {filesize}')

        await edit_or_send_as_file(
            event,
            progress_message,
            text=f'<b>Available formats:</b>\n\n{'\n'.join(format_list)}',
            file_name=f"{info_dict['id']}_formats.txt",
            caption=info_dict['webpage_url'],
        )
    except Exception as e:  # noqa: BLE001
        await progress_message.edit(f'An error occurred:\n<pre>{e!s}</pre>')


class YTDLP(ModuleBase):
    name = 'YT-DLP'
    description = 'Use YT-DLP'
    commands: ClassVar[ModuleBase.CommandsT] = {
        'ytformats': Command(
            handler=get_youtube_formats,
            description='[url]: Get available formats for a YouTube video.',
            pattern=re.compile(rf'^/ytformats\s+{HTTP_URL_PATTERN}$'),
        ),
        'ytinfo': Command(
            handler=get_youtube_info,
            description='[url]: Get video information as JSON.',
            pattern=re.compile(rf'^/ytinfo\s+{HTTP_URL_PATTERN}$'),
        ),
        'ytsub': Command(
            handler=get_youtube_subtitles,
            description='[lang] [url]: Get YouTube video subtitles.',
            pattern=re.compile(rf'^/ytsub\s+([a-z]{{2}})\s+{YOUTUBE_URL_PATTERN}$'),
            # condition=lambda event, message: True,  # Always applicable
            # is_applicable_for_reply=True,
        ),
    }
