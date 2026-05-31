from pathlib import Path
from shlex import join as shell_join
from shlex import quote
from tempfile import TemporaryDirectory
from typing import Any, ClassVar

import aiohttp
import regex as re
from telethon.events import CallbackQuery, NewMessage
from telethon.tl.custom import Message

from src import DOWNLOADS_DIR, PARENT_DIR
from src.modules.base import ModuleBase
from src.modules.plugins.media import (
    ALLOWED_AUDIO_FORMATS,
    ALLOWED_VIDEO_FORMATS,
    build_media_upload_params,
)
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
from src.utils.google_drive import (
    GDL_PATH,
    GDRIVE_URL_PATTERN,
    collect_downloaded_files,
    ensure_gdrive_downloader,
    extract_gdrive_input,
    missing_gdl_dependencies,
)
from src.utils.i18n import t
from src.utils.patterns import HTTP_URL_PATTERN
from src.utils.remote_files import ExternalDownload, RemoteFile, resolve_download_plan
from src.utils.telegram import get_reply_message, send_progress_message

MEDIA_UPLOAD_EXTS = {f'.{ext}' for ext in ALLOWED_AUDIO_FORMATS | ALLOWED_VIDEO_FORMATS}


def collect_upload_paths(pattern: str, base_dir: Path = PARENT_DIR) -> list[Path]:
    pattern_path = Path(pattern)
    if pattern_path.is_absolute():
        paths = Path('/').glob(pattern_path.as_posix().lstrip('/'))
    else:
        paths = base_dir.glob(pattern)
    return sorted((path for path in paths if path.is_file()), key=lambda path: path.as_posix())


def extract_gdrive_command_input(text: str) -> str:
    match = DownloadUpload.commands['gdrive'].pattern.match(text)
    return (match.group(1) if match else '') or ''


def has_gdrive_download_input(event: NewMessage.Event, message: Message | None) -> bool:
    if not is_admin_in_private(event, message or event.message):
        return False
    if DownloadUpload.commands['gdrive'].pattern.match(event.message.raw_text or ''):
        return True
    target = message or event.message
    return bool(re.search(GDRIVE_URL_PATTERN, target.raw_text or ''))


async def download_from_url(
    event: NewMessage.Event | CallbackQuery.Event,
    url: str,
    download_dir: Path,
    progress_message: Message | None = None,
    filename: str | None = None,
    headers: dict[str, str] | None = None,
) -> Path:
    filename = filename or get_filename_from_url(url)
    download_to = download_dir / filename
    header_args = ' '.join(
        f'--header={quote(f"{header}: {value}")}' for header, value in (headers or {}).items()
    )
    cmd = (
        f'aria2c -x 16 -d {quote(str(download_dir))} -o {quote(filename)} '
        f'{header_args} '
        f'{quote(url)} --allow-overwrite=true'
    )
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


async def upload_file_command(event: NewMessage.Event) -> None:
    progress_message = await event.reply(t('starting_file_upload'))
    upload_paths = collect_upload_paths(event.message.text.split(maxsplit=1)[1].strip())
    for idx, file_path in enumerate(upload_paths, start=1):
        if len(upload_paths) > 1:
            await progress_message.edit(f'{t("uploading")} {idx}/{len(upload_paths)}')
        await upload_file(event, file_path, progress_message)
    if upload_paths:
        uploaded = upload_paths[0].name if len(upload_paths) == 1 else str(len(upload_paths))
        await progress_message.edit(f'{t("file_uploaded")}: <code>{uploaded}</code>')
        return
    await progress_message.edit(t('no_files_found'))


async def download_remote_files(
    event: NewMessage.Event | CallbackQuery.Event,
    remote_files: list[RemoteFile],
    download_dir: Path,
    progress_message: Message,
    custom_name: str = '',
) -> list[Path]:
    output_files = []
    for idx, remote_file in enumerate(remote_files, start=1):
        if len(remote_files) > 1:
            await progress_message.edit(f'{t("downloading")} {idx}/{len(remote_files)}')
        filename = (
            custom_name if custom_name and len(remote_files) == 1 else Path(remote_file.name).name
        )
        output_file = await download_from_url(
            event,
            remote_file.url,
            download_dir,
            progress_message=progress_message,
            filename=filename,
            headers=remote_file.headers,
        )
        if not output_file.exists():
            return []
        output_files.append(output_file)
    return output_files


