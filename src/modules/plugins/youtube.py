import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from os import getenv
from pathlib import Path
from time import sleep
from typing import Any, ClassVar, cast

import aiohttp
import orjson
import regex as re
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
from telethon.events import CallbackQuery, NewMessage

from src import STATE_DIR
from src.modules.base import ModuleBase
from src.utils.command import Command
from src.utils.downloads import download_to_temp_file, get_download_name
from src.utils.filters import has_file, is_admin_in_private
from src.utils.i18n import t
from src.utils.telegram import get_reply_message, send_progress_message

YOUTUBE_SCOPES = [
    'https://www.googleapis.com/auth/youtube.upload',
    'https://www.googleapis.com/auth/youtube.readonly',
]
YOUTUBE_SCOPE = ' '.join(YOUTUBE_SCOPES)
YOUTUBE_DEVICE_CODE_URL = 'https://oauth2.googleapis.com/device/code'
YOUTUBE_TOKEN_URL = 'https://oauth2.googleapis.com/token'  # noqa: S105
YOUTUBE_TOKEN_PATH = STATE_DIR / 'youtube_token.json'
YOUTUBE_AUTH_PATH = STATE_DIR / 'youtube_auth.json'
RETRIABLE_STATUS_CODES = {
    HTTPStatus.INTERNAL_SERVER_ERROR,
    HTTPStatus.BAD_GATEWAY,
    HTTPStatus.SERVICE_UNAVAILABLE,
    HTTPStatus.GATEWAY_TIMEOUT,
}
YOUTUBE_UPLOAD_PATTERN = re.compile(r'^/youtube\s+upload(?:\s+(.+))?$')
YOUTUBE_AUTH_PATTERN = re.compile(r'^/youtube\s+auth(?:\s+(check|reset|remove))?$')
YOUTUBE_STATUS_PATTERN = re.compile(r'^/youtube\s+status$')
YOUTUBE_PRIVACY_STATUSES = {'private', 'unlisted', 'public'}


def get_youtube_client_config() -> tuple[str, str] | None:
    client_id = getenv('YOUTUBE_CLIENT_ID')
    client_secret = getenv('YOUTUBE_CLIENT_SECRET')
    if client_id and client_secret:
        return client_id, client_secret

    client_secrets_file = getenv('YOUTUBE_CLIENT_SECRETS_FILE')
    if not client_secrets_file:
        return None

    payload = orjson.loads(Path(client_secrets_file).read_bytes())
    config = payload.get('installed') or payload.get('web')
    if not config:
        return None
    client_id = config.get('client_id')
    client_secret = config.get('client_secret')
    return (client_id, client_secret) if client_id and client_secret else None


def get_youtube_partner_config() -> dict[str, str]:
    content_owner = getenv('YOUTUBE_CONTENT_OWNER_ID')
    channel = getenv('YOUTUBE_CONTENT_OWNER_CHANNEL_ID')
    if not content_owner or not channel:
        return {}
    return {
        'onBehalfOfContentOwner': content_owner,
        'onBehalfOfContentOwnerChannel': channel,
    }


def save_youtube_credentials(credentials: Credentials) -> None:
    YOUTUBE_TOKEN_PATH.write_text(credentials.to_json(strip=['client_secret']))


