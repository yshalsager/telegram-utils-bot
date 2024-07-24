from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import ClassVar

import orjson
import regex as re
from telethon.events import NewMessage
from youtube_transcript_api import YouTubeTranscriptApi
from yt_dlp import YoutubeDL

from src.modules.base import ModuleBase
from src.utils.command import Command
from src.utils.downloads import upload_file
from src.utils.json import json_options, process_dict
from src.utils.telegram import edit_or_send_as_file

YOUTUBE_URL_PATTERN = (
    r'(?:https?:\/\/)?(?:www\.)?(?:youtube\.com|youtu\.be)'
    r'\/(?:watch\?v=)?(?:embed\/)?(?:v\/)?(?:shorts\/)?(?:live\/)?'
    r'(?:(?:watch\?)?(?:time_continue=(?:\d+))?\&?(?:v=))?([^\s&]+)'
)


async def get_youtube_info(event: NewMessage.Event) -> None:
    progress_message = await event.reply('Fetching video information...')
    link = event.message.raw_text.split('ytinfo ')[1].strip()
    try:
        with YoutubeDL() as ytdl:
            info_dict = ytdl.extract_info(link, download=False)
        processed_info = process_dict(info_dict)
        json_str = orjson.dumps(processed_info, option=json_options).decode()
        await edit_or_send_as_file(
            event,
            progress_message,
            text=f'<pre>{json_str}</pre>',
            file_name=f"{info_dict['id']}.json",
        )
    except Exception as e:  # noqa: BLE001
        await event.reply(f'An error occurred: {e!s}')

    await progress_message.delete()


async def get_youtube_subtitles(event: NewMessage.Event) -> None:
    progress_message = await event.reply('Downloading subtitles...')
    language, link = event.message.raw_text.split('ytsub ')[1].split(maxsplit=2)
    try:
        # Extract video information
        with YoutubeDL() as ytdl:
            info_dict = ytdl.extract_info(link, download=False)
            video_id = info_dict.get('id')
            if video_title := info_dict.get('title', ''):
                video_title = re.sub(r'[^\w\-_\. ]', '', video_title)
        srt = YouTubeTranscriptApi.get_transcript(video_id, languages=[language])
        formatted_srt = '\n'.join(
            [
                f"{i + 1}\n{item['start']} --> {item['start'] + item['duration']}\n{item['text']}\n"
                for i, item in enumerate(srt)
            ]
        )

        with NamedTemporaryFile(suffix='.srt') as sub_file:
            sub_file.write(formatted_srt.encode('utf-8'))
            sub_file.seek(0)
            sub_file_path = Path(sub_file.name)
            sub_file_path = sub_file_path.rename(sub_file_path.with_stem(video_title))
            await upload_file(
                event,
                sub_file_path,
                progress_message,
                caption=f'https://www.youtube.com/watch?v={video_id}',
            )
    except Exception as e:  # noqa: BLE001
        await event.reply(f'An error occurred: {e!s}')

    await progress_message.delete()


class YTDLP(ModuleBase):
    name = 'YT-DLP'
    description = 'Use YT-DLP to download'
    commands: ClassVar[ModuleBase.CommandsT] = {
        'ytinfo': Command(
            handler=get_youtube_info,
            description='[url]: Get YouTube video information as JSON.',
            pattern=re.compile(rf'^/ytinfo\s+{YOUTUBE_URL_PATTERN}$'),
        ),
        'ytsub': Command(
            handler=get_youtube_subtitles,
            description='[lang] [url]: Get YouTube video subtitles. Works in reply mode too.',
            pattern=re.compile(rf'^/ytsub\s+([a-z]{{2}})\s+{YOUTUBE_URL_PATTERN}$'),
            # condition=lambda event, message: True,  # Always applicable
            # is_applicable_for_reply=True,
        ),
    }
