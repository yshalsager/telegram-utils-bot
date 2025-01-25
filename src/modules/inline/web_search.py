import logging
from contextlib import suppress
from os import getenv
from typing import ClassVar
from urllib import parse

import regex as re
import wikipedia
from search_engine_parser.core.base import SearchResult
from search_engine_parser.core.engines.duckduckgo import Search as DuckDuckGoSearch
from telethon import Button, events
from telethon.errors import QueryIdInvalidError

from src.modules.base import InlineModuleBase
from src.utils.command import InlineCommand
from src.utils.http import fetch_json
from src.utils.i18n import t
from src.utils.quran import surah_names

ddg_search = DuckDuckGoSearch()


async def list_all_inline_commands(event: events.InlineQuery.Event) -> None:
    buttons = []
    for cmd, command_obj in list(WebSearch.inline_commands.items())[1:]:  # Skip the first item
        button_text = f'{cmd}: {command_obj.name}'
        switch_inline_query = f'{cmd} '
        buttons.append(Button.switch_inline(button_text, switch_inline_query, same_peer=True))

    # Create a grid of buttons, 2 buttons per row
    button_grid = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]

    result = await event.builder.article(
        title=t('available_inline_commands'),
        description=t('click_a_button_to_start_using_a_command'),
        text=f'{t("available_web_search_commands")}:',
        buttons=button_grid,
    )

    with suppress(QueryIdInvalidError):
        await event.answer([result])


async def handle_duckduckgo_search(event: events.InlineQuery.Event) -> None:
    query = event.text[4:].strip()
    if not query:
        return

    try:
        results: SearchResult = await ddg_search.async_search(query, 1)
    except Exception as e:  # noqa: BLE001
        logging.error(f'{t("error_in_duckduckgo_search")}: {e}')
        return

    inline_results = []
    for result in results:
        title = result.get('titles', 'No title')
        link = result.get('links', '')
        if not link:
            continue
        if link.startswith('//'):
            link = f'https:{link}'
        elif not link.startswith('http:'):
            link = f'https://{link}'
        description = result.get('descriptions', 'No description')

        inline_results.append(
            await event.builder.article(
                title=title,
                description=description,
                text=f'<b>{title}</b>\n\n{description}\n\n{link}',
            )
        )

    with suppress(QueryIdInvalidError):
        await event.answer(inline_results)


async def handle_wikipedia_search(event: events.InlineQuery.Event) -> None:
    lang, query = event.text[5:].strip().split(maxsplit=1)

    wikipedia.set_lang(lang)
    try:
        pages = wikipedia.search(query, 5)
    except Exception as e:  # noqa: BLE001
        logging.error(f'{t("error_in_wikipedia_search")}: {e}')
        return
    if not pages:
        return

    inline_results = []
    for title in pages:
        try:
            summary = wikipedia.summary(title, sentences=3)
        except wikipedia.exceptions.PageError:
            continue
        url = f'https://{lang}.wikipedia.org/wiki/{parse.quote(title)}'
        inline_results.append(
            await event.builder.article(
                title=title,
                description=summary,
                text=f'<b>{title}</b>\n\n{summary}\n\n{url}',
                parse_mode='html',
            )
        )

    with suppress(QueryIdInvalidError):
        await event.answer(inline_results)


async def handle_quran_search(event: events.InlineQuery.Event) -> None:
    query = event.text[6:].strip()  # Remove 'quran ' from the beginning
    data = await fetch_json('https://api.quran.com/api/v4/search', params={'q': query})
    if not data:
        return

    results = data['search']['results']
    inline_results = []
    for result in results:
        surah, aya = map(int, result['verse_key'].split(':'))
        title = f'سورة {surah_names[surah - 1]} ({aya})'
        text = result['text']
        inline_results.append(
            await event.builder.article(
                title=title,
                description=text,
                text=f'<b>{title}</b>\n\n﴿{text}﴾',
            )
        )

    with suppress(QueryIdInvalidError):
        await event.answer(inline_results)


