from pathlib import Path
from shlex import quote
from shutil import which
from tempfile import NamedTemporaryFile, TemporaryDirectory
from typing import ClassVar

import aiohttp
import regex as re
from telethon.events import CallbackQuery, NewMessage
from telethon.tl.custom import Message

from src import DOWNLOADS_DIR, PARENT_DIR, STATE_DIR
from src.modules.base import ModuleBase
from src.modules.plugins.media import build_media_upload_params
from src.modules.plugins.run import stream_shell_output
from src.utils.command import Command
from src.utils.downloads import (
    download_file,
    download_to_temp_file,
    get_download_name,
    get_filename_from_url,
    upload_file,
    upload_file_and_cleanup,
)
from src.utils.filters import (
    has_file,
    has_no_file,
    has_valid_url,
    is_admin_in_private,
    is_file,
)
from src.utils.i18n import t
from src.utils.patterns import HTTP_URL_PATTERN
from src.utils.telegram import get_reply_message, send_progress_message

GDL_DOWNLOAD_URL = (
    'https://raw.githubusercontent.com/Akianonymus/gdrive-downloader/master/release/gdl'
)
GDL_PATH = STATE_DIR / 'bin' / 'gdl'
GDL_REQUIRED_PROGRAMS = ('bash', 'curl', 'jq', 'xargs')
GDRIVE_ID_PATTERN = re.compile(r'^[A-Za-z0-9_-]{10,}$')
GDRIVE_URL_PATTERN = re.compile(
    r'https?://(?:'
    r'drive\.google\.com/[^\s<>"\']*id=[A-Za-z0-9_-]+'
    r'|drive\.google\.com/[^\s<>"\']*file/d/[A-Za-z0-9_-]+'
    r'|drive\.google\.com/[^\s<>"\']*drive[^\s<>"\']*folders/[A-Za-z0-9_-]+'
    r'|docs\.google\.com/[^\s<>"\']*/d/[A-Za-z0-9_-]+'
    r')[^\s<>"\']*'
)


def extract_gdrive_input(text: str) -> str | None:
    if match := re.search(GDRIVE_URL_PATTERN, text):
        return match.group(0).rstrip('.,،)')

    text = text.strip()
    if re.fullmatch(GDRIVE_ID_PATTERN, text):
        return text
    return None


def extract_gdrive_command_input(text: str) -> str:
    match = DownloadUpload.commands['gdrive'].pattern.match(text)
    return (match.group(1) if match else '') or ''


def collect_downloaded_files(download_dir: Path) -> list[Path]:
    return sorted(path for path in download_dir.rglob('*') if path.is_file())


def missing_gdl_dependencies() -> list[str]:
    return [program for program in GDL_REQUIRED_PROGRAMS if which(program) is None]


async def ensure_gdrive_downloader() -> Path:
    if GDL_PATH.exists():
        return GDL_PATH

    GDL_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp_path = GDL_PATH.with_suffix('.tmp')
    async with aiohttp.ClientSession() as session, session.get(GDL_DOWNLOAD_URL) as response:
        response.raise_for_status()
        temp_path.write_bytes(await response.read())
    temp_path.chmod(0o755)
    temp_path.replace(GDL_PATH)
    return GDL_PATH


async def download_from_url(
    event: NewMessage.Event | CallbackQuery.Event,
    url: str,
    download_dir: Path,
    progress_message: Message | None = None,
) -> Path:
    filename = get_filename_from_url(url)
    download_to = download_dir / filename
    cmd = f"aria2c -x 16 -d {download_dir} -o {filename} '{url}' --allow-overwrite=true"
    await stream_shell_output(event, cmd, progress_message=progress_message)
    return download_to