def parse_credentials_expiry(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(UTC).replace(tzinfo=None)


def load_youtube_credentials() -> Credentials | None:
    client_config = get_youtube_client_config()
    if not client_config or not YOUTUBE_TOKEN_PATH.exists():
        return None

    client_id, client_secret = client_config
    payload = orjson.loads(YOUTUBE_TOKEN_PATH.read_text())
    credentials = Credentials(
        token=payload.get('token'),
        refresh_token=payload.get('refresh_token'),
        token_uri=payload.get('token_uri') or YOUTUBE_TOKEN_URL,
        client_id=client_id,
        client_secret=client_secret,
        scopes=payload.get('scopes') or YOUTUBE_SCOPES,
        expiry=parse_credentials_expiry(payload.get('expiry')),
    )
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
        save_youtube_credentials(credentials)
    return credentials if credentials.valid else None


def parse_youtube_upload_args(text: str) -> dict[str, Any]:
    parts = [part.strip() for part in text.split('|')]
    privacy_status = 'private'
    if parts and parts[0].lower() in YOUTUBE_PRIVACY_STATUSES:
        privacy_status = parts.pop(0).lower()

    title = parts[0] if parts else ''
    description = parts[1] if len(parts) > 1 else ''
    tags = [tag.strip() for tag in parts[2].split(',') if tag.strip()] if len(parts) > 2 else []
    return {
        'privacy_status': privacy_status,
        'title': title,
        'description': description,
        'tags': tags,
    }


def build_youtube_resource(
    *,
    title: str,
    description: str,
    tags: list[str],
    privacy_status: str,
) -> dict[str, Any]:
    return {
        'snippet': {
            'title': title,
            'description': description,
            'tags': tags,
            'categoryId': '22',
        },
        'status': {
            'privacyStatus': privacy_status,
            'selfDeclaredMadeForKids': False,
        },
    }


def upload_video(
    credentials: Credentials,
    video_path: Path,
    *,
    title: str,
    description: str,
    tags: list[str],
    privacy_status: str,
    on_progress: Callable[[int], None] | None = None,
) -> str:
    youtube = build('youtube', 'v3', credentials=credentials)
    request_args: dict[str, Any] = {
        'part': 'snippet,status',
        'body': cast(
            Any,
            build_youtube_resource(
                title=title,
                description=description,
                tags=tags,
                privacy_status=privacy_status,
            ),
        ),
        'media_body': MediaFileUpload(str(video_path), chunksize=8 * 1024 * 1024, resumable=True),
    }
    request_args.update(get_youtube_partner_config())
    request = youtube.videos().insert(**request_args)

    response = None
    retry = 0
    while response is None:
        try:
            status, response = request.next_chunk()
            if status and on_progress:
                on_progress(round(status.progress() * 100))
        except HttpError as e:
            if e.resp.status not in RETRIABLE_STATUS_CODES or retry >= 5:
                raise
            sleep(2**retry)
            retry += 1

    video_id = response.get('id')
    if not video_id:
        raise RuntimeError('YouTube upload response did not include a video id')
    return str(video_id)


def get_authenticated_channel(credentials: Credentials) -> str | None:
    youtube = build('youtube', 'v3', credentials=credentials)
    response = youtube.channels().list(part='snippet', mine=True).execute()
    items = response.get('items') or []
    if not items:
        return None
    channel = items[0]
    title = channel.get('snippet', {}).get('title') or '-'
    return f'{title} ({channel.get("id", "-")})'


async def reply_text(event: NewMessage.Event | CallbackQuery.Event, text: str) -> None:
    if isinstance(event, CallbackQuery.Event):
        await event.answer(text, alert=True)
    else:
        await event.reply(text)


async def youtube_auth(event: NewMessage.Event | CallbackQuery.Event) -> None:
    if not isinstance(event, NewMessage.Event):
        await event.answer(t('use_direct_command'), alert=True)
        return

    match = YOUTUBE_AUTH_PATTERN.match(event.message.raw_text or '')
    action = match.group(1) if match else None
    if action in ('reset', 'remove'):
        YOUTUBE_AUTH_PATH.unlink(missing_ok=True)
        YOUTUBE_TOKEN_PATH.unlink(missing_ok=True)
        await event.reply(t('youtube_auth_reset'))
        return
    if action == 'check':
        await youtube_auth_check(event)
        return

    client_config = get_youtube_client_config()
    if not client_config:
        await event.reply(t('youtube_missing_client_config'))
        return

    client_id, _ = client_config
    async with (
        aiohttp.ClientSession() as session,
        session.post(
            YOUTUBE_DEVICE_CODE_URL,
            data={'client_id': client_id, 'scope': YOUTUBE_SCOPE},
        ) as response,
    ):
        response.raise_for_status()
        payload = await response.json()

    YOUTUBE_AUTH_PATH.write_bytes(orjson.dumps(payload))
    await event.reply(
        t(
            'youtube_auth_started',
            url=payload['verification_url'],
            code=payload['user_code'],
            expires_in=payload['expires_in'],
        )
    )


async def youtube_auth_check(event: NewMessage.Event | CallbackQuery.Event) -> None:
    client_config = get_youtube_client_config()
    if not client_config:
        await reply_text(event, t('youtube_missing_client_config'))
        return
    if not YOUTUBE_AUTH_PATH.exists():
        await reply_text(event, t('youtube_auth_not_started'))
        return

    client_id, client_secret = client_config
    payload = orjson.loads(YOUTUBE_AUTH_PATH.read_text())
    async with (
        aiohttp.ClientSession() as session,
        session.post(
            YOUTUBE_TOKEN_URL,
            data={
                'client_id': client_id,
                'client_secret': client_secret,
                'device_code': payload['device_code'],
                'grant_type': 'urn:ietf:params:oauth:grant-type:device_code',
            },
        ) as response,
    ):
        token_payload = await response.json()
        if response.status != HTTPStatus.OK:
            error = token_payload.get('error')
            if error in ('authorization_pending', 'slow_down'):
                await reply_text(event, t('youtube_auth_pending'))
                return
            await reply_text(event, t('youtube_auth_failed', error=error or response.status))
            return

    credentials = Credentials(
        token=token_payload.get('access_token'),
        refresh_token=token_payload.get('refresh_token'),
        token_uri=YOUTUBE_TOKEN_URL,
        client_id=client_id,
        client_secret=client_secret,
        scopes=YOUTUBE_SCOPES,
        expiry=(
            datetime.now(UTC) + timedelta(seconds=token_payload.get('expires_in', 3600))
        ).replace(tzinfo=None),
    )
    save_youtube_credentials(credentials)
    YOUTUBE_AUTH_PATH.unlink(missing_ok=True)
    await reply_text(event, t('youtube_auth_completed'))


async def youtube_status(event: NewMessage.Event | CallbackQuery.Event) -> None:
    credentials = load_youtube_credentials()
    if not credentials:
        if YOUTUBE_AUTH_PATH.exists():
            await reply_text(event, t('youtube_auth_not_checked'))
            return
        await reply_text(event, t('youtube_not_authenticated'))
        return
    try:
        channel = await asyncio.to_thread(get_authenticated_channel, credentials)
    except HttpError:
        channel = None
    await reply_text(
        event,
        t('youtube_authenticated', channel=channel or t('youtube_channel_unknown')),
    )


async def youtube_upload(event: NewMessage.Event | CallbackQuery.Event) -> None:
    credentials = load_youtube_credentials()
    if not credentials:
        await reply_text(event, t('youtube_not_authenticated'))
        return

    reply_message = await get_reply_message(event, previous=True)
    if not reply_message or not reply_message.file:
        await reply_text(event, t('unsupported_file_type'))
        return

    input_text = ''
    if isinstance(event, NewMessage.Event):
        match = YOUTUBE_UPLOAD_PATTERN.match(event.message.raw_text or '')
        input_text = (match.group(1) if match else '') or ''

    args = parse_youtube_upload_args(input_text)
    title = args['title'] or get_download_name(reply_message).stem
    progress_message = await send_progress_message(event, t('downloading'))

    async with download_to_temp_file(
        event,
        reply_message,
        progress_message,
        suffix=reply_message.file.ext,
    ) as video_path:
        await progress_message.edit(t('youtube_uploading'))
        video_id = await asyncio.to_thread(
            upload_video,
            credentials,
            video_path,
            title=title,
            description=args['description'],
            tags=args['tags'],
            privacy_status=args['privacy_status'],
        )

    await progress_message.edit(t('youtube_upload_completed', url=f'https://youtu.be/{video_id}'))


class YouTube(ModuleBase):
    name = 'YouTube'
    description = t('_youtube_module_description')
    commands: ClassVar[ModuleBase.CommandsT] = {
        'youtube auth': Command(
            handler=youtube_auth,
            description=t('_youtube_auth_description'),
            pattern=YOUTUBE_AUTH_PATTERN,
            condition=is_admin_in_private,
        ),
        'youtube status': Command(
            handler=youtube_status,
            description=t('_youtube_status_description'),
            pattern=YOUTUBE_STATUS_PATTERN,
            condition=is_admin_in_private,
        ),
        'youtube upload': Command(
            handler=youtube_upload,
            description=t('_youtube_upload_description'),
            pattern=YOUTUBE_UPLOAD_PATTERN,
            condition=lambda e, m: is_admin_in_private(e, m) and has_file(e, m),
            is_applicable_for_reply=True,
        ),
    }
