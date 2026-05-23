import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from html import escape
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
from telethon import Button
from telethon.events import CallbackQuery, NewMessage
from telethon.tl.custom import Message

from src import STATE_DIR
from src.modules.base import ModuleBase
from src.utils.command import Command
from src.utils.cryptography import (
    decrypt_state_secret,
    encrypt_state_secret,
    has_state_encryption_key,
)
from src.utils.downloads import download_to_temp_file, get_download_name
from src.utils.filters import has_media
from src.utils.i18n import t
from src.utils.telegram import (
    buttons_grid,
    get_reply_message,
    safe_event_edit,
    send_progress_message,
)

YOUTUBE_SCOPES = [
    'https://www.googleapis.com/auth/youtube.upload',
    'https://www.googleapis.com/auth/youtube.readonly',
]
YOUTUBE_SCOPE = ' '.join(YOUTUBE_SCOPES)
YOUTUBE_DEVICE_CODE_URL = 'https://oauth2.googleapis.com/device/code'
YOUTUBE_TOKEN_URL = 'https://oauth2.googleapis.com/token'  # noqa: S105
YOUTUBE_DIR = STATE_DIR / 'youtube'
YOUTUBE_USERS_DIR = YOUTUBE_DIR / 'users'
RETRIABLE_STATUS_CODES = {
    HTTPStatus.INTERNAL_SERVER_ERROR,
    HTTPStatus.BAD_GATEWAY,
    HTTPStatus.SERVICE_UNAVAILABLE,
    HTTPStatus.GATEWAY_TIMEOUT,
}
YOUTUBE_PATTERN = re.compile(r'^/youtube(?:\s+(auth|channels|status|upload))?(?:\s+(.+))?$')
YOUTUBE_PRIVACY_STATUSES = {'private', 'unlisted', 'public'}
YOUTUBE_PENDING_AUTH = 'pending'


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


def normalize_alias(alias: str) -> str:
    return alias.strip().lower()


def slugify_alias(text: str, fallback: str) -> str:
    alias = re.sub(r'[^a-z0-9_-]+', '-', text.lower()).strip('-_')
    return (alias or fallback.lower())[:32].strip('-_')


def generate_channel_alias(channels: dict[str, dict[str, Any]], title: str, channel_id: str) -> str:
    for alias, channel in channels.items():
        if channel.get('channel_id') == channel_id:
            return alias

    base = slugify_alias(title, channel_id)
    alias = base
    counter = 2
    while alias in channels:
        suffix = f'-{counter}'
        alias = f'{base[: 32 - len(suffix)]}{suffix}'
        counter += 1
    return alias


def youtube_user_dir(user_id: int) -> Path:
    return YOUTUBE_USERS_DIR / str(user_id)


def youtube_channels_path(user_id: int) -> Path:
    return youtube_user_dir(user_id) / 'channels.json'


def youtube_token_path(user_id: int, alias: str) -> Path:
    return youtube_user_dir(user_id) / 'tokens' / f'{normalize_alias(alias)}.json'


def youtube_auth_path(user_id: int, alias: str) -> Path:
    return youtube_user_dir(user_id) / 'auth' / f'{normalize_alias(alias)}.json'


def youtube_pending_auth_path(user_id: int) -> Path:
    return youtube_auth_path(user_id, YOUTUBE_PENDING_AUTH)


def load_youtube_channels(user_id: int) -> dict[str, dict[str, Any]]:
    path = youtube_channels_path(user_id)
    if not path.exists():
        return {}
    return cast(dict[str, dict[str, Any]], orjson.loads(path.read_text()))


def save_youtube_channels(user_id: int, channels: dict[str, dict[str, Any]]) -> None:
    path = youtube_channels_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(orjson.dumps(channels, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS))


def remove_youtube_channel(user_id: int, alias: str) -> bool:
    alias = normalize_alias(alias)
    channels = load_youtube_channels(user_id)
    existed = (
        alias in channels
        or youtube_token_path(user_id, alias).exists()
        or youtube_auth_path(user_id, alias).exists()
    )
    channels.pop(alias, None)
    youtube_token_path(user_id, alias).unlink(missing_ok=True)
    youtube_auth_path(user_id, alias).unlink(missing_ok=True)
    save_youtube_channels(user_id, channels)
    return existed


def save_youtube_credentials(credentials: Credentials, token_path: Path) -> None:
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(encrypt_state_secret(credentials.to_json(strip=['client_secret'])))