async def download_file_command(event: NewMessage.Event | CallbackQuery.Event) -> None:
    progress_message = await send_progress_message(event, t('starting_file_download'))
    reply_message = await get_reply_message(event, previous=True)
    if url_match := re.search(HTTP_URL_PATTERN, reply_message.raw_text):
        url = url_match.group(0)
        download_to = await download_from_url(
            event, url, DOWNLOADS_DIR, progress_message=progress_message
        )
        if not download_to.exists():
            await event.reply(t('download_failed'))
            return
    else:
        reply_message = await get_reply_message(event, previous=True)
        download_to = DOWNLOADS_DIR / get_download_name(reply_message)
        with download_to.open('wb') as temp_file:
            temp_file_path = await download_file(event, temp_file, reply_message, progress_message)
            temp_file_path.rename(download_to)
    await progress_message.edit(f'{t("file_downloaded")}: <code>{download_to}</code>')


async def download_from_gdrive(event: NewMessage.Event | CallbackQuery.Event) -> None:
    input_text = (
        extract_gdrive_command_input(event.message.raw_text or '')
        if isinstance(event, NewMessage.Event)
        else ''
    )
    if not input_text:
        reply_message = await get_reply_message(event, previous=True)
        input_text = reply_message.raw_text if reply_message else ''

    gdrive_input = extract_gdrive_input(input_text)
    if not gdrive_input:
        await event.reply(t('no_valid_url_found'))
        return

    progress_message = await send_progress_message(event, t('starting_file_download'))
    if missing_dependencies := missing_gdl_dependencies():
        await progress_message.edit(
            f'Missing gdrive-downloader dependencies: <code>{", ".join(missing_dependencies)}</code>'
        )
        return

    if not GDL_PATH.exists():
        await progress_message.edit('Installing gdrive-downloader…')
    try:
        gdl_path = await ensure_gdrive_downloader()
    except aiohttp.ClientError as e:
        await progress_message.edit(t('an_error_occurred', error=f'\n<pre>{e}</pre>'))
        return

    with TemporaryDirectory(dir=DOWNLOADS_DIR) as download_dir_name:
        download_dir = Path(download_dir_name)
        command = (
            f'{quote(str(gdl_path))} {quote(gdrive_input)} '
            f'-d {quote(str(download_dir))} --skip-internet-check'
        )
        await stream_shell_output(event, command, progress_message=progress_message)
        output_files = collect_downloaded_files(download_dir)
        if not output_files:
            await progress_message.edit(t('download_failed'))
            return

        await progress_message.edit(t('download_complete_starting_upload'))
        for idx, output_file in enumerate(output_files, start=1):
            await progress_message.edit(f'{t("uploading")} {idx}/{len(output_files)}')
            await upload_file_and_cleanup(event, output_file, progress_message)

    await progress_message.edit(f'{t("file_uploaded")}: <code>{len(output_files)}</code>')


async def upload_file_command(event: NewMessage.Event) -> None:
    progress_message = await event.reply(t('starting_file_upload'))
    for file_path in PARENT_DIR.glob(event.message.text.split(maxsplit=1)[1].strip()):
        if file_path.exists():
            await upload_file(event, file_path, progress_message)
            await progress_message.edit(f'{t("file_uploaded")}: <code>{file_path.name}</code>')
            return
    await progress_message.edit(t('no_files_found'))


async def upload_from_url_command(event: NewMessage.Event) -> None:
    reply_message = await get_reply_message(event, previous=True)
    message = reply_message or event.message
    custom_name = ''
    url_match = re.search(HTTP_URL_PATTERN, message.raw_text)
    if url_match:
        url = url_match.group(0)
    else:
        await event.reply(t('no_valid_url_found'))
        return
    if custom := (message.raw_text or '').split('|', 1):
        custom_name = custom[1].strip() if len(custom) > 1 else ''
    progress_message = await send_progress_message(event, t('starting_file_download'))

    with NamedTemporaryFile(dir=DOWNLOADS_DIR, delete=False) as temp_file:
        download_to = await download_from_url(
            event, url, Path(temp_file.name).parent, progress_message=progress_message
        )
        if not download_to.exists():
            await progress_message.edit(t('download_failed'))
            return

        if custom_name:
            new_download_to = download_to.with_name(custom_name)
            download_to.rename(new_download_to)
            download_to = new_download_to

        await progress_message.edit(t('download_complete_starting_upload'))
        await upload_file_and_cleanup(event, download_to, progress_message, unlink=False)
        await progress_message.edit(f'{t("file_uploaded")}: <code>{download_to.name}</code>')
        Path(temp_file.name).unlink(missing_ok=True)