async def handle_hadith_search(event: events.InlineQuery.Event) -> None:
    endpoint = getenv('HADITH_SEARCH_ENDPOINT')
    if not endpoint:
        return
    query = event.text[7:].strip()  # Remove 'hadith ' from the beginning
    if not query:
        return
    data = await fetch_json(endpoint.format(query=query))
    if not data:
        return
    results = data.get('data', [])
    if not results:
        return

    inline_results = []
    for result in results:
        text = result.get('text', '')
        rawy = result.get('rawy', '')
        muhaddith = result.get('muhaddith', '')
        source = result.get('source', '')
        source_location = result.get('source_location', '')
        hukm = result.get('hukm', '')

        title = f'{muhaddith} - {source} ({source_location})'
        description = f'{hukm} | {rawy}'
        content = f'<b>{title}</b>\n<i>{description}</i>\n\n{text}'

        inline_results.append(
            await event.builder.article(
                title=title,
                description=f'{description} | {text[:100]}',
                text=content[:4093] + '…',
                parse_mode='html',
            )
        )

    with suppress(QueryIdInvalidError):
        await event.answer(inline_results)


async def handle_exchange(event: events.InlineQuery.Event) -> None:
    api_key = getenv('EXCHANGE_RATE_API_KEY')
    if not api_key:
        return

    query = event.text[9:].strip()  # Remove 'exchange ' from the beginning
    try:
        amount, from_currency, to_currency = query.split()
        amount = float(amount)
    except ValueError:
        return

    url = (
        f'https://v6.exchangerate-api.com/v6/{api_key}/pair/{from_currency}/{to_currency}/{amount}'
    )

    data = await fetch_json(url)
    if not data or 'result' not in data or data['result'] != 'success':
        return

    conversion_rate = data['conversion_rate']
    conversion_result = data['conversion_result']
    title = f'{amount} {from_currency} to {to_currency}'
    description = f'{conversion_result:.2f} {to_currency}'
    content = (
        f'<b>{amount} {from_currency} = {conversion_result:.2f} {to_currency}</b>\n\n'
        f'{t("exchange_rate")}: 1 {from_currency} = {conversion_rate:.4f} {to_currency}\n'
        f'{t("last_updated")}: {data["time_last_update_utc"]}\n'
    )

    inline_result = await event.builder.article(
        title=title,
        description=description,
        text=content,
    )

    with suppress(QueryIdInvalidError):
        await event.answer([inline_result])


class WebSearch(InlineModuleBase):
    name = 'Web Search'
    description = 'Search the web using search engines'
    inline_commands: ClassVar[InlineModuleBase.InlineCommandsT] = {
        'commands': InlineCommand(
            pattern=re.compile(r'^(commands|help)$'),
            handler=list_all_inline_commands,
            name=t('list_commands'),
        ),
        'ddg': InlineCommand(
            pattern=re.compile(r'^ddg\s+(.+)$'),
            handler=handle_duckduckgo_search,
            name=t('duckduckgo_search'),
        ),
        'exchange': InlineCommand(
            pattern=re.compile(r'^exchange\s+(\d+(?:\.\d+)?)\s+([A-Z]{3})\s+([A-Z]{3})$'),
            handler=handle_exchange,
            name=t('currency_exchange'),
        ),
        'hadith': InlineCommand(
            pattern=re.compile(r'^hadith\s+(.+)$'),
            handler=handle_hadith_search,
            name=t('hadith_search'),
        ),
        'quran': InlineCommand(
            pattern=re.compile(r'^quran\s+(.+)$'),
            handler=handle_quran_search,
            name=t('quran_search'),
        ),
        'wiki': InlineCommand(
            pattern=re.compile(r'^wiki\s+([a-z]{2})\s+(.+)$'),
            handler=handle_wikipedia_search,
            name=t('wikipedia_search'),
        ),
    }