async def upload_output_files(
    event: NewMessage.Event | CallbackQuery.Event,
    output_files: list[Path],
    progress_message: Message,
) -> None:
    for idx, output_file in enumerate(output_files, start=1):
        if len(output_files) > 1:
            await progress_message.edit(f'{t("uploading")} {idx}/{len(output_files)}')
        await upload_file_and_cleanup(
            event,
            output_file,
            progress_message,
            **await build_download_upload_params(output_file),
        )


async def build_download_upload_params(output_file: Path) -> dict[str, Any]:
    if output_file.suffix.lower() not in MEDIA_UPLOAD_EXTS:
        return {}
    try:
        return await build_media_upload_params(output_file)
    except IndexError, KeyError, ValueError:
        return {}


async def collect_download_plan_files(
    event: NewMessage.Event | CallbackQuery.Event,
    plan: ExternalDownload | list[RemoteFile],
    download_dir: Path,
    progress_message: Message,
    custom_name: str = '',
) -> list[Path]:
    if isinstance(plan, ExternalDownload):
        await stream_shell_output(
            event, shell_join(plan.command), progress_message=progress_message
        )
        return collect_downloaded_files(plan.output_dir)
    return await download_remote_files(event, plan, download_dir, progress_message, custom_name)


async def upload_files_from_plan(
    event: NewMessage.Event | CallbackQuery.Event,
    plan: ExternalDownload | list[RemoteFile],
    download_dir: Path,
    progress_message: Message,
    custom_name: str = '',
) -> None:
    output_files = await collect_download_plan_files(
        event, plan, download_dir, progress_message, custom_name
    )
    if not output_files:
        await progress_message.edit(t('download_failed'))
        return

    await progress_message.edit(t('download_complete_starting_upload'))
    await upload_output_files(event, output_files, progress_message)
    uploaded = output_files[0].name if len(output_files) == 1 else str(len(output_files))
    await progress_message.edit(f'{t("file_uploaded")}: <code>{uploaded}</code>')


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
        await upload_files_from_plan(
            event,
            ExternalDownload(
                name='google-drive',
                command=(
                    str(gdl_path),
                    gdrive_input,
                    '-d',
                    str(download_dir),
                    '--skip-internet-check',
                ),
                output_dir=download_dir,
            ),
            download_dir,
            progress_message,
        )


async def upload_from_url_command(event: NewMessage.Event | CallbackQuery.Event) -> None:
    reply_message = await get_reply_message(event, previous=True)
    message = reply_message or (
        await event.get_message() if isinstance(event, CallbackQuery.Event) else event.message
    )
    if message is None:
        await event.answer(t('no_valid_url_found'), alert=True)
        return
    custom_name = ''
    url_match = re.search(HTTP_URL_PATTERN, message.raw_text)
    if url_match:
        url = url_match.group(0)
    else:
        if isinstance(event, CallbackQuery.Event):
            await event.answer(t('no_valid_url_found'), alert=True)
        else:
            await event.reply(t('no_valid_url_found'))
        return
    if custom := (message.raw_text or '').split('|', 1):
        custom_name = custom[1].strip() if len(custom) > 1 else ''
    progress_message = await send_progress_message(event, t('starting_file_download'))

    try:
        plan = await resolve_download_plan(url)
    except aiohttp.ClientError as e:
        await progress_message.edit(t('an_error_occurred', error=f'\n<pre>{e}</pre>'))
        return

    with TemporaryDirectory(dir=DOWNLOADS_DIR) as download_dir_name:
        download_dir = Path(download_dir_name)
        await upload_files_from_plan(
            event,
            plan or [RemoteFile(name=custom_name or get_filename_from_url(url), url=url)],
            download_dir,
            progress_message,
            custom_name,
        )


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
            condition=has_gdrive_download_input,
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