async def upload_as_file_or_media(event: NewMessage.Event | CallbackQuery.Event) -> None:
    if isinstance(event, CallbackQuery.Event):
        force_document = event.data.decode().split('_')[-1] == 'file'
    else:
        force_document = event.message.text.split(maxsplit=1)[-1].strip() == 'file'

    reply_message = await get_reply_message(event, previous=True)
    progress_message = await send_progress_message(event, t('starting_file_download'))
    _type = 'file' if force_document else 'media'
    output_file_name = reply_message.file.name or f'{_type}{reply_message.file.ext}'
    if output_file_name == reply_message.file.name and not Path(output_file_name).suffix:
        output_file_name = f'{output_file_name}{reply_message.file.ext}'
    async with download_to_temp_file(event, reply_message, progress_message) as temp_file_path:
        await progress_message.edit(t('download_complete_starting_upload'))
        output_file = temp_file_path.rename(temp_file_path.with_name(output_file_name))
        upload_kwargs: dict = {}
        if not force_document and (
            reply_message.audio
            or reply_message.voice
            or reply_message.video
            or reply_message.video_note
        ):
            upload_kwargs = await build_media_upload_params(
                output_file, is_voice=bool(reply_message.voice)
            )
        await upload_file_and_cleanup(
            event,
            output_file,
            progress_message,
            force_document=force_document,
            **upload_kwargs,
        )

    await progress_message.edit(f'{t("file_uploaded_as")} {_type}: <code>{output_file_name}</code>')


class DownloadUpload(ModuleBase):
    name = 'Download'
    description = t('_download_module_description')
    commands: ClassVar[ModuleBase.CommandsT] = {
        'download': Command(
            handler=download_file_command,
            description=t('_download_description'),
            pattern=re.compile(r'^/download(?:\s+(.+))?$'),
            condition=lambda event, message: (
                is_admin_in_private(event, message)
                and (has_file(event, message) or has_valid_url(event, message))
            ),
            is_applicable_for_reply=True,
        ),
        'upload': Command(
            handler=upload_file_command,
            description=t('_upload_description'),
            pattern=re.compile(r'^/upload\s+(.+)$'),
            condition=lambda event, message: (
                is_admin_in_private(event, message) and has_no_file(event, message)
            ),
        ),
        'gdrive': Command(
            handler=download_from_gdrive,
            description=t('_gdrive_description'),
            pattern=re.compile(r'^/gdrive(?:\s+(.+))?$'),
            condition=is_admin_in_private,
            is_applicable_for_reply=True,
        ),
        'upload file': Command(
            handler=upload_as_file_or_media,
            description=t('_upload_file_description'),
            pattern=re.compile(r'^/upload\s+file$'),
            condition=lambda event, message: (
                has_file(event, message) and not is_file(event, message)
            ),
            is_applicable_for_reply=True,
        ),
        'upload media': Command(
            handler=upload_as_file_or_media,
            description=t('_upload_media_description'),
            pattern=re.compile(r'^/upload\s+media$'),
            condition=lambda event, message: has_file(event, message) and is_file(event, message),
            is_applicable_for_reply=True,
        ),
        'upload url': Command(
            handler=upload_from_url_command,
            description=t('_upload_url_description'),
            pattern=re.compile(r'^/upload\s+url\s+(.+)$'),
            condition=has_valid_url,
            is_applicable_for_reply=True,
        ),
    }
