import logging
from contextlib import suppress
from typing import ClassVar
from urllib import parse

import regex as re
import wikipedia
from aiohttp import ClientSession
from search_engine_parser.core.base import SearchResult
from search_engine_parser.core.engines.duckduckgo import Search as DuckDuckGoSearch
from telethon import events
from telethon.errors import QueryIdInvalidError

from src.modules.base import InlineModuleBase
from src.utils.command import InlineCommand
from src.utils.quran import surah_names

ddg_search = DuckDuckGoSearch()


async def handle_duckduckgo_search(event: events.InlineQuery.Event) -> None:
    query = event.text[4:].strip()
    if not query:
        return

    try:
        results: SearchResult = await ddg_search.async_search(query, 1)
    except Exception as e:  # noqa: BLE001
        logging.error(f'Error in DuckDuckGo search: {e}')
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
        logging.error(f'Error in Wikipedia search: {e}')
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
    async with ClientSession() as session:
        try:
            async with session.get(f'https://api.quran.com/api/v4/search?q={query}') as response:
                if response.status == 200:
                    data = await response.json()
                    results = data['search']['results']
                else:
                    logging.error(f'Error in Quran search: HTTP {response.status}')
                    return
        except Exception as e:  # noqa: BLE001
            logging.error(f'Error in Quran search: {e}')
            return

    inline_results = []
    for result in results:
        surah, aya = map(int, result['verse_key'].split(':'))
        title = f'سورة {surah_names[surah]} ({aya})'
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


class WebSearch(InlineModuleBase):
    name = 'Web Search'
    description = 'Search the web using search engines'
    inline_commands: ClassVar[InlineModuleBase.InlineCommandsT] = {
        'ddg': InlineCommand(
            pattern=re.compile(r'^ddg\s+(.+)$'),
            handler=handle_duckduckgo_search,
            name='DuckDuckGo Search',
        ),
        'quran': InlineCommand(
            pattern=re.compile(r'^quran\s+(.+)$'),
            handler=handle_quran_search,
            name='Quran Search',
        ),
        'wiki': InlineCommand(
            pattern=re.compile(r'^wiki\s+([a-z]{2})\s+(.+)$'),
            handler=handle_wikipedia_search,
            name='Wikipedia Search',
        ),
    }
