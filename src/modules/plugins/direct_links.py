from html import escape
from typing import ClassVar

import aiohttp
import regex as re
from telethon.events import CallbackQuery, NewMessage
from telethon.tl.custom import Message

from src.modules.base import ModuleBase
from src.utils.command import Command
from src.utils.filters import has_valid_url, is_admin_in_private
from src.utils.i18n import t
from src.utils.patterns import extract_urls
from src.utils.remote_files.models import ExternalDownload
from src.utils.remote_files.resolver import is_supported_remote_url, resolve_download_plan
from src.utils.telegram import edit_or_send_as_file, get_reply_message, send_progress_message

DIRECT_LINK_PATTERN = re.compile(r'^/direct(?:\s+(.+))?$')


def extract_direct_command_input(text: str) -> str:
    match = DIRECT_LINK_PATTERN.match(text)
    return (match.group(1) if match else '') or ''


def has_direct_link_input(event: NewMessage.Event, message: Message | None) -> bool:
    if not is_admin_in_private(event, message or event.message):
        return False

    text = (message or event.message).raw_text or ''
    if not (has_valid_url(event, message) or event.message.is_reply):
        return False

    if (event.message.raw_text or '').startswith('/direct'):
        return True
    return any(is_supported_remote_url(url) for url in extract_urls(text))


async def get_direct_input_text(event: NewMessage.Event | CallbackQuery.Event) -> str:
    if isinstance(event, CallbackQuery.Event):
        reply_message = await get_reply_message(event, previous=True)
        return reply_message.raw_text if reply_message else ''

    input_text = extract_direct_command_input(event.message.raw_text or '')
    if not input_text and event.message.is_reply:
        reply_message = await get_reply_message(event, previous=True)
        input_text = reply_message.raw_text if reply_message else ''
    return input_text


async def direct_links(event: NewMessage.Event | CallbackQuery.Event) -> None:
    input_text = await get_direct_input_text(event)

    urls = extract_urls(input_text)
    if not urls:
        if isinstance(event, CallbackQuery.Event):
            await event.answer(t('no_valid_url_found'), alert=True)
        else:
            await event.reply(t('no_valid_url_found'))
        return

    progress_message = await send_progress_message(event, t('fetching_information'))
    lines = [f'<b>{t("direct_links_header")}</b>']
    for url in urls:
        try:
            plan = await resolve_download_plan(url)
        except aiohttp.ClientError as e:
            lines.append(t('direct_link_failed', url=escape(url), error=escape(f'{e}')))
            continue

        if isinstance(plan, ExternalDownload):
            lines.append(t('direct_link_unsupported', url=escape(url)))
            continue

        if not plan:
            lines.append(t('direct_link_unsupported', url=escape(url)))
            continue

        lines.extend(link.to_html() for link in plan)

    await edit_or_send_as_file(
        event,
        progress_message,
        '\n'.join(lines),
        file_name='direct-links.html',
        parse_mode='html',
    )


class DirectLinks(ModuleBase):
    name = 'DirectLinks'
    description = t('_direct_module_description')
    commands: ClassVar[ModuleBase.CommandsT] = {
        'direct': Command(
            handler=direct_links,
            description=t('_direct_description'),
            pattern=DIRECT_LINK_PATTERN,
            condition=has_direct_link_input,
            is_applicable_for_reply=True,
        ),
    }