def parse_credentials_expiry(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(UTC).replace(tzinfo=None)


def load_youtube_credentials(user_id: int, alias: str) -> Credentials | None:
    client_config = get_youtube_client_config()
    token_path = youtube_token_path(user_id, alias)
    if not client_config or not token_path.exists():
        return None

    client_id, client_secret = client_config
    payload = orjson.loads(decrypt_state_secret(token_path.read_text()))
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
        save_youtube_credentials(credentials, token_path)
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


def get_authenticated_channel(credentials: Credentials) -> dict[str, str] | None:
    youtube = build('youtube', 'v3', credentials=credentials)
    response = youtube.channels().list(part='snippet', mine=True).execute()
    items = response.get('items') or []
    if not items:
        return None
    channel = items[0]
    return {
        'id': str(channel.get('id', '-')),
        'title': str(channel.get('snippet', {}).get('title') or '-'),
    }


def get_event_user_id(event: NewMessage.Event | CallbackQuery.Event) -> int:
    return int(event.sender_id or event.chat_id)


async def reply_text(event: NewMessage.Event | CallbackQuery.Event, text: str) -> None:
    if isinstance(event, CallbackQuery.Event):
        await event.answer(text, alert=True)
    else:
        await event.reply(text, parse_mode='html')


async def has_youtube_state_encryption(event: NewMessage.Event | CallbackQuery.Event) -> bool:
    if has_state_encryption_key():
        return True
    await reply_text(event, t('youtube_missing_state_encryption_key'))
    return False


async def edit_or_reply(
    event: NewMessage.Event | CallbackQuery.Event,
    text: str,
    *,
    buttons: list[list[Button]] | None = None,
) -> None:
    if isinstance(event, CallbackQuery.Event):
        await safe_event_edit(event, text, buttons=buttons, parse_mode='html')
    else:
        await event.reply(text, buttons=buttons, parse_mode='html')


def youtube_channel_buttons(
    user_id: int, *, prefix: str, media_message_id: int | None = None
) -> list[list[Button]]:
    buttons = []
    for alias, channel in sorted(load_youtube_channels(user_id).items()):
        title = channel.get('title') or alias
        data = f'm|youtube|{prefix}|{alias}'
        if media_message_id is not None:
            data = f'{data}|{media_message_id}'
        buttons.append(Button.inline(title, data))
    return buttons_grid(buttons, cols=1)


async def show_youtube_panel(event: NewMessage.Event | CallbackQuery.Event) -> None:
    user_id = get_event_user_id(event)
    channels = load_youtube_channels(user_id)
    buttons = [
        [Button.inline(t('_youtube_auth'), 'm|youtube|auth')],
        [Button.inline(t('youtube_channels'), 'm|youtube|channels')],
    ]
    text = t('youtube_panel', channel_count=len(channels))
    await edit_or_reply(event, text, buttons=buttons)


async def show_youtube_auth_panel(event: NewMessage.Event | CallbackQuery.Event) -> None:
    user_id = get_event_user_id(event)
    buttons: list[list[Button]] = [[Button.inline(t('youtube_add_channel'), 'm|youtube|auth_add')]]
    if youtube_pending_auth_path(user_id).exists():
        buttons.append([Button.inline(t('youtube_check_auth'), 'm|youtube|auth_check')])
    channels = load_youtube_channels(user_id)
    for alias, channel in sorted(channels.items()):
        title = channel.get('title') or alias
        buttons.extend(
            [
                [Button.inline(f'{title} ({alias})', f'm|youtube|status|{alias}')],
                [Button.inline(t('youtube_remove'), f'm|youtube|auth_remove|{alias}')],
            ]
        )
    await edit_or_reply(event, t('youtube_auth_panel'), buttons=buttons)


async def show_youtube_channels(event: NewMessage.Event | CallbackQuery.Event) -> None:
    user_id = get_event_user_id(event)
    channels = load_youtube_channels(user_id)
    if not channels:
        await edit_or_reply(
            event,
            t('youtube_no_channels'),
            buttons=[[Button.inline(t('youtube_add_channel'), 'm|youtube|auth_add')]],
        )
        return

    lines = [t('youtube_channels_header')]
    for alias, channel in sorted(channels.items()):
        title = channel.get('title') or alias
        channel_id = channel.get('channel_id', '-')
        lines.append(f'- <a href="https://youtube.com/channel/{channel_id}">{escape(title)}</a>')
    await edit_or_reply(
        event, '\n'.join(lines), buttons=youtube_channel_buttons(user_id, prefix='status')
    )


async def start_youtube_auth(event: NewMessage.Event | CallbackQuery.Event) -> None:
    user_id = get_event_user_id(event)

    client_config = get_youtube_client_config()
    if not client_config:
        await reply_text(event, t('youtube_missing_client_config'))
        return
    if not await has_youtube_state_encryption(event):
        return

    client_id, _client_secret = client_config
    async with (
        aiohttp.ClientSession() as session,
        session.post(
            YOUTUBE_DEVICE_CODE_URL,
            data={'client_id': client_id, 'scope': YOUTUBE_SCOPE},
        ) as response,
    ):
        response.raise_for_status()
        payload = await response.json()

    auth_path = youtube_pending_auth_path(user_id)
    auth_path.parent.mkdir(parents=True, exist_ok=True)
    auth_path.write_text(encrypt_state_secret(orjson.dumps(payload).decode()))
    await edit_or_reply(
        event,
        t(
            'youtube_auth_started',
            url=payload['verification_url'],
            code=payload['user_code'],
            expires_in=payload['expires_in'],
        ),
        buttons=[[Button.inline(t('youtube_check_auth'), 'm|youtube|auth_check')]],
    )


async def check_youtube_auth(event: NewMessage.Event | CallbackQuery.Event) -> None:
    user_id = get_event_user_id(event)
    client_config = get_youtube_client_config()
    if not client_config:
        await reply_text(event, t('youtube_missing_client_config'))
        return
    if not await has_youtube_state_encryption(event):
        return

    auth_path = youtube_pending_auth_path(user_id)
    if not auth_path.exists():
        await reply_text(event, t('youtube_auth_not_started'))
        return

    client_id, client_secret = client_config
    payload = orjson.loads(decrypt_state_secret(auth_path.read_text()))
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
    channel = await asyncio.to_thread(get_authenticated_channel, credentials)
    if not channel:
        await reply_text(event, t('youtube_channel_unknown'))
        return

    channels = load_youtube_channels(user_id)
    alias = generate_channel_alias(channels, channel['title'], channel['id'])
    save_youtube_credentials(credentials, youtube_token_path(user_id, alias))
    auth_path.unlink(missing_ok=True)
    channels[alias] = {
        'title': channel['title'],
        'channel_id': channel['id'],
    }
    save_youtube_channels(user_id, channels)
    await edit_or_reply(
        event,
        t(
            'youtube_auth_completed_for_alias',
            alias=alias,
            channel=f'{channel["title"]} ({channel["id"]})',
        ),
    )


async def show_youtube_status(
    event: NewMessage.Event | CallbackQuery.Event, alias: str | None = None
) -> None:
    user_id = get_event_user_id(event)
    channels = load_youtube_channels(user_id)
    if not alias:
        if not channels:
            await edit_or_reply(event, t('youtube_no_channels'))
            return
        if len(channels) > 1:
            await edit_or_reply(
                event,
                t('youtube_channels_header'),
                buttons=youtube_channel_buttons(user_id, prefix='status'),
            )
            return
        alias = next(iter(channels))
    alias = normalize_alias(alias)
    if not alias:
        await edit_or_reply(event, t('youtube_no_channels'))
        return

    if alias not in channels:
        await edit_or_reply(event, t('youtube_channel_not_found', alias=alias))
        return
    if not await has_youtube_state_encryption(event):
        return

    credentials = load_youtube_credentials(user_id, alias)
    if not credentials:
        await edit_or_reply(event, t('youtube_not_authenticated'))
        return

    try:
        channel = await asyncio.to_thread(get_authenticated_channel, credentials)
    except HttpError:
        channel = None
    configured = channels[alias]
    await edit_or_reply(
        event,
        t(
            'youtube_status',
            alias=alias,
            channel=(
                f'{channel["title"]} ({channel["id"]})' if channel else t('youtube_channel_unknown')
            ),
            saved=f'{configured.get("title", "-")} ({configured.get("channel_id", "-")})',
        ),
    )


async def show_youtube_upload_channels(
    event: NewMessage.Event | CallbackQuery.Event, reply_message: Message, input_text: str
) -> None:
    user_id = get_event_user_id(event)
    channels = load_youtube_channels(user_id)
    if not channels:
        await edit_or_reply(
            event,
            t('youtube_no_channels'),
            buttons=[[Button.inline(t('youtube_add_channel'), 'm|youtube|auth_add')]],
        )
        return
    if len(channels) == 1:
        await upload_to_youtube_alias(event, next(iter(channels)), reply_message, input_text)
        return
    buttons = youtube_channel_buttons(user_id, prefix='upload', media_message_id=reply_message.id)
    await edit_or_reply(event, t('youtube_choose_upload_channel'), buttons=buttons)


async def upload_to_youtube_alias(
    event: NewMessage.Event | CallbackQuery.Event,
    alias: str,
    reply_message: Message,
    input_text: str = '',
) -> None:
    user_id = get_event_user_id(event)
    if not await has_youtube_state_encryption(event):
        return

    credentials = load_youtube_credentials(user_id, alias)
    if not credentials:
        await reply_text(event, t('youtube_not_authenticated'))
        return

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


async def handle_youtube_callback(event: CallbackQuery.Event) -> None:
    parts = event.data.decode('utf-8').split('|')
    action = parts[2] if len(parts) > 2 else 'panel'
    alias = parts[3] if len(parts) > 3 else None
    simple_actions = {
        'panel': show_youtube_panel,
        'auth': show_youtube_auth_panel,
        'auth_add': start_youtube_auth,
        'auth_check': check_youtube_auth,
        'channels': show_youtube_channels,
    }
    if action in simple_actions:
        await simple_actions[action](event)
    elif alias:
        await handle_youtube_alias_callback(event, action, alias, parts)


async def handle_youtube_alias_callback(
    event: CallbackQuery.Event, action: str, alias: str, parts: list[str]
) -> None:
    if action == 'auth_remove':
        removed = remove_youtube_channel(get_event_user_id(event), alias)
        key = 'youtube_channel_removed' if removed else 'youtube_channel_not_found'
        await edit_or_reply(event, t(key, alias=alias))
    elif action == 'status':
        await show_youtube_status(event, alias)
    elif action == 'upload' and len(parts) > 4:
        media_message = await event.client.get_messages(event.chat_id, ids=int(parts[4]))
        command_message = await (await event.get_message()).get_reply_message()
        match = YOUTUBE_PATTERN.match(command_message.raw_text or '') if command_message else None
        await upload_to_youtube_alias(
            event, alias, media_message, (match.group(2) if match else '') or ''
        )


async def handle_youtube_message(event: NewMessage.Event) -> None:
    match = YOUTUBE_PATTERN.match(event.message.raw_text or '')
    action = match.group(1) if match else None
    input_text = (match.group(2) if match else '') or ''
    if action == 'auth':
        await show_youtube_auth_panel(event)
    elif action == 'channels':
        await show_youtube_channels(event)
    elif action == 'status':
        await show_youtube_status(event, input_text or None)
    elif action == 'upload':
        reply_message = await get_reply_message(event, previous=True)
        if not reply_message or not reply_message.file:
            await event.reply(t('unsupported_file_type'))
            return
        await show_youtube_upload_channels(event, reply_message, input_text)
    else:
        await show_youtube_panel(event)


async def youtube_upload_entrypoint(event: NewMessage.Event | CallbackQuery.Event) -> None:
    input_text = ''
    if isinstance(event, NewMessage.Event):
        match = YOUTUBE_PATTERN.match(event.message.raw_text or '')
        input_text = (match.group(2) if match else '') or ''
    reply_message = await get_reply_message(event, previous=True)
    if not reply_message or not reply_message.file:
        await reply_text(event, t('unsupported_file_type'))
        return
    await show_youtube_upload_channels(event, reply_message, input_text)


async def youtube_entrypoint(event: NewMessage.Event | CallbackQuery.Event) -> None:
    if isinstance(event, CallbackQuery.Event):
        await handle_youtube_callback(event)
    else:
        await handle_youtube_message(event)


class YouTube(ModuleBase):
    name = 'YouTube'
    description = t('_youtube_module_description')
    commands: ClassVar[ModuleBase.CommandsT] = {
        'youtube': Command(
            handler=youtube_entrypoint,
            description=t('_youtube_description'),
            pattern=YOUTUBE_PATTERN,
            condition=lambda e, _: bool(e.is_private),
            is_applicable_for_reply=False,
        ),
        'youtube upload': Command(
            handler=youtube_upload_entrypoint,
            description=t('_youtube_upload_description'),
            pattern=YOUTUBE_PATTERN,
            condition=lambda e, m: bool(e.is_private) and has_media(e, m, video=True),
            is_applicable_for_reply=True,
        ),
    }
