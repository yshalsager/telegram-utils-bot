from typing import ClassVar
from urllib.parse import quote as url_quote

import aiohttp
import regex as re
from telethon import Button
from telethon.events import CallbackQuery, NewMessage

from src.modules.base import ModuleBase
from src.utils.command import Command
from src.utils.filters import has_valid_url
from src.utils.i18n import t
from src.utils.patterns import HTTP_URL_PATTERN
from src.utils.telegram import edit_or_send_as_file, get_reply_message, inline_buttons_grid


def _get_url(text: str) -> str | None:
    for line in text.splitlines():
        if line.startswith('Source:') and (match := re.search(HTTP_URL_PATTERN, line)):
            return match.group(0) if match else None
    match = re.search(HTTP_URL_PATTERN, text)
    return match.group(0) if match else None


def _extract_jina(text: str, url: str) -> tuple[str, str]:
    title = ''
    for line in text.splitlines():
        if line.startswith('Title:'):
            title = line.removeprefix('Title:').strip()
            break

    content = text
    marker = 'Markdown Content:'
    if marker in text:
        content = text.split(f'{marker}\n', 1)[1]

    return title or url, content.strip()


def _buttons(url: str) -> list[list[Button]]:
    encoded = url_quote(url, safe='')
    readability_url = f'https://readability-bot.vercel.app/api/readability?url={encoded}'
    inline_row = inline_buttons_grid(
        [
            ('Text', 'm|read|text'),
        ],
        cols=1,
    )[0]
    return [
        [
            Button.url('Readability', readability_url),
            *inline_row,
        ],
        [
            Button.url('Instant View', f'https://a.devs.today/{url}'),
            Button.url('Jina', f'https://r.jina.ai/{url}'),
        ],
    ]


async def read(event: NewMessage.Event | CallbackQuery.Event) -> None:
    show_text = (
        isinstance(event, CallbackQuery.Event) and event.data.decode('utf-8') == 'm|read|text'
    )

    if isinstance(event, CallbackQuery.Event):
        await event.answer()
        message = await event.get_message()
        url = _get_url(message.raw_text)
        if not url and message.is_reply:
            reply_message = await get_reply_message(event, previous=True)
            url = _get_url(reply_message.raw_text)
            if not url and reply_message.is_reply:
                url = _get_url((await reply_message.get_reply_message()).raw_text)
    else:
        url = _get_url(event.message.raw_text)
        if not url and event.message.is_reply:
            reply_message = await get_reply_message(event, previous=True)
            url = _get_url(reply_message.raw_text)

    if not url:
        await event.reply(t('no_valid_url_found'))
        return

    header = f'<b>Readability</b>\nSource: {url}'

    if isinstance(event, NewMessage.Event):
        await event.reply(header, buttons=_buttons(url))
        return

    message = await event.get_message()
    if not show_text:
        await event.client.send_message(event.chat_id, header, buttons=_buttons(url))
        await message.delete()
        return

    async with aiohttp.ClientSession() as session, session.get(f'https://r.jina.ai/{url}') as resp:
        raw = await resp.text()

    title, content = _extract_jina(raw, url)
    md_text = f'{title}\n\n{content}'
    safe_title = re.sub(r'[\\/:*?"<>|]+', '_', title)[:80] or 'readability'
    await edit_or_send_as_file(
        event,
        message,
        md_text,
        file_name=f'{safe_title}.md',
        parse_mode='md',
        caption=url,
    )


class Readability(ModuleBase):
    name = 'Readability'
    description = t('_readability_module_description')
    commands: ClassVar[ModuleBase.CommandsT] = {
        'read': Command(
            handler=read,
            description=t('_read_description'),
            pattern=re.compile(r'^/(read)(?:\s+.+)?$'),
            condition=lambda e, m: has_valid_url(e, m) or bool(e.message.is_reply),
            is_applicable_for_reply=True,
        ),
    }
